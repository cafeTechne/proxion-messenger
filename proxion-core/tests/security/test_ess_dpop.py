"""Tests for DPoP nonce support (RFC 9449) and ESS 401-nonce-retry."""
import base64
import json
import pytest
from unittest.mock import MagicMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.dpop import make_dpop_proof, _extract_dpop_nonce


def _decode_payload(jwt_str: str) -> dict:
    parts = jwt_str.split(".")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _make_key():
    return Ed25519PrivateKey.generate()


# ---------------------------------------------------------------------------
# make_dpop_proof — nonce claim
# ---------------------------------------------------------------------------

class TestMakeDpopProofNonce:
    def test_without_nonce_has_no_nonce_claim(self):
        key = _make_key()
        proof = make_dpop_proof(key, "GET", "https://pod.example/resource")
        payload = _decode_payload(proof)
        assert "nonce" not in payload

    def test_with_nonce_includes_claim(self):
        key = _make_key()
        proof = make_dpop_proof(key, "GET", "https://pod.example/resource", nonce="abc123")
        payload = _decode_payload(proof)
        assert payload["nonce"] == "abc123"

    def test_nonce_none_does_not_add_claim(self):
        key = _make_key()
        proof = make_dpop_proof(key, "GET", "https://pod.example/resource", nonce=None)
        payload = _decode_payload(proof)
        assert "nonce" not in payload

    def test_standard_claims_still_present_with_nonce(self):
        key = _make_key()
        proof = make_dpop_proof(key, "POST", "https://pod.example/token", nonce="xyz")
        payload = _decode_payload(proof)
        assert payload["htm"] == "POST"
        assert "jti" in payload
        assert "iat" in payload
        assert "exp" in payload


# ---------------------------------------------------------------------------
# _extract_dpop_nonce
# ---------------------------------------------------------------------------

class TestExtractDpopNonce:
    def test_extracts_nonce_from_www_authenticate(self):
        header = 'DPoP error="use_dpop_nonce", nonce="server-nonce-abc"'
        assert _extract_dpop_nonce(header) == "server-nonce-abc"

    def test_returns_none_when_absent(self):
        assert _extract_dpop_nonce("Bearer realm=example") is None

    def test_returns_none_on_empty_string(self):
        assert _extract_dpop_nonce("") is None

    def test_case_insensitive_nonce_key(self):
        header = 'DPoP error="use_dpop_nonce", Nonce="MyNonce"'
        assert _extract_dpop_nonce(header) == "MyNonce"


# ---------------------------------------------------------------------------
# DpopSolidClient — 401 nonce retry and nonce caching
# ---------------------------------------------------------------------------

def _make_dpop_client(session):
    from proxion_messenger_core.css_auth import DpopSolidClient
    from proxion_messenger_core.dpop import generate_ec_dpop_key
    from proxion_messenger_core.solid import SolidResolver
    resolver = SolidResolver("https://pod.example")
    key = _make_key()
    # Mock credentials so get_token() never makes real HTTP calls (even after _refresh_auth)
    creds = MagicMock()
    creds.get_token = MagicMock(return_value="fake-access-token")
    creds.identity_key = key
    creds._dpop_ec_key = generate_ec_dpop_key()
    client = DpopSolidClient(resolver, creds, session=session)
    return client


class TestDpopSolidClientNonce:
    def test_nonce_stored_after_401_response(self):
        mock_session = MagicMock()
        r401 = MagicMock()
        r401.status_code = 401
        r401.headers = {"WWW-Authenticate": 'DPoP nonce="fresh-nonce"'}
        r200 = MagicMock()
        r200.status_code = 200
        r200.content = b"data"
        mock_session.get.side_effect = [r401, r200]

        client = _make_dpop_client(mock_session)
        result = client.get("stash://pod/resource")

        assert result == b"data"
        assert client._dpop_nonce == "fresh-nonce"

    def test_nonce_included_in_retry_request(self):
        mock_session = MagicMock()
        r401 = MagicMock()
        r401.status_code = 401
        r401.headers = {"WWW-Authenticate": 'DPoP nonce="retry-nonce"'}
        r200 = MagicMock()
        r200.status_code = 200
        r200.content = b"ok"
        mock_session.get.side_effect = [r401, r200]

        client = _make_dpop_client(mock_session)
        client.get("stash://pod/resource")

        # Second call's DPoP header must contain the nonce
        second_call_headers = mock_session.get.call_args_list[1][1].get("headers", {})
        dpop_token = second_call_headers.get("DPoP", "")
        payload = _decode_payload(dpop_token)
        assert payload.get("nonce") == "retry-nonce"

    def test_no_nonce_in_401_raises_solid_error(self):
        # 401 with no dpop-nonce: retry loop runs, second attempt also 401 → SolidError
        mock_session = MagicMock()
        r401a = MagicMock()
        r401a.status_code = 401
        r401a.headers = {}
        r401b = MagicMock()
        r401b.status_code = 401
        r401b.headers = {}
        mock_session.get.side_effect = [r401a, r401b]

        client = _make_dpop_client(mock_session)
        from proxion_messenger_core.solid_client import SolidError
        with pytest.raises(SolidError):
            client.get("stash://pod/resource")
        # Both attempts were made
        assert mock_session.get.call_count == 2

    def test_user_agent_sent_in_request(self):
        mock_session = MagicMock()
        r200 = MagicMock()
        r200.status_code = 200
        r200.content = b"ok"
        mock_session.get.return_value = r200

        client = _make_dpop_client(mock_session)
        client.get("stash://pod/resource")

        call_headers = mock_session.get.call_args[1].get("headers", {})
        assert call_headers.get("User-Agent") == "Proxion/1.0"


# ---------------------------------------------------------------------------
# CssClientCredentials — token endpoint nonce retry
# ---------------------------------------------------------------------------

class TestFetchAccessTokenNonceRetry:
    def test_retries_with_nonce_on_401(self):
        from proxion_messenger_core.css_auth import CssClientCredentials
        key = _make_key()
        creds = CssClientCredentials(
            css_base_url="https://pod.example",
            client_id="id",
            client_secret="secret",
            identity_key=key,
        )

        r401 = MagicMock()
        r401.status_code = 401
        r401.headers = {"WWW-Authenticate": 'DPoP nonce="token-nonce"'}
        r401.raise_for_status = MagicMock(side_effect=Exception("401"))

        r200 = MagicMock()
        r200.status_code = 200
        r200.headers = {}
        r200.raise_for_status = MagicMock()
        r200.json = MagicMock(return_value={"access_token": "tok", "expires_in": 3600})

        with patch("httpx.post", side_effect=[r401, r200]) as mock_post:
            token, exp = creds.fetch_access_token()

        assert token == "tok"
        assert exp == 3600
        assert mock_post.call_count == 2
        # Second call's DPoP proof should include the nonce
        second_headers = mock_post.call_args_list[1][1]["headers"]
        payload = _decode_payload(second_headers["DPoP"])
        assert payload.get("nonce") == "token-nonce"

    def test_uses_token_endpoint_url_when_set(self):
        from proxion_messenger_core.css_auth import CssClientCredentials
        key = _make_key()
        creds = CssClientCredentials(
            css_base_url="https://pod.example",
            client_id="id",
            client_secret="secret",
            identity_key=key,
            token_endpoint_url="https://auth.example/oauth/token",
        )
        r200 = MagicMock()
        r200.status_code = 200
        r200.headers = {}
        r200.raise_for_status = MagicMock()
        r200.json = MagicMock(return_value={"access_token": "tok2", "expires_in": 1800})

        with patch("httpx.post", return_value=r200) as mock_post:
            token, _ = creds.fetch_access_token()

        assert mock_post.call_args[0][0] == "https://auth.example/oauth/token"
        assert token == "tok2"
