"""Unit tests for room-descriptor signing/verification (PLAN_ROUND_71 B3)."""
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.room_descriptor import canonical_bytes, verify_descriptor
from proxion_messenger_core.didkey import pub_key_to_did


def _did(key):
    return pub_key_to_did(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def _sign(desc, key, did):
    sig = key.sign(canonical_bytes(desc))
    return {**desc, "px:signer": did, "px:sig": base64.b64encode(sig).decode()}


def _desc():
    return {
        "room_id": "room-x",
        "owner": "https://me.pod/#me",
        "created": "2026-07-26T00:00:00Z",
        "members": [{"webid": "https://me.pod/#me", "role": "owner"}],
        "long_chat": "https://me.pod/proxion/rooms/room-x/",
    }


def test_valid_signature_returns_signer():
    k = Ed25519PrivateKey.generate()
    did = _did(k)
    assert verify_descriptor(_sign(_desc(), k, did)) == did


def test_unsigned_returns_none():
    assert verify_descriptor(_desc()) is None
    assert verify_descriptor({}) is None
    assert verify_descriptor(None) is None


def test_tampered_membership_returns_none():
    k = Ed25519PrivateKey.generate()
    did = _did(k)
    s = _sign(_desc(), k, did)
    s["members"] = list(s["members"]) + [{"webid": "https://evil.pod/#me", "role": "admin"}]
    assert verify_descriptor(s) is None


def test_long_chat_is_not_covered_by_the_signature():
    # long_chat is filled server-side after signing, so changing it must NOT break.
    k = Ed25519PrivateKey.generate()
    did = _did(k)
    s = _sign(_desc(), k, did)
    s["long_chat"] = "https://me.pod/proxion/rooms/DIFFERENT/"
    assert verify_descriptor(s) == did


def test_claimed_signer_must_match_the_signing_key():
    k = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    s = _sign(_desc(), k, _did(k))
    s["px:signer"] = _did(other)   # claim a different signer than actually signed with
    assert verify_descriptor(s) is None
