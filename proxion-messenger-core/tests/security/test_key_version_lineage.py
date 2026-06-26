"""Tests for key version tracking in AgentState (Round 5)."""
import pytest
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def state():
    return AgentState.generate()


class TestKeyVersionLineage:
    def test_initial_identity_version_is_1(self, state):
        assert state.identity_key_version == 1

    def test_initial_store_version_is_1(self, state):
        assert state.store_key_version == 1

    def test_rotate_identity_increments_version(self, state):
        state.rotate_identity_key()
        assert state.identity_key_version == 2

    def test_rotate_store_increments_version(self, state):
        state.rotate_store_key()
        assert state.store_key_version == 2

    def test_rotate_identity_sets_timestamp(self, state):
        import time
        before = time.time()
        state.rotate_identity_key()
        after = time.time()
        assert state.identity_key_rotated_at is not None
        assert before <= state.identity_key_rotated_at <= after

    def test_rotate_store_sets_timestamp(self, state):
        import time
        before = time.time()
        state.rotate_store_key()
        after = time.time()
        assert state.store_key_rotated_at is not None
        assert before <= state.store_key_rotated_at <= after

    def test_state_persists_and_loads_key_versions(self, tmp_path):
        state = AgentState.generate()
        state.rotate_identity_key()
        state.rotate_store_key()
        path = str(tmp_path / "agent.json")
        state.save(path, b"test-passphrase-ok")
        loaded = AgentState.load(path, b"test-passphrase-ok")
        assert loaded.identity_key_version == 2
        assert loaded.store_key_version == 2
        assert loaded.identity_key_rotated_at is not None
        assert loaded.store_key_rotated_at is not None
