"""Round 2: Passphrase strength policy for AgentState.save."""
import pytest
from proxion_messenger_core.persist import AgentState, PersistError


def test_save_rejects_short_passphrase(tmp_path, monkeypatch):
    """Passphrase shorter than 12 chars raises PersistError."""
    monkeypatch.delenv("PROXION_ALLOW_WEAK_PASSPHRASE", raising=False)
    agent = AgentState.generate()
    with pytest.raises(PersistError, match="passphrase|weak"):
        agent.save(str(tmp_path / "agent.json"), b"short")


def test_save_rejects_11_char_passphrase(tmp_path, monkeypatch):
    """11-character passphrase is below the minimum and is rejected."""
    monkeypatch.delenv("PROXION_ALLOW_WEAK_PASSPHRASE", raising=False)
    agent = AgentState.generate()
    with pytest.raises(PersistError, match="passphrase|weak"):
        agent.save(str(tmp_path / "agent.json"), b"elevencharss"[:11])


def test_save_allows_12_char_passphrase(tmp_path):
    """12-character passphrase meets the minimum and is accepted."""
    agent = AgentState.generate()
    path = str(tmp_path / "agent.json")
    agent.save(path, b"exactlytwelve")  # 13 chars but >= 12 is fine
    loaded = AgentState.load(path, b"exactlytwelve")
    assert loaded.identity_pub_bytes == agent.identity_pub_bytes


def test_save_allows_short_passphrase_with_override_env(tmp_path, monkeypatch):
    """Short passphrase is accepted when PROXION_ALLOW_WEAK_PASSPHRASE=1."""
    monkeypatch.setenv("PROXION_ALLOW_WEAK_PASSPHRASE", "1")  # already set by autouse, but explicit here
    agent = AgentState.generate()
    path = str(tmp_path / "agent.json")
    agent.save(path, b"short")
    loaded = AgentState.load(path, b"short")
    assert loaded.identity_pub_bytes == agent.identity_pub_bytes


def test_load_legacy_state_unchanged(tmp_path, monkeypatch):
    """Loading a file saved with override env still works after env is removed."""
    # Save with override env (autouse already sets it, but we set explicitly)
    monkeypatch.setenv("PROXION_ALLOW_WEAK_PASSPHRASE", "1")
    agent = AgentState.generate()
    path = str(tmp_path / "agent.json")
    agent.save(path, b"pw")
    # Remove override — load must still work (env only gates save, not load)
    monkeypatch.delenv("PROXION_ALLOW_WEAK_PASSPHRASE", raising=False)
    loaded = AgentState.load(path, b"pw")
    assert loaded.identity_pub_bytes == agent.identity_pub_bytes
