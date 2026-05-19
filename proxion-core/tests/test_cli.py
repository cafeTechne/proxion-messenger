"""CLI tests for new Batch A commands: did subgroup, chat room search, chat dm list."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.persist import AgentState

runner = CliRunner()
PASSPHRASE = "test-passphrase"


@pytest.fixture
def alice(tmp_path):
    """An AgentState saved to a temp file."""
    agent = AgentState.generate()
    state_file = tmp_path / "alice.json"
    agent.save(state_file, PASSPHRASE.encode())
    return agent, state_file


# ==============================================================================
# proxion did show
# ==============================================================================

def test_cli_did_show(alice):
    """proxion did show should print the agent's DID."""
    agent, state_file = alice
    
    result = runner.invoke(app, [
        "did", "show",
        "--state", str(state_file),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code == 0, result.output
    # DID format: did:key:z...
    assert result.stdout.strip().startswith("did:key:")


# ==============================================================================
# proxion chat dm list
# ==============================================================================

def test_cli_chat_dm_list_no_conversations(alice):
    """proxion chat dm list with no conversations should print message."""
    agent, state_file = alice
    
    result = runner.invoke(app, [
        "chat", "dm", "list",
        "--state", str(state_file),
    ])
    # The command should succeed
    assert result.exit_code == 0, result.output
    assert "No DM conversations" in result.stdout


def test_cli_chat_dm_list_with_did_flag(alice):
    """proxion chat dm list --with-did should accept the flag without error."""
    agent, state_file = alice
    
    result = runner.invoke(app, [
        "chat", "dm", "list",
        "--with-did",
        "--state", str(state_file),
    ])
    # The command should succeed with the flag
    assert result.exit_code == 0, result.output


# ==============================================================================
# Additional smoke tests
# ==============================================================================

def test_cli_did_app_has_subcommands():
    """proxion did --help should show the subcommands."""
    result = runner.invoke(app, ["did", "--help"])
    assert result.exit_code == 0
    assert "show" in result.stdout
    assert "resolve" in result.stdout
    assert "peers" in result.stdout
    assert "trust" in result.stdout


def test_cli_chat_room_search_help():
    """proxion chat room search --help should show help."""
    result = runner.invoke(app, ["chat", "room", "search", "--help"])
    assert result.exit_code == 0
    assert "search" in result.stdout.lower()


# ==============================================================================
# proxion chat room leave  (R27-G)
# ==============================================================================

def test_cli_chat_room_leave_unknown(alice):
    """proxion chat room leave for an unknown room exits non-zero."""
    _, state_file = alice
    result = runner.invoke(app, [
        "chat", "room", "leave", "nonexistent-room-id",
        "--state", str(state_file),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code != 0


# ==============================================================================
# proxion chat dm delete  (R27-G)
# ==============================================================================

def test_cli_chat_dm_delete_unknown(alice):
    """proxion chat dm delete for a missing cert ID exits non-zero."""
    _, state_file = alice
    result = runner.invoke(app, [
        "chat", "dm", "delete", "cert-id-that-does-not-exist",
        "--state", str(state_file),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code != 0


# ==============================================================================
# proxion status --json  (R27-G)
# ==============================================================================

def test_cli_status_json(alice):
    """proxion status --json should output valid JSON with expected keys."""
    _, state_file = alice
    result = runner.invoke(app, [
        "status", "--json",
        "--state", str(state_file),
        "--passphrase", PASSPHRASE,
    ])
    # May exit 0 or 1 depending on CSS/gateway availability; just check JSON shape
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        pytest.fail(f"Output is not valid JSON: {result.stdout!r}")
    assert "agent_ok" in data
    assert "pod_ok" in data
    assert "gateway_ok" in data
    assert "cert_count" in data
    assert "room_count" in data
