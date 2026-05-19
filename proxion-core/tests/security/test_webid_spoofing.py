"""Tests for relay message spoofing via HTTPS WebIDs."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.relay import sign_relay_message, verify_relay_message


def _fresh_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _priv_to_did(priv: Ed25519PrivateKey) -> str:
    from proxion_messenger_core.didkey import pub_key_to_did
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _pub_hex(priv: Ed25519PrivateKey) -> str:
    return priv.public_key().public_bytes_raw().hex()


_TO_WEBID = "did:key:z6MkHJzCkGz4CzDfW3FGsGZpMvHFp3JsBGFn2TEm7fGkMRvs@https://other.example.com"


def _sign(priv: Ed25519PrivateKey, from_webid: str) -> tuple[str, str, str, str]:
    ts = datetime.now(timezone.utc).isoformat()
    msg_id = "test-msg-001"
    content = "hello"
    sig = sign_relay_message(priv, from_webid, _TO_WEBID, msg_id, content, ts)
    return msg_id, content, ts, sig


# Patch target: lazy import inside verify_relay_message resolves to webid_verify module
_WV_PATCH = "proxion_messenger_core.webid_verify.get_webid_pub_hex"


class TestDIDKeyRelayVerification:
    def test_valid_did_key_signature_accepted(self):
        priv = _fresh_key()
        did = _priv_to_did(priv)
        msg_id, content, ts, sig = _sign(priv, did)
        assert verify_relay_message(did, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))

    def test_wrong_key_did_rejected(self):
        priv_a = _fresh_key()
        priv_b = _fresh_key()
        did_a = _priv_to_did(priv_a)
        msg_id, content, ts, sig = _sign(priv_b, did_a)
        assert not verify_relay_message(did_a, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))


class TestHTTPSWebIDRelayVerification:
    def test_https_webid_accepted_when_key_matches(self):
        priv = _fresh_key()
        webid = "https://pod.example.com/alice/profile/card#me"
        msg_id, content, ts, sig = _sign(priv, webid)
        with patch(_WV_PATCH, return_value=_pub_hex(priv)):
            result = verify_relay_message(webid, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))
        assert result is True

    def test_https_webid_rejected_when_key_mismatch(self):
        priv_signer = _fresh_key()
        priv_victim = _fresh_key()
        webid = "https://pod.example.com/alice/profile/card#me"
        msg_id, content, ts, sig = _sign(priv_signer, webid)
        with patch(_WV_PATCH, return_value=_pub_hex(priv_victim)):
            result = verify_relay_message(webid, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))
        assert result is False

    def test_https_webid_rejected_when_profile_unreachable(self):
        priv = _fresh_key()
        webid = "https://pod.example.com/alice/profile/card#me"
        msg_id, content, ts, sig = _sign(priv, webid)
        with patch(_WV_PATCH, return_value=None):
            result = verify_relay_message(webid, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))
        assert result is False

    def test_http_webid_also_resolved_via_webid_verify(self):
        priv = _fresh_key()
        webid = "http://localhost:3000/alice/profile/card#me"
        msg_id, content, ts, sig = _sign(priv, webid)
        with patch(_WV_PATCH, return_value=_pub_hex(priv)):
            result = verify_relay_message(webid, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))
        assert result is True

    def test_tampered_content_rejected(self):
        priv = _fresh_key()
        webid = "https://pod.example.com/alice/profile/card#me"
        msg_id, content, ts, sig = _sign(priv, webid)
        with patch(_WV_PATCH, return_value=_pub_hex(priv)):
            result = verify_relay_message(webid, _TO_WEBID, msg_id, "TAMPERED", ts, sig, clock_skew_window=timedelta(minutes=5))
        assert result is False

    def test_get_webid_pub_hex_called_once_for_https(self):
        priv = _fresh_key()
        webid = "https://pod.example.com/alice/profile/card#me"
        msg_id, content, ts, sig = _sign(priv, webid)
        with patch(_WV_PATCH, return_value=_pub_hex(priv)) as mock_get:
            verify_relay_message(webid, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))
        mock_get.assert_called_once_with(webid)

    def test_did_key_does_not_call_webid_verify(self):
        priv = _fresh_key()
        did = _priv_to_did(priv)
        msg_id, content, ts, sig = _sign(priv, did)
        with patch(_WV_PATCH) as mock_get:
            verify_relay_message(did, _TO_WEBID, msg_id, content, ts, sig, clock_skew_window=timedelta(minutes=5))
        mock_get.assert_not_called()
