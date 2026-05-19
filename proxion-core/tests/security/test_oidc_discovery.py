"""Tests for fetch_oidc_token_endpoint in discovery.py."""
import json
import pytest
from unittest.mock import patch, MagicMock

from proxion_messenger_core.discovery import fetch_oidc_token_endpoint
from proxion_messenger_core.errors import ProxionError


def _mock_safe_get(body: dict):
    return patch(
        "proxion_messenger_core.discovery.safe_get",
        return_value=json.dumps(body).encode(),
    )


class TestFetchOidcTokenEndpoint:
    def test_returns_token_endpoint(self):
        with _mock_safe_get({"token_endpoint": "https://auth.example/token", "issuer": "https://auth.example"}):
            result = fetch_oidc_token_endpoint("https://auth.example")
        assert result == "https://auth.example/token"

    def test_raises_on_missing_token_endpoint(self):
        with _mock_safe_get({"issuer": "https://auth.example"}):
            with pytest.raises(ProxionError, match="missing token_endpoint"):
                fetch_oidc_token_endpoint("https://auth.example")

    def test_raises_on_network_error(self):
        from proxion_messenger_core.network import NetworkError
        with patch("proxion_messenger_core.discovery.safe_get", side_effect=NetworkError("timeout")):
            with pytest.raises(ProxionError, match="Failed to fetch OIDC discovery"):
                fetch_oidc_token_endpoint("https://unreachable.example")

    def test_raises_on_invalid_json(self):
        with patch("proxion_messenger_core.discovery.safe_get", return_value=b"not-json"):
            with pytest.raises(ProxionError, match="Failed to fetch OIDC discovery"):
                fetch_oidc_token_endpoint("https://auth.example")

    def test_fetches_correct_well_known_url(self):
        captured = {}
        def fake_safe_get(url, **kwargs):
            captured["url"] = url
            return json.dumps({"token_endpoint": "https://auth.example/t"}).encode()
        with patch("proxion_messenger_core.discovery.safe_get", side_effect=fake_safe_get):
            fetch_oidc_token_endpoint("https://auth.example")
        assert captured["url"] == "https://auth.example/.well-known/openid-configuration"

    def test_strips_trailing_slash_from_issuer(self):
        captured = {}
        def fake_safe_get(url, **kwargs):
            captured["url"] = url
            return json.dumps({"token_endpoint": "https://auth.example/t"}).encode()
        with patch("proxion_messenger_core.discovery.safe_get", side_effect=fake_safe_get):
            fetch_oidc_token_endpoint("https://auth.example/")
        assert captured["url"] == "https://auth.example/.well-known/openid-configuration"

    def test_empty_token_endpoint_raises(self):
        with _mock_safe_get({"token_endpoint": ""}):
            with pytest.raises(ProxionError, match="missing token_endpoint"):
                fetch_oidc_token_endpoint("https://auth.example")


class TestFetchAccessTokenUsesDiscoveredEndpoint:
    def test_uses_token_endpoint_url_field(self):
        from proxion_messenger_core.css_auth import CssClientCredentials
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        creds = CssClientCredentials(
            css_base_url="https://pod.example",
            client_id="id",
            client_secret="secret",
            identity_key=key,
            token_endpoint_url="https://auth.example/custom/token",
        )
        r200 = MagicMock()
        r200.status_code = 200
        r200.headers = {}
        r200.raise_for_status = MagicMock()
        r200.json = MagicMock(return_value={"access_token": "tok", "expires_in": 3600})

        with patch("httpx.post", return_value=r200) as mock_post:
            token, _ = creds.fetch_access_token()

        assert mock_post.call_args[0][0] == "https://auth.example/custom/token"
        assert token == "tok"

    def test_defaults_to_oidc_token_path_without_override(self):
        from proxion_messenger_core.css_auth import CssClientCredentials
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        creds = CssClientCredentials(
            css_base_url="https://pod.example",
            client_id="id",
            client_secret="secret",
            identity_key=key,
        )
        r200 = MagicMock()
        r200.status_code = 200
        r200.headers = {}
        r200.raise_for_status = MagicMock()
        r200.json = MagicMock(return_value={"access_token": "t2", "expires_in": 1800})

        with patch("httpx.post", return_value=r200) as mock_post:
            creds.fetch_access_token()

        assert mock_post.call_args[0][0] == "https://pod.example/.oidc/token"
