"""Tests for authn adapter contracts (Python-side auth bridge mode)."""
import os
import pytest
from unittest.mock import patch, MagicMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.css_auth import CssClientCredentials, _BridgeTransportError, _is_auth_security_error


@pytest.fixture
def creds():
    key = Ed25519PrivateKey.generate()
    return CssClientCredentials(
        css_base_url="https://pod.example",
        client_id="cid",
        client_secret="secret",
        identity_key=key,
    )


class TestNodeAuthnAdapterMapsSuccessAndFailureCodes:
    def test_bridge_transport_error_is_raised_by_stub(self, creds):
        """_fetch_via_bridge raises _BridgeTransportError (stub behaviour)."""
        with pytest.raises(_BridgeTransportError):
            creds._fetch_via_bridge("pod_rw")

    def test_legacy_mode_never_calls_bridge(self, creds):
        """In legacy mode, fetch_access_token never tries the bridge."""
        r200 = MagicMock()
        r200.status_code = 200
        r200.headers = {}
        r200.raise_for_status = MagicMock()
        r200.json = MagicMock(return_value={"access_token": "tok", "expires_in": 3600})

        with patch.dict(os.environ, {"PROXION_SOLID_AUTH_MODE": "legacy"}):
            with patch("httpx.post", return_value=r200) as mock_post:
                tok, _ = creds.fetch_access_token()
        assert tok == "tok"
        assert mock_post.call_count == 1  # direct POST, no bridge attempt

    def test_inrupt_bridge_mode_raises_when_bridge_unavailable(self, creds):
        """inrupt_bridge mode surfaces _BridgeTransportError directly."""
        with patch.dict(os.environ, {"PROXION_SOLID_AUTH_MODE": "inrupt_bridge"}):
            with pytest.raises(_BridgeTransportError):
                creds.fetch_access_token()


class TestBrowserAuthnAdapterEnforcesStateValidation:
    def test_is_auth_security_error_flags_nonce_keyword(self):
        """_is_auth_security_error detects nonce-related messages."""
        err = Exception("use_dpop_nonce required")
        assert _is_auth_security_error(err)

    def test_is_auth_security_error_flags_signature(self):
        assert _is_auth_security_error(Exception("invalid signature"))

    def test_is_auth_security_error_flags_401(self):
        assert _is_auth_security_error(Exception("401 unauthorized"))

    def test_is_auth_security_error_does_not_flag_transport(self):
        assert not _is_auth_security_error(Exception("connection refused"))


class TestAutoModeFallbackDisallowedForNonceSignatureFailures:
    def test_auto_mode_falls_back_on_transport_error(self, creds):
        """auto mode falls back to legacy when bridge raises _BridgeTransportError."""
        r200 = MagicMock()
        r200.status_code = 200
        r200.headers = {}
        r200.raise_for_status = MagicMock()
        r200.json = MagicMock(return_value={"access_token": "tok", "expires_in": 3600})

        with patch.dict(os.environ, {"PROXION_SOLID_AUTH_MODE": "auto"}):
            with patch("httpx.post", return_value=r200) as mock_post:
                tok, _ = creds.fetch_access_token()
        assert tok == "tok"
        assert mock_post.call_count == 1  # legacy fallback succeeded

    def test_auto_mode_does_not_suppress_security_errors(self, creds):
        """auto mode re-raises security auth failures rather than falling back."""
        def _bad_bridge(_scope):
            raise Exception("401 use_dpop_nonce")

        with patch.dict(os.environ, {"PROXION_SOLID_AUTH_MODE": "auto"}):
            with patch.object(creds, "_fetch_via_bridge", side_effect=_bad_bridge):
                with pytest.raises(Exception, match="401 use_dpop_nonce"):
                    creds.fetch_access_token()
