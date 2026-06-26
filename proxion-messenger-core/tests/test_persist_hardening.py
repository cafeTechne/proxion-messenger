import json
import pytest
from pathlib import Path
from proxion_messenger_core.persist import AgentState, PersistError
from proxion_messenger_core.room_store import RoomStore
from proxion_messenger_core.room import RoomConfig

PASSPHRASE = b"correct-horse-battery-staple"

@pytest.fixture
def agent():
    return AgentState.generate()

def test_agent_state_save_atomic_no_tmp_after_save(agent, tmp_path):
    path = tmp_path / "agent.json"
    agent.save(path, PASSPHRASE)
    
    assert path.exists()
    tmp = path.with_suffix(path.suffix + ".tmp")
    assert not tmp.exists()

def test_agent_state_save_creates_bak_on_second_save(agent, tmp_path):
    path = tmp_path / "agent.json"
    bak = path.with_suffix(path.suffix + ".bak")
    
    # First save
    agent.save(path, PASSPHRASE)
    assert not bak.exists()
    
    # Second save
    agent.save(path, PASSPHRASE)
    assert bak.exists()

def test_agent_state_load_falls_back_to_bak(agent, tmp_path):
    path = tmp_path / "agent.json"
    bak = path.with_suffix(path.suffix + ".bak")
    
    # Setup: save twice to get a backup
    agent.save(path, PASSPHRASE)
    orig_identity = agent.identity_pub_bytes
    agent.save(path, PASSPHRASE)
    
    assert path.exists()
    assert bak.exists()
    
    # Corrupt primary
    path.write_text("garbage", encoding="utf-8")
    
    # Load should fallback to bak
    loaded = AgentState.load(path, PASSPHRASE)
    assert loaded.identity_pub_bytes == orig_identity

def test_room_store_atomic_no_tmp_after_save(tmp_path):
    store = RoomStore(tmp_path)
    config = RoomConfig(
        room_id="room1",
        name="Test Room",
        owner_webid="alice",
        pod_url="http://pod",
        stash_root="stash://rooms/room1/",
        created_at="2024-01-01T00:00:00Z"
    )
    store.save_room(config)
    
    config_path = tmp_path / "room1" / "config.json"
    assert config_path.exists()
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    assert not tmp.exists()

def test_room_store_atomic_bak_exists(tmp_path):
    store = RoomStore(tmp_path)
    config = RoomConfig(
        room_id="room1",
        name="Test Room",
        owner_webid="alice",
        pod_url="http://pod",
        stash_root="stash://rooms/room1/",
        created_at="2024-01-01T00:00:00Z"
    )
    
    config_path = tmp_path / "room1" / "config.json"
    bak = config_path.with_suffix(config_path.suffix + ".bak")
    
    store.save_room(config)
    assert not bak.exists()
    
    store.save_room(config)
    assert bak.exists()
