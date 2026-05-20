"""Tests for skipped message key cache in the double ratchet (Round 19)."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
import base64

from proxion_messenger_core.e2e_session import (
    SessionState,
    MAX_SKIP,
    encrypt_session_message,
    decrypt_session_message,
    init_outbound_session,
    init_inbound_session,
    generate_prekey_bundle,
    session_to_dict,
    session_from_dict,
)


def _ed_to_x25519_pub(ed_key):
    raw = ed_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return X25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _bootstrap():
    alice_key = Ed25519PrivateKey.generate()
    bob_key = Ed25519PrivateKey.generate()
    bob_bundle = generate_prekey_bundle("bob@example.org")
    alice_state, header = init_outbound_session(
        alice_identity_key=alice_key,
        alice_webid="alice@example.org",
        bob_webid="bob@example.org",
        bob_identity_pub_bytes=_ed_to_x25519_pub(bob_key),
        bob_signed_prekey_pub_b64=bob_bundle["signed_prekey_pub_b64"],
    )
    bob_state = init_inbound_session(
        bob_identity_key=bob_key,
        bob_webid="bob@example.org",
        alice_webid="alice@example.org",
        alice_identity_pub_bytes=_ed_to_x25519_pub(alice_key),
        header=header,
        signed_prekey_priv_bytes=base64.b64decode(bob_bundle["signed_prekey_priv_b64"]),
    )
    return alice_state, bob_state


def test_out_of_order_message_decrypts_from_skipped_cache():
    """Messages arriving out-of-order should decrypt using the skipped key cache."""
    alice_state, bob_state = _bootstrap()

    # Alice sends three messages in order
    alice_state, p0 = encrypt_session_message(alice_state, "msg0")
    alice_state, p1 = encrypt_session_message(alice_state, "msg1")
    alice_state, p2 = encrypt_session_message(alice_state, "msg2")

    # Bob receives msg1 first (out of order) — msg0 key should be cached
    bob_state, pt1 = decrypt_session_message(bob_state, p1)
    assert pt1 == "msg1"
    assert bob_state.recv_count == 2  # skipped 0, processed 1

    # msg0's key must now be in the cache
    cache_key = f"{p0.get('ratchet_pub_b64', '')}:0"
    assert cache_key in bob_state.skipped_keys

    # Bob receives msg0 — must decrypt from cache
    bob_state, pt0 = decrypt_session_message(bob_state, p0)
    assert pt0 == "msg0"
    assert cache_key not in bob_state.skipped_keys  # key was consumed

    # Bob receives msg2 normally
    bob_state, pt2 = decrypt_session_message(bob_state, p2)
    assert pt2 == "msg2"


def test_max_skip_limit_raises_on_large_gap():
    """Gaps larger than MAX_SKIP must raise ValueError."""
    alice_state, bob_state = _bootstrap()

    # Alice sends MAX_SKIP + 2 messages; Bob only receives the last one
    payloads = []
    for i in range(MAX_SKIP + 2):
        alice_state, p = encrypt_session_message(alice_state, f"msg{i}")
        payloads.append(p)

    # Bob tries to decrypt the last message — gap = MAX_SKIP + 1 > MAX_SKIP
    with pytest.raises(ValueError, match="MAX_SKIP exceeded"):
        decrypt_session_message(bob_state, payloads[-1])


def test_skipped_keys_cleared_after_use():
    """Using a skipped key removes it from the cache."""
    alice_state, bob_state = _bootstrap()

    alice_state, p0 = encrypt_session_message(alice_state, "first")
    alice_state, p1 = encrypt_session_message(alice_state, "second")

    # Bob receives p1 first — caches key for p0
    bob_state, _ = decrypt_session_message(bob_state, p1)
    assert len(bob_state.skipped_keys) == 1

    # Bob receives p0 — cache entry is consumed
    bob_state, pt = decrypt_session_message(bob_state, p0)
    assert pt == "first"
    assert len(bob_state.skipped_keys) == 0


def test_skipped_keys_survive_session_serialization():
    """skipped_keys must round-trip through session_to_dict / session_from_dict."""
    alice_state, bob_state = _bootstrap()

    alice_state, p0 = encrypt_session_message(alice_state, "skip_me")
    alice_state, p1 = encrypt_session_message(alice_state, "deliver_first")

    # Bob receives p1, caching p0's key
    bob_state, _ = decrypt_session_message(bob_state, p1)
    assert len(bob_state.skipped_keys) == 1

    # Serialize / deserialize
    restored = session_from_dict(session_to_dict(bob_state))
    assert restored.skipped_keys == bob_state.skipped_keys

    # The restored state should still be able to decrypt p0
    restored, pt = decrypt_session_message(restored, p0)
    assert pt == "skip_me"
