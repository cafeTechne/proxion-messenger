"""Minimal RFC 5389 STUN client for external endpoint discovery.

Only implements the Binding Request / Binding Response exchange over UDP.
No OS-level WireGuard, TUN, or kernel dependencies. Used to discover the
local device's external IP and port behind a NAT so that UDP hole punching
can be attempted via the existing sealed DM signalling channel.

Packet layout (RFC 5389 §6):

  0                   1                   2                   3
  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |0 0|  STUN Message Type       |         Message Length         |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |                     Magic Cookie = 0x2112A442                 |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |                                                               |
  |                     Transaction ID (96 bits)                  |
  |                                                               |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
"""
from __future__ import annotations

import asyncio
import os
import socket
import struct

STUN_MAGIC_COOKIE: int = 0x2112A442
_BINDING_REQUEST: int = 0x0001
_BINDING_RESPONSE_SUCCESS: int = 0x0101
_ATTR_MAPPED_ADDRESS: int = 0x0001
_ATTR_XOR_MAPPED_ADDRESS: int = 0x0020
_HEADER_LEN: int = 20


def validate_stun_endpoint(ip: str, port: int) -> tuple[bool, str]:
    """Validate a candidate external endpoint before using it for hole punching.

    Returns
    -------
    (valid, reason)
        *valid* is True when the endpoint is acceptable.
        *reason* is an empty string on success or a human-readable rejection reason.
    """
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False, f"invalid IP address: {ip!r}"
    if addr.is_loopback:
        return False, "loopback address rejected"
    if addr.is_multicast:
        return False, "multicast address rejected"
    if addr.is_link_local:
        return False, "link-local address rejected"
    if addr.is_unspecified:
        return False, "unspecified (0.0.0.0) address rejected"
    if str(addr) == "255.255.255.255":
        return False, "broadcast address rejected"
    if not (1 <= port <= 65535):
        return False, f"port {port} out of valid range [1, 65535]"
    return True, ""


class StunError(Exception):
    """Raised when STUN discovery fails."""


def build_binding_request() -> tuple[bytes, bytes]:
    """Build a STUN Binding Request packet.

    Returns
    -------
    (packet, transaction_id)
        *packet* is ready to send; *transaction_id* is needed to verify
        the response.
    """
    txn_id = os.urandom(12)
    header = struct.pack(
        "!HHI12s",
        _BINDING_REQUEST,
        0,  # message length (no attributes)
        STUN_MAGIC_COOKIE,
        txn_id,
    )
    return header, txn_id


def parse_binding_response(data: bytes, transaction_id: bytes) -> tuple[str, int] | None:
    """Parse a STUN Binding Response and return (external_ip, external_port).

    Returns None if the response is not a valid success response for the
    given *transaction_id* or if no address attribute is found.
    """
    if len(data) < _HEADER_LEN:
        return None
    msg_type, msg_len, magic, rxn_id = struct.unpack_from("!HHI12s", data, 0)
    if msg_type != _BINDING_RESPONSE_SUCCESS:
        return None
    if magic != STUN_MAGIC_COOKIE:
        return None
    if rxn_id != transaction_id:
        return None

    offset = _HEADER_LEN
    end = _HEADER_LEN + msg_len
    xor_result: tuple[str, int] | None = None
    mapped_result: tuple[str, int] | None = None

    while offset + 4 <= min(end, len(data)):
        attr_type, attr_len = struct.unpack_from("!HH", data, offset)
        offset += 4
        attr_data = data[offset : offset + attr_len]
        offset += attr_len + (4 - attr_len % 4) % 4  # pad to 4-byte boundary

        if attr_type == _ATTR_XOR_MAPPED_ADDRESS and len(attr_data) >= 8:
            family = attr_data[1]
            if family == 0x01:  # IPv4
                xor_port = struct.unpack_from("!H", attr_data, 2)[0] ^ (STUN_MAGIC_COOKIE >> 16)
                xor_addr = struct.unpack_from("!I", attr_data, 4)[0] ^ STUN_MAGIC_COOKIE
                ip = socket.inet_ntoa(struct.pack("!I", xor_addr))
                xor_result = (ip, xor_port)

        elif attr_type == _ATTR_MAPPED_ADDRESS and len(attr_data) >= 8:
            family = attr_data[1]
            if family == 0x01:  # IPv4
                port = struct.unpack_from("!H", attr_data, 2)[0]
                addr = struct.unpack_from("!I", attr_data, 4)[0]
                ip = socket.inet_ntoa(struct.pack("!I", addr))
                mapped_result = (ip, port)

    return xor_result or mapped_result


async def discover_external_endpoint(
    stun_host: str = "stun.l.google.com",
    stun_port: int = 19302,
    timeout: float = 3.0,
) -> tuple[str, int]:
    """Discover external IP:port by sending a STUN Binding Request over UDP.

    Raises
    ------
    StunError
        If no valid response is received within *timeout* seconds.
    """
    packet, txn_id = build_binding_request()
    loop = asyncio.get_event_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        # Resolve once (synchronously in thread pool to avoid blocking the loop)
        addrs = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(stun_host, stun_port, socket.AF_INET, socket.SOCK_DGRAM)
        )
        if not addrs:
            raise StunError(f"Could not resolve STUN host: {stun_host}")
        server_addr = addrs[0][4]

        await loop.sock_sendto(sock, packet, server_addr)

        end_time = loop.time() + timeout
        while loop.time() < end_time:
            remaining = end_time - loop.time()
            try:
                data, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 1024), timeout=remaining)
            except asyncio.TimeoutError:
                break
            result = parse_binding_response(data, txn_id)
            if result is not None:
                return result
    except StunError:
        raise
    except Exception as exc:
        raise StunError(f"STUN discovery failed: {exc}") from exc
    finally:
        sock.close()

    raise StunError(f"No STUN response from {stun_host}:{stun_port} within {timeout}s")
