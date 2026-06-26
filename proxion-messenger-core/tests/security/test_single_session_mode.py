"""Tests for R7 PROXION_SINGLE_SESSION=1 — single-session enforcement."""
import asyncio
import json
import os
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def gateway(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "test.db")),
    )


def make_ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = ("127.0.0.1", 9000)
    return ws


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSingleSessionMode:
    def test_new_login_revokes_prior_sessions_when_enabled(self, gateway):
        os.environ["PROXION_SINGLE_SESSION"] = "1"
        identity = "did:key:z6MkTestIdentity"
        ws1 = make_ws()
        ws2 = make_ws()
        # Register ws1
        gateway.clients.add(ws1)
        gateway._client_webids[ws1] = identity
        gateway._webid_sockets[identity] = {ws1}
        gateway._session_meta[ws1] = {"session_id": "s1", "connected_at": "", "ip_addr": "127.0.0.1"}
        # Now register ws2 for same identity
        gateway.clients.add(ws2)
        gateway._session_meta[ws2] = {"session_id": "s2", "connected_at": "", "ip_addr": "127.0.0.1"}
        run(gateway._handle_register(ws2, {
            "did": identity, "webid": "", "display_name": "", "gateway_url": "",
        }))
        # ws1 should have received session_revoked
        calls = [json.loads(c.args[0]) for c in ws1.send.call_args_list if c.args]
        revoked = [c for c in calls if c.get("type") == "session_revoked"]
        assert len(revoked) >= 1
        assert revoked[0]["reason"] == "single_session_enforced"
        os.environ.pop("PROXION_SINGLE_SESSION", None)

    def test_single_session_mode_noop_when_disabled(self, gateway):
        os.environ.pop("PROXION_SINGLE_SESSION", None)
        identity = "did:key:z6MkTestIdentityB"
        ws1 = make_ws()
        ws2 = make_ws()
        gateway.clients.add(ws1)
        gateway._client_webids[ws1] = identity
        gateway._webid_sockets[identity] = {ws1}
        gateway._session_meta[ws1] = {"session_id": "s1", "connected_at": "", "ip_addr": "127.0.0.1"}
        gateway.clients.add(ws2)
        gateway._session_meta[ws2] = {"session_id": "s2", "connected_at": "", "ip_addr": "127.0.0.1"}
        run(gateway._handle_register(ws2, {
            "did": identity, "webid": "", "display_name": "", "gateway_url": "",
        }))
        # ws1 should NOT have received session_revoked
        calls = [json.loads(c.args[0]) for c in ws1.send.call_args_list if c.args]
        revoked = [c for c in calls if c.get("type") == "session_revoked"]
        assert len(revoked) == 0

    def test_revoked_session_receives_reason_single_session_enforced(self, gateway):
        os.environ["PROXION_SINGLE_SESSION"] = "1"
        identity = "did:key:z6MkTestIdentityC"
        ws1 = make_ws()
        ws2 = make_ws()
        gateway.clients.add(ws1)
        gateway._client_webids[ws1] = identity
        gateway._webid_sockets[identity] = {ws1}
        gateway._session_meta[ws1] = {"session_id": "s1", "connected_at": "", "ip_addr": "127.0.0.1"}
        gateway.clients.add(ws2)
        gateway._session_meta[ws2] = {"session_id": "s2", "connected_at": "", "ip_addr": "127.0.0.1"}
        run(gateway._handle_register(ws2, {
            "did": identity, "webid": "", "display_name": "", "gateway_url": "",
        }))
        calls = [json.loads(c.args[0]) for c in ws1.send.call_args_list if c.args]
        revoked = [c for c in calls if c.get("type") == "session_revoked"]
        assert revoked[0]["reason"] == "single_session_enforced"
        os.environ.pop("PROXION_SINGLE_SESSION", None)
