"""Tests that invalid hole punch state transitions are rejected."""
import pytest

from proxion_messenger_core.hole_punch import (
    is_valid_punch_transition,
    PUNCH_STATE_PENDING,
    PUNCH_STATE_OFFERED,
    PUNCH_STATE_ACCEPTED,
    PUNCH_STATE_SUCCEEDED,
    PUNCH_STATE_FAILED,
    PUNCH_STATE_EXPIRED,
)


def test_valid_pending_to_offered():
    assert is_valid_punch_transition(PUNCH_STATE_PENDING, PUNCH_STATE_OFFERED) is True


def test_valid_pending_to_failed():
    assert is_valid_punch_transition(PUNCH_STATE_PENDING, PUNCH_STATE_FAILED) is True


def test_valid_pending_to_expired():
    assert is_valid_punch_transition(PUNCH_STATE_PENDING, PUNCH_STATE_EXPIRED) is True


def test_valid_offered_to_accepted():
    assert is_valid_punch_transition(PUNCH_STATE_OFFERED, PUNCH_STATE_ACCEPTED) is True


def test_valid_offered_to_failed():
    assert is_valid_punch_transition(PUNCH_STATE_OFFERED, PUNCH_STATE_FAILED) is True


def test_valid_accepted_to_succeeded():
    assert is_valid_punch_transition(PUNCH_STATE_ACCEPTED, PUNCH_STATE_SUCCEEDED) is True


def test_valid_accepted_to_failed():
    assert is_valid_punch_transition(PUNCH_STATE_ACCEPTED, PUNCH_STATE_FAILED) is True


def test_invalid_pending_to_accepted():
    assert is_valid_punch_transition(PUNCH_STATE_PENDING, PUNCH_STATE_ACCEPTED) is False


def test_invalid_pending_to_succeeded():
    assert is_valid_punch_transition(PUNCH_STATE_PENDING, PUNCH_STATE_SUCCEEDED) is False


def test_invalid_offered_to_succeeded():
    assert is_valid_punch_transition(PUNCH_STATE_OFFERED, PUNCH_STATE_SUCCEEDED) is False


def test_invalid_from_terminal_succeeded():
    assert is_valid_punch_transition(PUNCH_STATE_SUCCEEDED, PUNCH_STATE_OFFERED) is False


def test_invalid_from_terminal_failed():
    assert is_valid_punch_transition(PUNCH_STATE_FAILED, PUNCH_STATE_ACCEPTED) is False


def test_invalid_from_terminal_expired():
    assert is_valid_punch_transition(PUNCH_STATE_EXPIRED, PUNCH_STATE_PENDING) is False


def test_invalid_to_pending_from_any():
    for state in (PUNCH_STATE_OFFERED, PUNCH_STATE_ACCEPTED):
        assert is_valid_punch_transition(state, PUNCH_STATE_PENDING) is False
