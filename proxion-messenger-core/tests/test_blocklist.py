"""Unit tests for Blocklist."""

import pytest
from proxion_messenger_core.blocklist import Blocklist

def test_blocklist_block_and_is_blocked(tmp_path):
    bl = Blocklist(str(tmp_path / "blocklist.json"))
    bl.block("alice@pod.example")
    
    assert bl.is_blocked("alice@pod.example") is True
    assert bl.is_blocked("bob@pod.example") is False

def test_blocklist_unblock(tmp_path):
    bl = Blocklist(str(tmp_path / "blocklist.json"))
    bl.block("alice@pod.example")
    bl.unblock("alice@pod.example")
    
    assert bl.is_blocked("alice@pod.example") is False

def test_blocklist_save_load_roundtrip(tmp_path):
    path = tmp_path / "blocklist.json"
    bl1 = Blocklist(str(path))
    bl1.block("alice@pod.example")
    bl1.block("charlie@pod.example")
    
    # Needs a fresh instance
    bl2 = Blocklist(str(path))
    assert bl2.is_blocked("alice@pod.example") is True
    assert bl2.is_blocked("charlie@pod.example") is True
    assert bl2.is_blocked("bob@pod.example") is False

def test_blocklist_unblock_nonexistent_is_noop(tmp_path):
    bl = Blocklist(str(tmp_path / "blocklist.json"))
    # Should not raise exception
    bl.unblock("never-blocked")
    assert bl.is_blocked("never-blocked") is False
