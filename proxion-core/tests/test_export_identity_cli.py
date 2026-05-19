"""Tests for `proxion agent export-identity` CLI command."""

import json
import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.sealed import mailbox_id_for

PASSPHRASE = "export-identity-test"
runner = CliRunner()


def _invoke(state_path, extra=None):
    return runner.invoke(app, [
        "agent", "export-identity",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ] + (extra or []))


def test_export_identity_valid_json(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    assert result.exit_code == 0
    # Should parse as valid JSON without raising
    card = json.loads(result.output)
    assert isinstance(card, dict)


def test_export_identity_has_required_fields(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    card = json.loads(result.output)
    for field in ("version", "identity_pub_hex", "store_pub_hex", "mailbox_id_hex"):
        assert field in card, f"Missing field: {field}"


def test_export_identity_pubkeys_match_state(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    card = json.loads(result.output)
    assert card["identity_pub_hex"] == agent.identity_pub_bytes.hex()
    assert card["store_pub_hex"] == agent.store_pub_bytes.hex()


def test_export_identity_mailbox_id_derived_correctly(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    card = json.loads(result.output)
    expected_mailbox_id = mailbox_id_for(agent.store_pub_bytes)
    assert card["mailbox_id_hex"] == expected_mailbox_id


def test_export_identity_to_file(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    output_file = tmp_path / "identity.json"
    result = _invoke(p, ["--output", str(output_file)])

    assert result.exit_code == 0
    assert output_file.exists()
    card_text = output_file.read_text(encoding="utf-8")
    card = json.loads(card_text)
    assert card["identity_pub_hex"] == agent.identity_pub_bytes.hex()
    assert card["store_pub_hex"] == agent.store_pub_bytes.hex()
