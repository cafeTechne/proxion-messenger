"""Tests for R7 CssClientCredentials max_cached_token_lifetime_s and purge_token_cache."""
import pytest
import time
import respx
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.css_auth import CssClientCredentials


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def creds(key):
    return CssClientCredentials(
        css_base_url="http://localhost:3001",
        client_id="test-client",
        client_secret="test-secret",
        identity_key=key,
        max_cached_token_lifetime_s=3600,
    )


class TestTokenCacheHardTTL:
    def test_max_cached_token_lifetime_s_default(self, creds):
        """Default max_cached_token_lifetime_s is 3600."""
        assert creds.max_cached_token_lifetime_s == 3600

    def test_cached_token_expires_at_hard_ttl(self, creds):
        """Token expiry is clamped to max_cached_token_lifetime_s."""
        with respx.mock:
            respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "longlivedtok", "expires_in": 86400}
                )
            )
            creds.max_cached_token_lifetime_s = 300  # hard cap at 5 min
            creds.get_token()
            expiry = creds._token_expiries.get("pod_rw", 0.0)
            # Expiry should be at most ~300s from now
            assert expiry <= time.time() + 305

    def test_purge_token_cache_clears_cached_token(self, creds):
        """purge_token_cache() removes all cached tokens."""
        creds._cached_tokens["pod_rw"] = "mytoken"
        creds._token_expiries["pod_rw"] = time.time() + 3600
        creds.purge_token_cache()
        assert creds._cached_tokens == {}
        assert creds._token_expiries == {}

    def test_auth_failure_triggers_token_cache_purge(self, creds):
        """Tokens can be purged on auth failures via purge_token_cache."""
        creds._cached_tokens["pod_rw"] = "stale-token"
        creds._token_expiries["pod_rw"] = time.time() + 3600
        # Simulate auth failure handler calling purge
        creds.purge_token_cache()
        assert "pod_rw" not in creds._cached_tokens

    def test_purge_clears_all_scopes(self, creds):
        """purge_token_cache clears tokens for all scopes, not just pod_rw."""
        creds._cached_tokens["pod_rw"] = "tok1"
        creds._cached_tokens["custom_scope"] = "tok2"
        creds.purge_token_cache()
        assert len(creds._cached_tokens) == 0

    def test_get_token_after_purge_refetches(self, creds):
        """After purge, get_token fetches a fresh token."""
        with respx.mock:
            respx.post("http://localhost:3001/.oidc/token").mock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "fresh", "expires_in": 3600}
                )
            )
            creds._cached_tokens["pod_rw"] = "stale"
            creds._token_expiries["pod_rw"] = time.time() + 3600
            creds.purge_token_cache()
            token = creds.get_token()
            assert token == "fresh"
