"""Tests for PROXION_SAFE_MODE gating (Round 5)."""
import pytest
from proxion_messenger_core.command_validation import MUTATING_COMMANDS, validate_command_payload


class TestSafeModeControls:
    def test_mutating_commands_are_identified(self):
        """Verify that MUTATING_COMMANDS frozenset contains expected commands."""
        assert "send_dm" in MUTATING_COMMANDS
        assert "send_room" in MUTATING_COMMANDS
        assert "add_reaction" in MUTATING_COMMANDS
        assert "delete_room" in MUTATING_COMMANDS

    def test_read_only_commands_not_in_mutating_set(self):
        """Verify that read-only commands are not in MUTATING_COMMANDS."""
        assert "get_audit_logs" not in MUTATING_COMMANDS
        assert "get_security_events" not in MUTATING_COMMANDS
        assert "get_rooms" not in MUTATING_COMMANDS
        assert "get_identity" not in MUTATING_COMMANDS

    def test_send_dm_is_mutating(self):
        """Verify that send_dm is a mutating command."""
        assert "send_dm" in MUTATING_COMMANDS

    def test_send_room_is_mutating(self):
        """Verify that send_room is a mutating command."""
        assert "send_room" in MUTATING_COMMANDS
