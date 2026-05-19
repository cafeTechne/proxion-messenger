"""Tests for `proxion store mailbox peek/list/drain` CLI subcommands."""

from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.store import StoredMessage

runner = CliRunner()
_REMOTE = "proxion_messenger_core.store_client.RemoteStore"

MAILBOX = "ab" * 32
STORE_URL = "http://localhost:8765"


def _fake_stored_message(msg_id="msg1", age_s=120.0, ct_len=64):
    import time
    from proxion_messenger_core.sealed import SealedEnvelope
    env = MagicMock(spec=SealedEnvelope)
    env.ciphertext = b"x" * ct_len
    sm = StoredMessage(
        message_id=msg_id,
        envelope=env,
        posted_at=time.time() - age_s,
    )
    return sm


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------

def test_mailbox_peek_shows_count_and_bytes():
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.peek.return_value = {"count": 5, "bytes": 1024, "oldest_age_s": 60.0}
        instance.close.return_value = None
        result = runner.invoke(app, ["store", "mailbox", "peek", MAILBOX, STORE_URL])
    assert result.exit_code == 0
    assert "5" in result.output
    assert "1024" in result.output


def test_mailbox_peek_shows_oldest_age():
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.peek.return_value = {"count": 1, "bytes": 50, "oldest_age_s": 42.5}
        instance.close.return_value = None
        result = runner.invoke(app, ["store", "mailbox", "peek", MAILBOX, STORE_URL])
    assert result.exit_code == 0
    assert "42" in result.output


def test_mailbox_peek_empty_shows_empty():
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.peek.return_value = {"count": 0, "bytes": 0, "oldest_age_s": None}
        instance.close.return_value = None
        result = runner.invoke(app, ["store", "mailbox", "peek", MAILBOX, STORE_URL])
    assert result.exit_code == 0
    assert "empty" in result.output.lower() or "0" in result.output


def test_mailbox_peek_error_exits_1():
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.peek.side_effect = ConnectionError("refused")
        instance.close.return_value = None
        result = runner.invoke(app, ["store", "mailbox", "peek", MAILBOX, STORE_URL])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_mailbox_list_shows_message_ids():
    sm = _fake_stored_message("deadbeef-1234", age_s=30.0)
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = [sm]
        instance.close.return_value = None
        result = runner.invoke(app, ["store", "mailbox", "list", MAILBOX, STORE_URL])
    assert result.exit_code == 0
    assert "deadbeef" in result.output


def test_mailbox_list_empty_mailbox():
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = []
        instance.close.return_value = None
        result = runner.invoke(app, ["store", "mailbox", "list", MAILBOX, STORE_URL])
    assert result.exit_code == 0
    assert "empty" in result.output.lower()


def test_mailbox_list_shows_count():
    msgs = [_fake_stored_message(f"msg-{i}") for i in range(3)]
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = msgs
        instance.close.return_value = None
        result = runner.invoke(app, ["store", "mailbox", "list", MAILBOX, STORE_URL])
    assert result.exit_code == 0
    assert "3" in result.output


# ---------------------------------------------------------------------------
# drain
# ---------------------------------------------------------------------------

def test_mailbox_drain_with_yes_flag():
    msgs = [_fake_stored_message("msg1"), _fake_stored_message("msg2")]
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.take_all.return_value = msgs
        instance.close.return_value = None
        result = runner.invoke(app, [
            "store", "mailbox", "drain", MAILBOX, STORE_URL, "--yes"
        ])
    assert result.exit_code == 0
    assert "2" in result.output
    instance.take_all.assert_called_once()


def test_mailbox_drain_empty():
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.take_all.return_value = []
        instance.close.return_value = None
        result = runner.invoke(app, [
            "store", "mailbox", "drain", MAILBOX, STORE_URL, "--yes"
        ])
    assert result.exit_code == 0
    assert "0" in result.output


def test_mailbox_drain_cancelled_by_user():
    """Without --yes and user types 'n', no drain occurs."""
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.take_all.return_value = []
        instance.close.return_value = None
        # Inject 'n' as stdin input for the confirmation prompt
        result = runner.invoke(app, [
            "store", "mailbox", "drain", MAILBOX, STORE_URL
        ], input="n\n")
    assert result.exit_code == 0
    assert "Cancelled" in result.output
    instance.take_all.assert_not_called()
