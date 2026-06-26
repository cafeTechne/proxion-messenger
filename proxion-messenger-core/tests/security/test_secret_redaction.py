"""Round 3: Sensitive data redaction helpers and webhook secret isolation."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gw(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9890, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


def test_redact_dict_masks_sensitive_keys(gw):
    """_redact_dict replaces sensitive key values with '<redacted>'."""
    data = {
        "username": "alice",
        "passphrase": "supersecret",
        "token": "abc123",
        "message": "hello",
    }
    redacted = gw._redact_dict(data)
    assert redacted["username"] == "alice"
    assert redacted["message"] == "hello"
    assert redacted["passphrase"] == "<redacted>"
    assert redacted["token"] == "<redacted>"


@pytest.mark.asyncio
async def test_webhook_list_never_returns_secret_material(gw, tmp_path):
    """list_webhooks response never includes token or secret_token fields."""
    from proxion_messenger_core.didkey import pub_key_to_did
    owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = owner_did

    room_id = "room-webhooks-test"
    gw._local_rooms[room_id] = {
        "name": "Test", "code": "x" * 64, "members": {ws},
        "invite_url": "", "history_mode": "none", "messages": [],
        "creator_webid": owner_did,
    }

    # Create a webhook
    await gw.process_command(ws, {
        "cmd": "create_webhook",
        "thread_id": room_id,
        "direction": "incoming",
        "bot_name": "TestBot",
    })

    # Now list webhooks
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "list_webhooks", "thread_id": room_id})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "webhook_list"
    for hook in resp.get("webhooks", []):
        assert "token" not in hook, f"token must not be in list: {hook}"
        assert "secret" not in hook, f"secret must not be in list: {hook}"
        assert "secret_token" not in hook, f"secret_token must not be in list: {hook}"


def test_redact_dict_case_insensitive(gw):
    """_redact_dict keys check is case-insensitive."""
    data = {"Authorization": "Bearer xyz", "Content-Type": "application/json"}
    redacted = gw._redact_dict(data)
    assert redacted["Authorization"] == "<redacted>"
    assert redacted["Content-Type"] == "application/json"
