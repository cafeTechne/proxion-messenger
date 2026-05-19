"""Tests for proxion_messenger_core.css_auth — CSS client credentials and DPoP Solid client."""

import pytest
import respx
import httpx
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.css_auth import CssClientCredentials, DpopSolidClient
from proxion_messenger_core.solid import SolidResolver


@pytest.fixture
def key():
    """Generate a fresh Ed25519 private key for testing."""
    return Ed25519PrivateKey.generate()


@pytest.fixture
def creds(key):
    """Create test CSS client credentials."""
    return CssClientCredentials(
        css_base_url="http://localhost:3001",
        client_id="test-client-id",
        client_secret="test-secret",
        identity_key=key,
    )


@pytest.fixture
def resolver():
    """Create a test Solid resolver."""
    return SolidResolver("http://localhost:3001/alice/")


class TestCssClientCredentials:
    """Tests for CssClientCredentials."""

    def test_fetch_access_token_posts_to_oidc_token(self, creds):
        """fetch_access_token POSTs to /oidc/token with DPoP and grant_type."""
        with respx.mock:
            mock_route = respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "tok123", "expires_in": 600}
                )
            )

            token, expires_in = creds.fetch_access_token()

            assert token == "tok123"
            assert expires_in == 600
            assert mock_route.called

            # Verify the request had the expected headers and body
            request = mock_route.calls[0].request
            assert "DPoP" in request.headers
            # scope=pod_rw is included since Round 6 added per-scope token caching
            assert b"grant_type=client_credentials" in request.content
            assert b"client_id=test-client-id" in request.content
            assert b"client_secret=test-secret" in request.content

    def test_fetch_access_token_default_expires_in(self, creds):
        """fetch_access_token returns 3600 if expires_in is missing."""
        with respx.mock:
            respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "tok"}
                )
            )

            token, expires_in = creds.fetch_access_token()

            assert token == "tok"
            assert expires_in == 3600

    def test_get_token_caches_result(self, creds):
        """get_token caches the token and only fetches once."""
        with respx.mock:
            mock_route = respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "tok123", "expires_in": 3600}
                )
            )

            # First call
            token1 = creds.get_token()
            assert token1 == "tok123"
            assert mock_route.call_count == 1

            # Second call should use cache (same token object, expires_at in future)
            token2 = creds.get_token()
            assert token2 == "tok123"
            assert mock_route.call_count == 1  # Still only 1 call

    def test_get_token_refreshes_when_expired(self, creds):
        """get_token refreshes when cached token is expired."""
        with respx.mock:
            mock_route = respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "new", "expires_in": 3600}
                )
            )

            # Set cache to an expired token
            creds._cached_token = "old"
            creds._token_expires_at = 0.0  # Already expired

            token = creds.get_token()

            assert token == "new"
            assert mock_route.called


class TestDpopSolidClient:
    """Tests for DpopSolidClient."""

    def test_dpop_solid_client_injects_headers_on_get(self, resolver, creds):
        """get() request includes Authorization and DPoP headers."""
        with respx.mock:
            mock_route = respx.get("http://localhost:3001/alice/foo").mock(
                return_value=httpx.Response(200, content=b"hello")
            )

            # Mock the token endpoint so get() can fetch a token
            respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "mytoken", "expires_in": 3600}
                )
            )

            client = DpopSolidClient(resolver, creds)
            data = client.get("stash://alice/foo")

            assert data == b"hello"
            assert mock_route.called

            # Verify headers on the GET request
            request = mock_route.calls[0].request
            assert "Authorization" in request.headers
            assert request.headers["Authorization"].startswith("DPoP ")
            assert "DPoP" in request.headers

    def test_dpop_solid_client_auth_includes_token(self, resolver, creds):
        """Authorization header contains the exact token value."""
        with respx.mock:
            mock_route = respx.get("http://localhost:3001/alice/foo").mock(
                return_value=httpx.Response(200, content=b"hello")
            )

            # Pre-set a cached token so we don't need to mock /oidc/token
            import time
            creds._cached_tokens["pod_rw"] = "mytoken"
            creds._token_expiries["pod_rw"] = time.time() + 3600
            creds._token_issued_at["pod_rw"] = time.time()

            client = DpopSolidClient(resolver, creds)
            data = client.get("stash://alice/foo")

            assert data == b"hello"

            # Verify Authorization header is exactly "DPoP mytoken"
            request = mock_route.calls[0].request
            assert request.headers["Authorization"] == "DPoP mytoken"
            assert "DPoP" in request.headers

    def test_dpop_solid_client_put_injects_headers(self, resolver, creds):
        """put() request includes Authorization and DPoP headers."""
        with respx.mock:
            mock_route = respx.put("http://localhost:3001/alice/bar").mock(
                return_value=httpx.Response(201)
            )

            # Mock the token endpoint
            respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "mytoken", "expires_in": 3600}
                )
            )

            client = DpopSolidClient(resolver, creds)
            client.put("stash://alice/bar", b"data")

            assert mock_route.called

            # Verify headers on the PUT request
            request = mock_route.calls[0].request
            assert "Authorization" in request.headers
            assert request.headers["Authorization"].startswith("DPoP ")
            assert "DPoP" in request.headers
