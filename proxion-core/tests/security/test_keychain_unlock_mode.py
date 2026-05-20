"""R17: OS keychain unlock mode for AgentState.save / load."""
import os
import pytest
from unittest.mock import patch, MagicMock

from proxion_messenger_core.persist import AgentState, PersistError
from proxion_messenger_core.keychain_store import (
    store_wrap_key, load_wrap_key, delete_wrap_key, is_keychain_available,
    SERVICE_NAME,
)


def _make_mock_keyring(store: dict):
    """Return a mock keyring module backed by a plain dict."""
    kr = MagicMock()
    kr.get_keyring.return_value = MagicMock(__class__=type("FakeBackend", (), {}))

    def _set(service, username, password):
        store[(service, username)] = password
    def _get(service, username):
        return store.get((service, username))
    def _delete(service, username):
        store.pop((service, username), None)

    kr.set_password.side_effect = _set
    kr.get_password.side_effect = _get
    kr.delete_password.side_effect = _delete
    return kr


def test_keychain_mode_stores_and_loads_wrap_key(tmp_path):
    vault: dict = {}
    kr = _make_mock_keyring(vault)

    with patch("proxion_messenger_core.keychain_store._import_keyring", return_value=kr):
        state = AgentState.generate()
        path = tmp_path / "agent.json"
        state.save(path, unlock_mode="keychain", identity_id="test-id")
        assert len(vault) == 1

        loaded = AgentState.load(path)
        assert loaded.identity_pub_bytes == state.identity_pub_bytes


def test_keychain_unavailable_falls_back_to_passphrase_mode(tmp_path):
    state = AgentState.generate()
    path = tmp_path / "agent.json"
    passphrase = b"strongpassphrase!"
    state.save(path, passphrase)
    loaded = AgentState.load(path, passphrase)
    assert loaded.identity_pub_bytes == state.identity_pub_bytes


def test_keychain_delete_revokes_silent_unlock(tmp_path):
    vault: dict = {}
    kr = _make_mock_keyring(vault)

    with patch("proxion_messenger_core.keychain_store._import_keyring", return_value=kr):
        state = AgentState.generate()
        path = tmp_path / "agent.json"
        state.save(path, unlock_mode="keychain", identity_id="test-id-del")
        assert len(vault) == 1

        vault.clear()

        with pytest.raises(PersistError, match="keychain wrap key not found"):
            AgentState.load(path)
