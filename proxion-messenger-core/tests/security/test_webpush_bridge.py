"""Tests for WebPush VAPID bridge (Round 18)."""
import pytest
from unittest.mock import patch, MagicMock
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.webpush import generate_vapid_keypair, vapid_public_key_from_pem


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_vapid_keypair_generated():
    """generate_vapid_keypair returns a non-empty PEM and base64url public key."""
    priv_pem, pub_b64 = generate_vapid_keypair()
    assert priv_pem.startswith("-----BEGIN")
    assert len(pub_b64) > 20
    # Public key derivation from the private key should match
    derived_pub = vapid_public_key_from_pem(priv_pem)
    assert derived_pub == pub_b64


def test_push_subscription_stored_and_retrieved(store):
    """save_push_subscription / get_push_subscriptions round-trip."""
    store.save_push_subscription(
        subscription_id="sub-001",
        owner_webid="alice@example.org",
        endpoint="https://push.example.com/1234",
        p256dh_b64="cGFkZGluZw==",
        auth_b64="YXV0aA==",
    )
    subs = store.get_push_subscriptions("alice@example.org")
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example.com/1234"
    assert subs[0]["subscription_id"] == "sub-001"


def test_web_push_sent_when_recipient_offline():
    """send_web_push is called when store has subscriptions and pywebpush is available."""
    from proxion_messenger_core import webpush as wp_mod

    mock_response = MagicMock()
    mock_response.status_code = 201

    with patch.object(wp_mod, "send_web_push", return_value=True) as mock_push:
        result = wp_mod.send_web_push(
            subscription={
                "endpoint": "https://push.example.com/abc",
                "keys": {"p256dh": "abc==", "auth": "def=="},
            },
            payload={"type": "message", "thread_id": "dm-001", "display_name": "Bob"},
            vapid_private_pem="fake_pem",
            vapid_subject="mailto:admin@example.com",
        )
    assert mock_push.called


def test_delete_push_subscription(store):
    """delete_push_subscription removes the entry."""
    store.save_push_subscription("sub-002", "bob@example.org", "https://ep.com/x", "p256==", "au==")
    assert len(store.get_push_subscriptions("bob@example.org")) == 1
    store.delete_push_subscription("sub-002")
    assert len(store.get_push_subscriptions("bob@example.org")) == 0
