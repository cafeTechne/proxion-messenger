"""Contact verification via safety numbers.

Safety numbers let two users verify out-of-band that they share the same view
of each other's identity keys — closing the MITM gap against a compromised
or subpoenaed gateway operator.

Algorithm
---------
fingerprint_bytes = SHA-512(
    len(stable_id_A) || stable_id_A || identity_pub_A_raw ||
    len(stable_id_B) || identity_pub_B_raw
)

where ``stable_id`` is the UTF-8-encoded WebID string and ``identity_pub`` is
the raw 32-byte Ed25519 or X25519 public key.  The comparison is symmetric:
parties must sort by WebID lexicographically before hashing so both sides
produce the same fingerprint regardless of who initiates the check.

The 64-byte hash is folded to 60 decimal digits (12 groups of 5, Signal-style)
by interpreting each 5-byte chunk as a big-endian integer modulo 100 000.
"""
from __future__ import annotations

import hashlib


def _stable_order(
    webid_a: str,
    pub_a: bytes,
    webid_b: str,
    pub_b: bytes,
) -> tuple[str, bytes, str, bytes]:
    """Return (webid1, pub1, webid2, pub2) in stable lexicographic order."""
    if webid_a <= webid_b:
        return webid_a, pub_a, webid_b, pub_b
    return webid_b, pub_b, webid_a, pub_a


def _hash_identity_pair(
    webid1: str, pub1: bytes, webid2: str, pub2: bytes
) -> bytes:
    """Return SHA-512 over the sorted, length-prefixed identity pair."""
    id1 = webid1.encode("utf-8")
    id2 = webid2.encode("utf-8")
    h = hashlib.sha512()
    h.update(len(id1).to_bytes(4, "big"))
    h.update(id1)
    h.update(pub1)
    h.update(len(id2).to_bytes(4, "big"))
    h.update(id2)
    h.update(pub2)
    return h.digest()


def _bytes_to_decimal(digest: bytes, groups: int = 12, digits_per_group: int = 5) -> str:
    """Convert digest bytes to a fixed-width decimal string.

    Each 5-byte chunk → big-endian uint40 mod 100_000 → zero-padded 5-digit string.
    """
    modulus = 10 ** digits_per_group
    parts: list[str] = []
    for i in range(groups):
        chunk = digest[i * 5 : i * 5 + 5]
        value = int.from_bytes(chunk, "big") % modulus
        parts.append(f"{value:0{digits_per_group}d}")
    return "".join(parts)


def compute_safety_numbers(
    my_webid: str,
    my_pub_bytes: bytes,
    peer_webid: str,
    peer_pub_bytes: bytes,
) -> str:
    """Return a 60-digit safety-number string for this identity pair.

    The result is the same regardless of which party calls this function,
    as long as both use the same WebIDs and public key bytes.
    """
    w1, p1, w2, p2 = _stable_order(my_webid, my_pub_bytes, peer_webid, peer_pub_bytes)
    digest = _hash_identity_pair(w1, p1, w2, p2)
    return _bytes_to_decimal(digest)


def format_safety_numbers(raw: str) -> list[str]:
    """Split a 60-digit raw safety number into 12 groups of 5 for display."""
    return [raw[i : i + 5] for i in range(0, len(raw), 5)]


def verify_safety_numbers(
    expected: str,
    my_webid: str,
    my_pub_bytes: bytes,
    peer_webid: str,
    peer_pub_bytes: bytes,
) -> bool:
    """Return True iff *expected* matches the computed safety numbers for this pair."""
    computed = compute_safety_numbers(my_webid, my_pub_bytes, peer_webid, peer_pub_bytes)
    return expected == computed
