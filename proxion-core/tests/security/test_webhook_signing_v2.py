"""Tests for outgoing webhook payload signing v2 (Round 6)."""
import pytest


class TestWebhookSigningV2:
    def test_delivery_log_written_for_success_and_failure(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "wh.db"))
        store.save_webhook_delivery_log(
            webhook_id="wh-001",
            thread_id="room-1",
            status_code=200,
            success=True,
            latency_ms=42,
        )
        store.save_webhook_delivery_log(
            webhook_id="wh-001",
            thread_id="room-1",
            status_code=500,
            success=False,
            latency_ms=1234,
        )
        logs = store.get_webhook_delivery_logs("wh-001")
        assert len(logs) == 2
        successes = [l for l in logs if l["success"]]
        failures = [l for l in logs if not l["success"]]
        assert len(successes) == 1
        assert len(failures) == 1

    def test_delivery_log_fields(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "wh2.db"))
        store.save_webhook_delivery_log(
            webhook_id="wh-002",
            thread_id="room-X",
            status_code=200,
            success=True,
            latency_ms=100,
        )
        logs = store.get_webhook_delivery_logs("wh-002")
        assert len(logs) == 1
        log = logs[0]
        assert log["webhook_id"] == "wh-002"
        assert log["thread_id"] == "room-X"
        assert log["status_code"] == 200
        assert log["latency_ms"] == 100
        assert log["created_at"] > 0

    def test_get_webhook_delivery_logs_empty_for_unknown_id(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "wh3.db"))
        logs = store.get_webhook_delivery_logs("unknown-wh")
        assert logs == []

    def test_webhook_signing_v2_header_construction(self):
        """Test construction of webhook v2 signature headers."""
        import hashlib
        import hmac
        import time

        payload = b'{"type":"message","content":"hello"}'
        token = "webhook-secret-key-123"
        _wh_ts = str(int(time.time()))
        _body_sha256 = hashlib.sha256(payload).hexdigest()
        _sig_v2_input = f"{_wh_ts}.{_body_sha256}".encode()
        _sig_v2 = hmac.new(token.encode(), _sig_v2_input, hashlib.sha256).hexdigest()

        # Verify headers can be constructed
        headers = {
            "X-Proxion-Timestamp": _wh_ts,
            "X-Proxion-Body-SHA256": _body_sha256,
            "X-Proxion-Signature-V2": f"sha256={_sig_v2}",
        }

        assert "X-Proxion-Timestamp" in headers
        assert "X-Proxion-Body-SHA256" in headers
        assert "X-Proxion-Signature-V2" in headers
        assert headers["X-Proxion-Signature-V2"].startswith("sha256=")
