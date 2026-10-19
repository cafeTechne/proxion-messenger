"""Tests for strict JSON scalar type validation (Round 5)."""
import pytest
from proxion_messenger_core.command_validation import validate_command_payload, SchemaError


class TestStrictScalarValidation:
    def test_reject_float_for_integer_fields(self):
        """A float is refused for an integer field even when it is whole."""
        base = {"cert_id": "abc", "content": "hi"}
        validate_command_payload("send_dm", {**base, "seq_num": 3})
        with pytest.raises(SchemaError, match="seq_num: must be integer, not float"):
            validate_command_payload("send_dm", {**base, "seq_num": 3.0})

    def test_bool_rejected_for_integers(self):
        """True is an int in Python; the validator must still refuse it."""
        with pytest.raises(SchemaError, match="seq_num: must be integer, not bool"):
            validate_command_payload("send_dm", {"cert_id": "abc", "content": "hi", "seq_num": True})

    def test_reject_non_bool_enabled_toggle(self):
        """A toggle takes a real boolean, not 1 or "true"."""
        base = {"cert_id": "abc", "content": "hi"}
        validate_command_payload("send_dm", {**base, "enabled": False})
        for bad in (1, "true"):
            with pytest.raises(SchemaError, match="enabled: must be boolean"):
                validate_command_payload("send_dm", {**base, "enabled": bad})

    def test_accept_valid_schema_command(self):
        """Test that commands with valid payloads pass validation."""
        # Should not raise
        validate_command_payload("send_dm", {"cert_id": "abc", "content": "hi"})
