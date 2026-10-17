"""Every dispatched WebSocket command is classified as mutating or read-only.

The revoked-identity, safe-mode and DB-integrity gates in process_command only
block commands in MUTATING_COMMANDS. This test parses the dispatcher so a new
command that nobody classified fails here instead of silently bypassing them.
"""
import ast
import inspect
import json
import os
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from proxion_messenger_core import command_validation as cv
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _dispatched_commands() -> set:
    src = textwrap.dedent(inspect.getsource(ProxionGateway.process_command))
    names = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "cmd" and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and isinstance(node.comparators[0], ast.Constant)):
            names.add(node.comparators[0].value)
    return names


def test_dispatcher_parse_finds_commands():
    cmds = _dispatched_commands()
    assert len(cmds) > 150
    assert {"send_dm", "chat_room_create", "get_rooms"} <= cmds


def test_every_dispatched_command_is_classified():
    cmds = _dispatched_commands()
    unclassified = cmds - cv.MUTATING_COMMANDS - cv.READ_ONLY_COMMANDS
    assert not unclassified, f"classify these in command_validation.py: {sorted(unclassified)}"


def test_classification_sets_are_disjoint_and_current():
    assert not (cv.MUTATING_COMMANDS & cv.READ_ONLY_COMMANDS)
    stale = (cv.MUTATING_COMMANDS | cv.READ_ONLY_COMMANDS) - _dispatched_commands()
    assert not stale, f"no longer dispatched: {sorted(stale)}"
    assert cv.TEARDOWN_COMMANDS <= cv.MUTATING_COMMANDS


@pytest.mark.parametrize("cmd", [
    "edit_local_message", "send_dm_fanout", "chat_room_create", "join_room",
    "ban_member", "mute_member", "unban_member", "accept_friend_request",
    "send_friend_request", "revoke_device_and_rekey", "set_identity",
    "start_tunnel", "rehost_room", "sealed_dm", "voice_invite",
])
def test_state_writing_commands_are_mutating(cmd):
    assert cmd in cv.MUTATING_COMMANDS
    assert cv.blocked_when_locked(cmd)


# ---------------------------------------------------------------------------
# Gate behaviour through process_command
# ---------------------------------------------------------------------------

@pytest.fixture
def gateway():
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9974), read_state=ReadState(),
    )


def _registered_ws(gw, did):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = did
    return ws


def _codes(ws):
    return [json.loads(c[0][0]).get("code") for c in ws.send.call_args_list]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"cmd": "chat_room_create", "name": "x"},
    {"cmd": "edit_local_message", "message_id": "m1", "content": "edited"},
    {"cmd": "join_room", "code": "ABC"},
    {"cmd": "set_identity", "display_name": "x"},
    {"cmd": "start_tunnel"},
])
async def test_revoked_identity_cannot_mutate(gateway, payload):
    ws = _registered_ws(gateway, "did:key:zRevoked1")
    gateway._revoked_dids.add("did:key:zRevoked1")
    await gateway.process_command(ws, dict(payload))
    assert _codes(ws) == ["E_REVOKED"]


@pytest.mark.asyncio
async def test_safe_mode_blocks_room_create(gateway):
    ws = _registered_ws(gateway, "did:key:zSafe1")
    with patch.dict(os.environ, {"PROXION_SAFE_MODE": "1"}):
        await gateway.process_command(ws, {"cmd": "chat_room_create", "name": "x"})
    assert _codes(ws) == ["E_SAFE_MODE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"cmd": "get_rooms"},
    {"cmd": "stop_tunnel"},
    {"cmd": "logout_all_devices"},
])
async def test_revoked_identity_can_still_read_and_tear_down(gateway, payload):
    ws = _registered_ws(gateway, "did:key:zRevoked2")
    gateway._revoked_dids.add("did:key:zRevoked2")
    with patch.dict(os.environ, {"PROXION_SAFE_MODE": "1"}):
        await gateway.process_command(ws, dict(payload))
    assert "E_REVOKED" not in _codes(ws)
    assert "E_SAFE_MODE" not in _codes(ws)
