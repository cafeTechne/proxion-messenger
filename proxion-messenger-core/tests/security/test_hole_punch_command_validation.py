"""Tests that hole punch commands are wired into command_validation."""
import pytest

from proxion_messenger_core.command_validation import (
    MUTATING_COMMANDS,
    HEAVY_COMMANDS,
    validate_command_payload,
    SchemaError,
)


def test_request_hole_punch_in_schema():
    validate_command_payload("request_hole_punch", {"peer_webid": "did:web:bob.example"})


def test_request_hole_punch_missing_peer_webid_raises():
    with pytest.raises(SchemaError):
        validate_command_payload("request_hole_punch", {})


def test_request_hole_punch_peer_webid_too_long_raises():
    with pytest.raises(SchemaError):
        validate_command_payload("request_hole_punch", {"peer_webid": "a" * 257})


def test_hole_punch_complete_notify_in_schema():
    validate_command_payload(
        "hole_punch_complete_notify",
        {"attempt_id": "some-uuid", "result": "success"},
    )


def test_hole_punch_complete_notify_missing_attempt_id_raises():
    with pytest.raises(SchemaError):
        validate_command_payload("hole_punch_complete_notify", {"result": "success"})


def test_hole_punch_complete_notify_missing_result_raises():
    with pytest.raises(SchemaError):
        validate_command_payload("hole_punch_complete_notify", {"attempt_id": "abc"})


def test_request_hole_punch_in_mutating_commands():
    assert "request_hole_punch" in MUTATING_COMMANDS


def test_hole_punch_complete_notify_in_mutating_commands():
    assert "hole_punch_complete_notify" in MUTATING_COMMANDS


def test_request_hole_punch_in_heavy_commands():
    assert "request_hole_punch" in HEAVY_COMMANDS


def test_hole_punch_complete_notify_not_in_heavy_commands():
    assert "hole_punch_complete_notify" not in HEAVY_COMMANDS
