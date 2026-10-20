"""The MCP server's tool layer (mcp_server.build_server), driven through FastMCP
against a real gateway. tests/e2e/test_mcp_gateway_client.py covers the
transport; this covers what an MCP host actually sees: the tool list, the
identity and gateway settings read from the environment, and tool calls
reaching the gateway."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")
pytest.importorskip("websockets")

from gwharness import start_gateway
from proxion_messenger_core import mcp_server
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

EXPECTED_TOOLS = {
    "whoami", "list_rooms", "list_direct_messages", "create_room", "join_room",
    "send_room_message", "send_direct_message", "read_history", "search_messages",
    "edit_message", "delete_message", "react_to_message", "get_room_members",
}


def _result(ret, many=False):
    """The tool's return value. FastMCP sends a list return as one text block per
    item and anything else as a single block, so list tools pass many=True."""
    if isinstance(ret, tuple):            # newer mcp: (content blocks, structured)
        content, structured = ret
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        ret = content
    items = [json.loads(b.text) for b in ret]
    return items if many else items[0]


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    gw = ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", port=0, db_path=str(tmp_path / "mcp.db"),
                             http_port=None),
    )
    handle = start_gateway(gw, 0, 0, serve_http=False)
    return f"ws://127.0.0.1:{handle.ws_port}"


def test_gateway_cafile_prefers_explicit_then_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("PROXION_GATEWAY_CAFILE", raising=False)
    monkeypatch.delenv("PROXION_DATA_DIR", raising=False)
    assert mcp_server._gateway_cafile() is None

    monkeypatch.setenv("PROXION_DATA_DIR", str(tmp_path))
    assert mcp_server._gateway_cafile() is None          # no cert written yet
    cert = tmp_path / "tls" / "cert.pem"
    cert.parent.mkdir()
    cert.write_text("pem")
    assert mcp_server._gateway_cafile() == str(cert)

    monkeypatch.setenv("PROXION_GATEWAY_CAFILE", "/explicit.pem")
    assert mcp_server._gateway_cafile() == "/explicit.pem"


def test_load_agent_uses_the_saved_identity(tmp_path, monkeypatch):
    saved = AgentState.generate()
    path = tmp_path / "agent.json"
    saved.save(str(path), b"pw")
    monkeypatch.setenv("PROXION_AGENT_STATE", str(path))
    monkeypatch.setenv("PROXION_AGENT_PASSPHRASE", "pw")
    assert mcp_server._load_agent().identity_pub_bytes == saved.identity_pub_bytes

    monkeypatch.delenv("PROXION_AGENT_STATE")
    a, b = mcp_server._load_agent(), mcp_server._load_agent()
    assert a.identity_pub_bytes != b.identity_pub_bytes  # ephemeral, fresh each time


@pytest.mark.asyncio
async def test_tools_reach_the_gateway_as_the_configured_identity(gateway, tmp_path, monkeypatch):
    agent = AgentState.generate()
    path = tmp_path / "agent.json"
    agent.save(str(path), b"pw")
    monkeypatch.setenv("PROXION_AGENT_STATE", str(path))
    monkeypatch.setenv("PROXION_AGENT_PASSPHRASE", "pw")
    monkeypatch.setenv("PROXION_GATEWAY_URL", gateway)
    monkeypatch.setenv("PROXION_AGENT_NAME", "Test Agent")

    server = mcp_server.build_server()
    assert {t.name for t in await server.list_tools()} == EXPECTED_TOOLS

    who = _result(await server.call_tool("whoami", {}))
    assert who["did"] == pub_key_to_did(agent.identity_pub_bytes)
    assert who["display_name"] == "Test Agent"

    room = _result(await server.call_tool("create_room", {"name": "MCP room"}))
    assert room["room_id"]
    rooms = _result(await server.call_tool("list_rooms", {}), many=True)
    assert any(r.get("id") == room["room_id"] for r in rooms)

    _result(await server.call_tool("send_room_message",
                                   {"room_id": room["room_id"], "text": "from the tool layer"}))
    history = _result(await server.call_tool("read_history",
                                             {"thread_id": room["room_id"], "limit": 10}), many=True)
    assert any(m.get("content") == "from the tool layer" for m in history)
