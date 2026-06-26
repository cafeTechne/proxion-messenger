"""Unit tests for STUN Binding Request/Response codec — no network required."""
import struct

import pytest

from proxion_messenger_core.stun_client import (
    STUN_MAGIC_COOKIE,
    build_binding_request,
    parse_binding_response,
)

_MAGIC_BYTES = struct.pack("!I", STUN_MAGIC_COOKIE)


def test_build_binding_request_length():
    packet, txn_id = build_binding_request()
    assert len(packet) == 20
    assert len(txn_id) == 12


def test_build_binding_request_message_type():
    packet, _ = build_binding_request()
    msg_type = struct.unpack_from("!H", packet, 0)[0]
    assert msg_type == 0x0001


def test_build_binding_request_magic_cookie():
    packet, _ = build_binding_request()
    magic = struct.unpack_from("!I", packet, 4)[0]
    assert magic == STUN_MAGIC_COOKIE


def test_build_binding_request_txn_id_in_packet():
    packet, txn_id = build_binding_request()
    assert packet[8:20] == txn_id


def test_build_binding_request_unique_txn_ids():
    _, txn1 = build_binding_request()
    _, txn2 = build_binding_request()
    assert txn1 != txn2


def _build_response(txn_id: bytes, external_ip: str, external_port: int) -> bytes:
    """Construct a minimal STUN Binding Success Response with XOR-MAPPED-ADDRESS."""
    import socket as _socket

    xor_port = external_port ^ (STUN_MAGIC_COOKIE >> 16)
    ip_int = struct.unpack("!I", _socket.inet_aton(external_ip))[0]
    xor_ip = ip_int ^ STUN_MAGIC_COOKIE

    # XOR-MAPPED-ADDRESS attribute (type=0x0020, len=8)
    attr = struct.pack("!HHBBHI", 0x0020, 8, 0x00, 0x01, xor_port, xor_ip)
    msg_len = len(attr)
    header = struct.pack("!HHI12s", 0x0101, msg_len, STUN_MAGIC_COOKIE, txn_id)
    return header + attr


def test_parse_binding_response_xor_mapped_address():
    _, txn_id = build_binding_request()
    response = _build_response(txn_id, "203.0.113.42", 54321)
    result = parse_binding_response(response, txn_id)
    assert result is not None
    ip, port = result
    assert ip == "203.0.113.42"
    assert port == 54321


def test_parse_binding_response_wrong_txn_id():
    _, txn_id = build_binding_request()
    _, other_txn = build_binding_request()
    response = _build_response(txn_id, "10.0.0.1", 1234)
    result = parse_binding_response(response, other_txn)
    assert result is None


def test_parse_binding_response_too_short():
    _, txn_id = build_binding_request()
    result = parse_binding_response(b"\x00" * 10, txn_id)
    assert result is None


def test_parse_binding_response_wrong_message_type():
    _, txn_id = build_binding_request()
    # Use type 0x0001 (request) instead of 0x0101 (success response)
    import socket as _socket
    xor_port = 12345 ^ (STUN_MAGIC_COOKIE >> 16)
    xor_ip = struct.unpack("!I", _socket.inet_aton("1.2.3.4"))[0] ^ STUN_MAGIC_COOKIE
    attr = struct.pack("!HHBBHI", 0x0020, 8, 0x00, 0x01, xor_port, xor_ip)
    header = struct.pack("!HHI12s", 0x0001, len(attr), STUN_MAGIC_COOKIE, txn_id)
    result = parse_binding_response(header + attr, txn_id)
    assert result is None


def test_parse_binding_response_wrong_magic_cookie():
    _, txn_id = build_binding_request()
    bad_magic = 0xDEADBEEF
    import socket as _socket
    xor_port = 12345 ^ (bad_magic >> 16)
    xor_ip = struct.unpack("!I", _socket.inet_aton("1.2.3.4"))[0] ^ bad_magic
    attr = struct.pack("!HHBBHI", 0x0020, 8, 0x00, 0x01, xor_port, xor_ip)
    header = struct.pack("!HHI12s", 0x0101, len(attr), bad_magic, txn_id)
    result = parse_binding_response(header + attr, txn_id)
    assert result is None
