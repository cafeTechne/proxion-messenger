"""Tests for the WebID resolution sliding-window rate limiter."""
import pytest
from unittest.mock import patch, MagicMock

from proxion_messenger_core.webid_verify import (
    get_webid_pub_hex,
    invalidate_cache,
    reset_rate_limits,
    _check_webid_rate_limit,
    _RATE_LIMIT_MAX,
    _RATE_LIMIT_WINDOW,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset cache and rate limit windows before each test."""
    invalidate_cache()
    reset_rate_limits()
    yield
    invalidate_cache()
    reset_rate_limits()


class TestCheckWebidRateLimit:
    def test_empty_ip_always_allowed(self):
        for _ in range(100):
            assert _check_webid_rate_limit("", 1000.0) is True

    def test_first_request_allowed(self):
        assert _check_webid_rate_limit("1.2.3.4", 1000.0) is True

    def test_up_to_limit_allowed(self):
        ip = "1.2.3.4"
        for i in range(_RATE_LIMIT_MAX):
            assert _check_webid_rate_limit(ip, 1000.0 + i) is True

    def test_one_over_limit_rejected(self):
        ip = "1.2.3.5"
        for i in range(_RATE_LIMIT_MAX):
            _check_webid_rate_limit(ip, 1000.0 + i * 0.1)
        assert _check_webid_rate_limit(ip, 1000.0 + _RATE_LIMIT_MAX * 0.1) is False

    def test_window_expiry_frees_slots(self):
        ip = "1.2.3.6"
        t0 = 1000.0
        for i in range(_RATE_LIMIT_MAX):
            _check_webid_rate_limit(ip, t0 + i * 0.1)
        # All slots used — rejected at t0+1
        assert _check_webid_rate_limit(ip, t0 + 1.0) is False
        # After the window elapses, slots free up
        t_after = t0 + _RATE_LIMIT_WINDOW + 1.0
        assert _check_webid_rate_limit(ip, t_after) is True

    def test_different_ips_independent_quotas(self):
        for i in range(_RATE_LIMIT_MAX):
            _check_webid_rate_limit("10.0.0.1", 1000.0 + i)
        # IP .1 is exhausted, but IP .2 is fresh
        assert _check_webid_rate_limit("10.0.0.1", 1000.0 + _RATE_LIMIT_MAX) is False
        assert _check_webid_rate_limit("10.0.0.2", 1000.0) is True


class TestGetWebidPubHexRateLimit:
    PUB_HEX = "a" * 64

    def _mock_discovery(self):
        """Patch _fetch_proxion_discovery to always return a fixed pub_hex."""
        return patch(
            "proxion_messenger_core.webid_verify._fetch_proxion_discovery",
            return_value=self.PUB_HEX,
        )

    def test_cache_hit_bypasses_rate_limit(self):
        ip = "5.5.5.5"
        t0 = 2000.0
        webid = "https://pod.example.com/alice/profile/card#me"
        with self._mock_discovery():
            # First call: cache miss (counts against rate limit)
            result = get_webid_pub_hex(webid, _now=t0, peer_ip=ip)
            assert result == self.PUB_HEX
            # Subsequent calls: cache hit, rate limit NOT consumed
            for _ in range(1000):
                result2 = get_webid_pub_hex(webid, _now=t0 + 1, peer_ip=ip)
                assert result2 == self.PUB_HEX

    def test_rate_limit_exceeded_returns_none(self):
        ip = "6.6.6.6"
        t0 = 3000.0
        with self._mock_discovery():
            # Exhaust the rate limit with distinct WebIDs (each is a cache miss)
            for i in range(_RATE_LIMIT_MAX):
                get_webid_pub_hex(
                    f"https://pod.example.com/user{i}/profile/card#me",
                    _now=t0 + i * 0.01,
                    peer_ip=ip,
                )
            # 11th unique WebID from same IP → rate limited → None
            result = get_webid_pub_hex(
                "https://pod.example.com/userX/profile/card#me",
                _now=t0 + _RATE_LIMIT_MAX * 0.01,
                peer_ip=ip,
            )
            assert result is None

    def test_no_peer_ip_never_rate_limited(self):
        t0 = 4000.0
        with self._mock_discovery():
            # No peer_ip → internal caller, no rate limit applied
            for i in range(_RATE_LIMIT_MAX * 3):
                result = get_webid_pub_hex(
                    f"https://pod.example.com/u{i}/profile/card#me",
                    _now=t0 + i * 0.01,
                    peer_ip="",
                )
                assert result == self.PUB_HEX
