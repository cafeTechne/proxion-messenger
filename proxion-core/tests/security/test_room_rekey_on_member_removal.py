"""R17: Room re-key on member removal."""
import base64
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from proxion_messenger_core.room_rekey import (
    generate_room_key,
    seal_room_key_for_member,
    unseal_room_key,
    rotate_room_key,
    build_room_key_update_event,
)


def _x25519_keypair():
    priv = X25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return priv_bytes, pub


def test_kick_member_triggers_room_rekey():
    result = rotate_room_key("room-001", ["did:key:alice", "did:key:bob"])
    assert result["room_id"] == "room-001"
    assert "new_key_b64" in result
    raw = base64.b64decode(result["new_key_b64"])
    assert len(raw) == 32
    assert "sealed_keys" in result
    assert "rekey_event_id" in result


def test_departed_member_cannot_decrypt_post_rekey_messages():
    departed_priv, departed_pub = _x25519_keypair()
    remaining_priv, remaining_pub = _x25519_keypair()

    new_room_key = generate_room_key()
    sealed_for_remaining = seal_room_key_for_member(new_room_key, remaining_pub)

    recovered = unseal_room_key(sealed_for_remaining, remaining_priv)
    assert recovered == new_room_key

    with pytest.raises(Exception):
        unseal_room_key(sealed_for_remaining, departed_priv)


def test_remaining_members_receive_new_room_key_event():
    result = rotate_room_key("room-002", ["did:key:alice"])
    event = build_room_key_update_event(result)
    assert event["type"] == "room_key_update"
    assert event["room_id"] == "room-002"
    assert "sealed_keys" in event
    assert "rekey_event_id" in event
