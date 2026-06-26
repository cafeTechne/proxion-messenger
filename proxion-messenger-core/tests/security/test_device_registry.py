"""Tests for device key registration (Round 19)."""
import time
import pytest
from proxion_messenger_core.device_registry import (
    generate_device_key,
    sign_device_attestation,
    verify_device_attestation,
)
from proxion_messenger_core.local_store import LocalStore
import base64


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_device_registration_stores_and_retrieves(store):
    """register_device persists the record; get_device retrieves it."""
    dk = generate_device_key()
    owner = "alice@example.org"
    ts = time.time()
    sig = sign_device_attestation(
        base64.b64decode(dk["priv_b64"]), owner, dk["device_id"], ts
    )
    store.register_device(dk["device_id"], owner, dk["pub_b64"], sig)

    result = store.get_device(dk["device_id"])
    assert result is not None
    assert result["device_id"] == dk["device_id"]
    assert result["owner_webid"] == owner
    assert result["device_pub_b64"] == dk["pub_b64"]


def test_device_attestation_signature_verified():
    """verify_device_attestation returns True for valid sig, False for tampered."""
    dk = generate_device_key()
    owner = "bob@example.org"
    ts = time.time()
    sig = sign_device_attestation(base64.b64decode(dk["priv_b64"]), owner, dk["device_id"], ts)

    assert verify_device_attestation(dk["pub_b64"], owner, dk["device_id"], ts, sig)
    # Wrong owner
    assert not verify_device_attestation(dk["pub_b64"], "evil@example.org", dk["device_id"], ts, sig)
    # Tampered sig
    tampered = base64.b64encode(b"\x00" * 64).decode()
    assert not verify_device_attestation(dk["pub_b64"], owner, dk["device_id"], ts, tampered)


def test_list_devices_returns_owner_devices_only(store):
    """list_devices returns only devices belonging to the given owner."""
    alice = "alice@example.org"
    bob = "bob@example.org"

    for owner in (alice, bob, alice):
        dk = generate_device_key()
        ts = time.time()
        sig = sign_device_attestation(base64.b64decode(dk["priv_b64"]), owner, dk["device_id"], ts)
        store.register_device(dk["device_id"], owner, dk["pub_b64"], sig)

    alice_devices = store.list_devices(alice)
    bob_devices = store.list_devices(bob)

    assert len(alice_devices) == 2
    assert len(bob_devices) == 1
    assert all(d["owner_webid"] == alice for d in alice_devices)


def test_unregister_removes_device(store):
    """unregister_device deletes the device record."""
    owner = "carol@example.org"
    dk = generate_device_key()
    ts = time.time()
    sig = sign_device_attestation(base64.b64decode(dk["priv_b64"]), owner, dk["device_id"], ts)
    store.register_device(dk["device_id"], owner, dk["pub_b64"], sig)

    assert store.get_device(dk["device_id"]) is not None
    store.unregister_device(dk["device_id"])
    assert store.get_device(dk["device_id"]) is None
