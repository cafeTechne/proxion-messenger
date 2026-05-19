"""Tests for schema_reject security event telemetry (Round 5)."""
import pytest
from proxion_messenger_core.command_validation import validate_command_payload, SchemaError
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


class TestSchemaRejectTelemetry:
    def test_schema_error_is_raised_for_invalid_payload(self):
        """Verify that SchemaError is raised for invalid payloads."""
        with pytest.raises(SchemaError):
            validate_command_payload("send_dm", {"cert_id": "x"})  # missing content

    def test_schema_error_includes_command_name(self):
        """Verify that SchemaError includes the command name."""
        with pytest.raises(SchemaError, match="send_dm"):
            validate_command_payload("send_dm", {"cert_id": "x"})

    def test_security_events_table_exists(self, store):
        """Verify that security_events table exists and can store events."""
        store.save_security_event("schema_reject", "info", details="cmd=send_dm reason=missing content")
        events = store.get_security_events(event_type="schema_reject")
        assert len(events) >= 1

    def test_security_event_records_command_in_details(self, store):
        """Verify that security event can record command name and reason."""
        store.save_security_event("schema_reject", "info", details="cmd=send_room reason=invalid room_id")
        events = store.get_security_events(event_type="schema_reject")
        assert any("send_room" in (e.get("details") or "") for e in events)
