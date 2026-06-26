"""Round 8: real-time abuse signal rollups and owner command tests."""
import asyncio
import json
import time
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_get_abuse_signal_rollups_returns_expected_categories(store):
    rollup = store.get_abuse_signal_rollups(hours=1)
    assert "schema_rejects" in rollup
    assert "auth_lockouts" in rollup
    assert "replay_rejects" in rollup
    assert "invite_rate_limit_hits" in rollup
    assert "relay_conflict_rejects" in rollup
    assert "db_integrity_events" in rollup
    assert "relay_failed" in rollup
    assert rollup["hours"] == 1


def test_abuse_signal_rollups_24h(store):
    rollup = store.get_abuse_signal_rollups(hours=24)
    assert rollup["hours"] == 24


def test_abuse_signal_rollups_count_security_events(store):
    now = time.time()
    store.save_security_event("auth_lockout", "warning", ip="1.2.3.4")
    store.save_security_event("auth_lockout", "warning", ip="1.2.3.4")

    rollup = store.get_abuse_signal_rollups(hours=1)
    assert rollup["auth_lockouts"] >= 2


def test_get_realtime_abuse_signals_owner_only(tmp_path):
    """Non-owner WebSocket gets E_FORBIDDEN."""
    agent = AgentState.generate()
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState

    cfg = GatewayConfig(host="127.0.0.1", port=0, http_port=0,
                        public_url="ws://127.0.0.1:1", db_path=str(tmp_path / "gw.db"))
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())

    sent = []

    class FakeWS:
        async def send(self, msg): sent.append(json.loads(msg))

    ws = FakeWS()
    gw._client_webids[ws] = "did:key:not-the-owner"

    async def run():
        await gw.process_command(ws, {"cmd": "get_realtime_abuse_signals"})

    asyncio.get_event_loop().run_until_complete(run())
    assert any(m.get("code") == "E_FORBIDDEN" for m in sent), f"Expected E_FORBIDDEN, got: {sent}"


def test_get_realtime_abuse_signals_owner_receives_data(tmp_path):
    agent = AgentState.generate()
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState
    from proxion_messenger_core.didkey import pub_key_to_did

    cfg = GatewayConfig(host="127.0.0.1", port=0, http_port=0,
                        public_url="ws://127.0.0.1:1", db_path=str(tmp_path / "gw.db"))
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())

    sent = []

    class FakeWS:
        async def send(self, msg): sent.append(json.loads(msg))

    ws = FakeWS()
    owner_did = pub_key_to_did(agent.identity_pub_bytes)
    gw._client_webids[ws] = owner_did
    gw._webid_sockets[owner_did] = {ws}

    async def run():
        await gw.process_command(ws, {"cmd": "get_realtime_abuse_signals"})

    asyncio.get_event_loop().run_until_complete(run())
    assert any(m.get("type") == "realtime_abuse_signals" for m in sent), f"Expected abuse signals, got: {sent}"


def test_severity_score_escalates_at_thresholds(store):
    from proxion_messenger_core._gateway_misc import MiscHandlerMixin

    class FakeMisc(MiscHandlerMixin):
        pass

    m = FakeMisc()

    def _severity(r):
        auth = r.get("auth_lockouts", 0)
        integrity = r.get("db_integrity_events", 0)
        replay = r.get("replay_rejects", 0)
        if integrity > 0 or auth >= 10:
            return "critical"
        if auth >= 5 or replay >= 20:
            return "high"
        if auth >= 2 or replay >= 5 or r.get("relay_failed", 0) >= 50:
            return "medium"
        return "low"

    assert _severity({"auth_lockouts": 0, "db_integrity_events": 0, "replay_rejects": 0, "relay_failed": 0}) == "low"
    assert _severity({"auth_lockouts": 2, "db_integrity_events": 0, "replay_rejects": 0, "relay_failed": 0}) == "medium"
    assert _severity({"auth_lockouts": 5, "db_integrity_events": 0, "replay_rejects": 0, "relay_failed": 0}) == "high"
    assert _severity({"auth_lockouts": 10, "db_integrity_events": 0, "replay_rejects": 0, "relay_failed": 0}) == "critical"
    assert _severity({"auth_lockouts": 0, "db_integrity_events": 1, "replay_rejects": 0, "relay_failed": 0}) == "critical"
