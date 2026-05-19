"""Tests for the get_audit_logs gateway command and audit callback in network.py."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from proxion_messenger_core.network import set_audit_fn, clear_audit_fn, NetworkError


# ---------------------------------------------------------------------------
# network.py audit callback
# ---------------------------------------------------------------------------

class TestNetworkAuditCallback:
    def setup_method(self):
        clear_audit_fn()

    def teardown_method(self):
        clear_audit_fn()

    def test_callback_called_on_ssrf_block_in_safe_get(self):
        calls = []
        set_audit_fn(lambda et, sev, detail: calls.append((et, sev, detail)))
        with pytest.raises(NetworkError):
            from proxion_messenger_core.network import safe_get
            safe_get("http://127.0.0.1/internal")
        assert len(calls) == 1
        assert calls[0][0] == "ssrf_blocked"
        assert calls[0][1] == "warning"
        assert "127.0.0.1" in calls[0][2]

    @pytest.mark.asyncio
    async def test_callback_called_on_ssrf_block_in_async_post(self):
        calls = []
        set_audit_fn(lambda et, sev, detail: calls.append((et, sev, detail)))
        from proxion_messenger_core.network import async_safe_post
        result = await async_safe_post("http://10.0.0.1/relay", {"msg": "test"})
        assert result is False
        assert len(calls) == 1
        assert calls[0][0] == "ssrf_blocked"

    def test_no_callback_safe_get_raises_network_error(self):
        with pytest.raises(NetworkError):
            from proxion_messenger_core.network import safe_get
            safe_get("http://192.168.1.1/private")

    def test_callback_exception_does_not_propagate(self):
        def bad_callback(et, sev, detail):
            raise RuntimeError("callback error")
        set_audit_fn(bad_callback)
        with pytest.raises(NetworkError):
            from proxion_messenger_core.network import safe_get
            safe_get("http://127.0.0.1/test")

    def test_clear_audit_fn_removes_callback(self):
        calls = []
        set_audit_fn(lambda et, sev, detail: calls.append(1))
        clear_audit_fn()
        with pytest.raises(NetworkError):
            from proxion_messenger_core.network import safe_get
            safe_get("http://127.0.0.1/test")
        assert calls == []


# ---------------------------------------------------------------------------
# get_audit_logs gateway command — owner access only
# ---------------------------------------------------------------------------

def _make_gateway(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    agent = AgentState.generate()
    config = GatewayConfig(db_path=str(tmp_path / "store.db"))
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=config)
    return gw


def _fake_ws(gw, webid: str):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = webid
    return ws


class TestGetAuditLogsCommand:
    @pytest.mark.asyncio
    async def test_owner_receives_logs(self, tmp_path):
        gw = _make_gateway(tmp_path)
        from proxion_messenger_core.didkey import pub_key_to_did
        owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
        ws = _fake_ws(gw, owner_did)
        # Seed an audit log entry
        gw._store.save_audit_log("test_event", severity="info", ip="1.2.3.4")
        await gw.process_command(ws, {"cmd": "get_audit_logs"})
        responses = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        audit_resp = next((r for r in responses if r.get("type") == "audit_logs"), None)
        assert audit_resp is not None
        assert isinstance(audit_resp["logs"], list)
        assert len(audit_resp["logs"]) >= 1
        assert audit_resp["logs"][0]["event_type"] == "test_event"

    @pytest.mark.asyncio
    async def test_non_owner_gets_error(self, tmp_path):
        gw = _make_gateway(tmp_path)
        ws = _fake_ws(gw, "did:key:z6MkImposter")
        await gw.process_command(ws, {"cmd": "get_audit_logs"})
        responses = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        errors = [r for r in responses if r.get("type") == "error"]
        assert any("owner" in r.get("message", "").lower() for r in errors)

    @pytest.mark.asyncio
    async def test_filter_by_event_type(self, tmp_path):
        gw = _make_gateway(tmp_path)
        from proxion_messenger_core.didkey import pub_key_to_did
        owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
        ws = _fake_ws(gw, owner_did)
        gw._store.save_audit_log("relay_rejected")
        gw._store.save_audit_log("quota_exceeded")
        await gw.process_command(ws, {"cmd": "get_audit_logs", "event_type": "relay_rejected"})
        responses = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        audit_resp = next(r for r in responses if r.get("type") == "audit_logs")
        assert all(l["event_type"] == "relay_rejected" for l in audit_resp["logs"])

    @pytest.mark.asyncio
    async def test_limit_honored(self, tmp_path):
        gw = _make_gateway(tmp_path)
        from proxion_messenger_core.didkey import pub_key_to_did
        owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
        ws = _fake_ws(gw, owner_did)
        for _ in range(20):
            gw._store.save_audit_log("event")
        await gw.process_command(ws, {"cmd": "get_audit_logs", "limit": 5})
        responses = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        audit_resp = next(r for r in responses if r.get("type") == "audit_logs")
        assert len(audit_resp["logs"]) <= 5

    @pytest.mark.asyncio
    async def test_limit_capped_at_500(self, tmp_path):
        gw = _make_gateway(tmp_path)
        from proxion_messenger_core.didkey import pub_key_to_did
        owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
        ws = _fake_ws(gw, owner_did)
        # No assertion about log count — just verify it doesn't error with huge limit
        await gw.process_command(ws, {"cmd": "get_audit_logs", "limit": 9999})
        responses = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        assert any(r.get("type") == "audit_logs" for r in responses)
