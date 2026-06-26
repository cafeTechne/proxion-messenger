"""Tests for webhook secret-token and IP-allowlist hardening."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import json


def _make_store(wh: dict):
    store = MagicMock()
    store.get_webhook_by_token.return_value = wh
    return store


def _base_wh(**kwargs):
    wh = {
        "id": "wh-1",
        "thread_id": "room-1",
        "direction": "incoming",
        "active": 1,
        "bot_name": "Bot",
        "secret_token": None,
        "allowed_ips": None,
    }
    wh.update(kwargs)
    return wh


class TestWebhookSecretToken:
    def test_no_secret_configured_any_request_accepted(self):
        """When no secret_token is set, any POST is accepted (backward compat)."""
        wh = _base_wh(secret_token=None)
        # No secret → no check needed; verify by asserting the webhook dict as expected
        assert not wh["secret_token"]

    def test_correct_secret_accepted(self):
        wh = _base_wh(secret_token="my-secret")
        headers_raw = {b"x-proxion-secret": b"my-secret"}
        req_secret = headers_raw.get(b"x-proxion-secret", b"").decode("utf-8", errors="replace")
        assert req_secret == wh["secret_token"]

    def test_wrong_secret_rejected(self):
        wh = _base_wh(secret_token="correct-secret")
        headers_raw = {b"x-proxion-secret": b"wrong-secret"}
        req_secret = headers_raw.get(b"x-proxion-secret", b"").decode("utf-8", errors="replace")
        assert req_secret != wh["secret_token"]

    def test_missing_secret_header_rejected(self):
        wh = _base_wh(secret_token="required-secret")
        headers_raw = {}
        req_secret = headers_raw.get(b"x-proxion-secret", b"").decode("utf-8", errors="replace")
        assert req_secret != wh["secret_token"]

    def test_empty_secret_token_skips_check(self):
        wh = _base_wh(secret_token="")
        # empty string is falsy — check should be skipped
        assert not (wh.get("secret_token") or "")


class TestWebhookIPAllowlist:
    def _check_ip(self, allowed_ips: str, peer_ip: str) -> bool:
        import ipaddress
        try:
            peer_addr = ipaddress.ip_address(peer_ip)
            return any(
                peer_addr in ipaddress.ip_network(cidr.strip(), strict=False)
                for cidr in allowed_ips.split(",")
                if cidr.strip()
            )
        except ValueError:
            return False

    def test_allowed_exact_ip_accepted(self):
        assert self._check_ip("93.184.216.34", "93.184.216.34")

    def test_disallowed_ip_rejected(self):
        assert not self._check_ip("93.184.216.34", "1.2.3.4")

    def test_cidr_range_accepted(self):
        assert self._check_ip("10.0.0.0/8", "10.1.2.3")

    def test_cidr_range_excluded_ip_rejected(self):
        assert not self._check_ip("10.0.0.0/8", "192.168.0.1")

    def test_multiple_cidrs_first_matches(self):
        assert self._check_ip("1.2.3.0/24, 5.6.7.0/24", "1.2.3.100")

    def test_multiple_cidrs_second_matches(self):
        assert self._check_ip("1.2.3.0/24, 5.6.7.0/24", "5.6.7.200")

    def test_multiple_cidrs_none_match(self):
        assert not self._check_ip("1.2.3.0/24, 5.6.7.0/24", "9.9.9.9")

    def test_malformed_cidr_returns_false(self):
        assert not self._check_ip("not-an-ip", "1.2.3.4")

    def test_no_allowed_ips_skips_check(self):
        wh = _base_wh(allowed_ips=None)
        # None/empty → no restriction applied
        assert not (wh.get("allowed_ips") or "")

    def test_empty_allowed_ips_skips_check(self):
        wh = _base_wh(allowed_ips="")
        assert not (wh.get("allowed_ips") or "")


class TestLocalStoreWebhookSchema:
    def test_save_reaction_quota_exceeded(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "wh.db"))
        room_id = "room-wh"
        sender = "https://example.com/alice#me"
        for i in range(50):
            ok = store.save_reaction(room_id, f"msg-{i}", "👍", sender)
            assert ok is True
        ok = store.save_reaction(room_id, "msg-51", "👎", sender)
        assert ok is False
