"""Tests for `proxion agent backup` / `proxion agent restore-identity` (E1)."""

import json
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.persist import AgentState

PASSPHRASE = "backup-cli-test"
KIT_CODE = "ABCD-EFGH-JKMN-PQRS-TVWX"
runner = CliRunner()


def _make_state(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return agent, p


def _backup(state_path, out_path):
    return runner.invoke(app, [
        "agent", "backup",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
        "--backup-passphrase", KIT_CODE,
        "--output", str(out_path),
    ])


def test_backup_writes_kit(tmp_path):
    _, p = _make_state(tmp_path)
    kit = tmp_path / "kit.json"
    result = _backup(p, kit)
    assert result.exit_code == 0
    obj = json.loads(kit.read_text(encoding="utf-8"))
    assert obj["@type"] == "ProxionBackup"
    assert obj["backup_mode"] == "passphrase"


def test_backup_restore_roundtrip(tmp_path):
    agent, p = _make_state(tmp_path)
    kit = tmp_path / "kit.json"
    assert _backup(p, kit).exit_code == 0

    restored_state = tmp_path / "restored" / "agent.json"
    result = runner.invoke(app, [
        "agent", "restore-identity", str(kit),
        "--state", str(restored_state),
        "--passphrase", PASSPHRASE,
        "--backup-passphrase", KIT_CODE,
    ])
    assert result.exit_code == 0, result.output
    restored = AgentState.load(restored_state, PASSPHRASE.encode())
    assert restored.identity_pub_bytes == agent.identity_pub_bytes
    assert restored.store_pub_bytes == agent.store_pub_bytes


def test_restore_wrong_code_fails(tmp_path):
    _, p = _make_state(tmp_path)
    kit = tmp_path / "kit.json"
    assert _backup(p, kit).exit_code == 0

    result = runner.invoke(app, [
        "agent", "restore-identity", str(kit),
        "--state", str(tmp_path / "restored.json"),
        "--passphrase", PASSPHRASE,
        "--backup-passphrase", "WRONG-CODE-0000-0000-0000",
    ])
    assert result.exit_code == 1
    assert "Restore failed" in result.output


def test_restore_missing_file_fails(tmp_path):
    result = runner.invoke(app, [
        "agent", "restore-identity", str(tmp_path / "nope.json"),
        "--state", str(tmp_path / "restored.json"),
        "--passphrase", PASSPHRASE,
        "--backup-passphrase", KIT_CODE,
    ])
    assert result.exit_code == 1
    assert "no such file" in result.output


def test_restore_existing_state_requires_confirmation(tmp_path):
    _, p = _make_state(tmp_path)
    kit = tmp_path / "kit.json"
    assert _backup(p, kit).exit_code == 0

    # Restoring over the existing state file without --force prompts; answer "n".
    result = runner.invoke(app, [
        "agent", "restore-identity", str(kit),
        "--state", str(p),
        "--passphrase", PASSPHRASE,
        "--backup-passphrase", KIT_CODE,
    ], input="n\n")
    assert result.exit_code == 1

    # With --force it proceeds without prompting.
    result = runner.invoke(app, [
        "agent", "restore-identity", str(kit),
        "--state", str(p),
        "--passphrase", PASSPHRASE,
        "--backup-passphrase", KIT_CODE,
        "--force",
    ])
    assert result.exit_code == 0, result.output
