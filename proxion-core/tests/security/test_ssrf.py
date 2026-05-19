"""Tests for SSRF protection in network.py, webid_verify, and relay."""
import os
import pytest
from unittest.mock import patch, MagicMock

from proxion_messenger_core.network import _resolve_safe_ip, safe_get, NetworkError


# ---------------------------------------------------------------------------
# _resolve_safe_ip unit tests
# ---------------------------------------------------------------------------

class TestResolveSafeIP:
    def test_loopback_blocked_by_default(self):
        assert _resolve_safe_ip("http://127.0.0.1/foo") is None

    def test_ipv6_loopback_blocked(self):
        assert _resolve_safe_ip("http://[::1]/foo") is None

    def test_link_local_blocked(self):
        assert _resolve_safe_ip("http://169.254.169.254/metadata") is None

    def test_rfc1918_10_blocked(self):
        assert _resolve_safe_ip("http://10.0.0.1/foo") is None

    def test_rfc1918_192168_blocked(self):
        assert _resolve_safe_ip("http://192.168.1.100/foo") is None

    def test_rfc1918_172_blocked(self):
        assert _resolve_safe_ip("http://172.16.0.1/foo") is None

    def test_userinfo_credential_bypass_rejected(self):
        # http://user@127.0.0.1/ — urlparse.hostname strips userinfo
        assert _resolve_safe_ip("http://user@127.0.0.1/relay") is None

    def test_non_http_scheme_rejected(self):
        assert _resolve_safe_ip("ftp://example.com/file") is None
        assert _resolve_safe_ip("file:///etc/passwd") is None

    def test_empty_host_rejected(self):
        assert _resolve_safe_ip("http:///path") is None

    def test_public_ip_allowed(self):
        _fake = [(None, None, None, None, ("93.184.216.34", 0))]
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_fake):
            result = _resolve_safe_ip("https://example.com/resource")
        assert result == "93.184.216.34"

    def test_private_allowed_with_env_flag(self, monkeypatch):
        monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
        assert _resolve_safe_ip("http://127.0.0.1/foo") is not None
        assert _resolve_safe_ip("http://192.168.1.1/foo") is not None

    def test_resolution_failure_returns_none(self):
        import socket
        with patch("proxion_messenger_core.network.socket.getaddrinfo",
                   side_effect=socket.gaierror("NXDOMAIN")):
            result = _resolve_safe_ip("http://nonexistent.invalid/foo")
        assert result is None

    def test_split_horizon_all_addresses_checked(self):
        # If ANY resolved address is private, the whole batch is rejected
        _mixed = [
            (None, None, None, None, ("93.184.216.34", 0)),  # public
            (None, None, None, None, ("127.0.0.1", 0)),       # loopback → reject
        ]
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_mixed):
            result = _resolve_safe_ip("https://tricky.example.com/resource")
        assert result is None


class TestSafeGet:
    def test_blocked_ip_raises_network_error(self):
        with pytest.raises(NetworkError, match="blocked"):
            safe_get("http://127.0.0.1/foo")

    def test_non_2xx_raises_network_error(self):
        _fake = [(None, None, None, None, ("93.184.216.34", 0))]
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_fake):
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value = mock_resp
            with patch("proxion_messenger_core.network.httpx.Client", return_value=mock_client):
                with pytest.raises(NetworkError, match="HTTP 404"):
                    safe_get("https://example.com/missing")

    def test_body_size_limit_enforced(self):
        _fake = [(None, None, None, None, ("93.184.216.34", 0))]
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_fake):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_bytes.return_value = iter([b"X" * 100_000, b"X" * 100_000])
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value = mock_resp
            with patch("proxion_messenger_core.network.httpx.Client", return_value=mock_client):
                with pytest.raises(NetworkError, match="exceeds"):
                    safe_get("https://example.com/big", max_bytes=50_000)


# ---------------------------------------------------------------------------
# webid_verify SSRF tests
# ---------------------------------------------------------------------------

class TestWebIDVerifySSRF:
    def test_private_ip_webid_blocked(self):
        from proxion_messenger_core.webid_verify import get_webid_pub_hex, invalidate_cache
        invalidate_cache()
        # Should return None without making any network request to a private IP
        result = get_webid_pub_hex("http://192.168.0.1/profile/card#me")
        assert result is None

    def test_loopback_webid_blocked(self):
        from proxion_messenger_core.webid_verify import get_webid_pub_hex, invalidate_cache
        invalidate_cache()
        result = get_webid_pub_hex("http://127.0.0.1:3000/alice/profile/card#me")
        assert result is None

    def test_public_webid_resolves(self):
        from proxion_messenger_core.webid_verify import get_webid_pub_hex, invalidate_cache
        invalidate_cache()
        _fake_ip = [(None, None, None, None, ("93.184.216.34", 0))]
        pub_hex = "a" * 64
        mock_bytes = f'{{"identity_pub_hex": "{pub_hex}"}}'.encode()
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_fake_ip), \
             patch("proxion_messenger_core.network.httpx.Client") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_bytes.return_value = iter([mock_bytes])
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_client_inst = MagicMock()
            mock_client_inst.__enter__ = lambda s: s
            mock_client_inst.__exit__ = MagicMock(return_value=False)
            mock_client_inst.stream.return_value = mock_resp
            MockClient.return_value = mock_client_inst
            result = get_webid_pub_hex("https://pod.example.com/alice/profile/card#me")
        assert result == pub_hex


# ---------------------------------------------------------------------------
# relay SSRF tests
# ---------------------------------------------------------------------------

class TestRelaySsrf:
    def test_validate_relay_target_blocks_loopback(self):
        from proxion_messenger_core.relay import _validate_relay_target
        assert not _validate_relay_target("http://127.0.0.1:8080/relay")

    def test_validate_relay_target_blocks_private(self):
        from proxion_messenger_core.relay import _validate_relay_target
        assert not _validate_relay_target("http://10.0.0.1/relay")
        assert not _validate_relay_target("http://172.16.0.1/relay")

    def test_validate_relay_target_allows_public(self):
        from proxion_messenger_core.relay import _validate_relay_target
        _fake = [(None, None, None, None, ("93.184.216.34", 0))]
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_fake):
            assert _validate_relay_target("https://peer.example.com/relay")

    @pytest.mark.asyncio
    async def test_post_relay_blocked_private(self):
        from proxion_messenger_core.relay import post_relay
        result = await post_relay("http://127.0.0.1/relay", {"msg": "test"})
        assert result is False
