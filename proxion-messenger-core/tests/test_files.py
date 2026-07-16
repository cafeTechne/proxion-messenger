"""Unit tests for files.py — file sharing."""

import pytest
import json
import uuid
from unittest.mock import MagicMock, patch

from proxion_messenger_core.files import (
    FileAttachment,
    send_file, receive_files, download_file,
    _guess_mime_type,
)


@pytest.fixture
def mock_pod_client():
    """Mock SolidClient."""
    client = MagicMock()
    client._resolver = MagicMock()
    client._resolver.pod_base_url = "http://localhost:3001/alice/"
    return client


@pytest.fixture
def mock_cert():
    """Mock RelationshipCertificate."""
    cert = MagicMock()
    cert.certificate_id = uuid.uuid4().hex
    return cert


def test_guess_mime_type_png():
    """Test MIME type detection for PNG."""
    assert _guess_mime_type("image.png") == "image/png"


def test_guess_mime_type_pdf():
    """Test MIME type detection for PDF."""
    assert _guess_mime_type("document.pdf") == "application/pdf"


def test_guess_mime_type_unknown():
    """Test MIME type falls back to octet-stream.

    Deliberately nonsense extension: real-but-obscure ones (e.g. ``.xyz``)
    are registered in some OS mime databases (Ubuntu maps it to chemical/x-xyz).
    """
    assert _guess_mime_type("unknown.zq3vx") == "application/octet-stream"


def test_send_file_puts_and_sends(mock_pod_client, mock_cert, tmp_path):
    """Test send_file() uploads file and sends metadata message."""
    # Create a temp file
    test_file = tmp_path / "test.png"
    test_content = b"\x89PNG\x0D\x0A\x1A\x0A"
    test_file.write_bytes(test_content)
    
    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send") as mock_send:
        
        mock_msg = MagicMock()
        mock_compose.return_value = mock_msg
        
        attachment = send_file(
            mock_cert,
            mock_pod_client,
            str(test_file),
            mime_type="image/png",
        )
        
        assert attachment.filename == "test.png"
        assert attachment.mime_type == "image/png"
        assert attachment.size == len(test_content)
        assert attachment.stash_uri.startswith("stash://files/")
        
        # Verify PUT was called
        mock_pod_client.put.assert_called_once()
        put_args = mock_pod_client.put.call_args[0]
        assert put_args[1] == test_content


def test_send_file_auto_detects_mime_type(mock_pod_client, mock_cert, tmp_path):
    """Test send_file() auto-detects MIME type from extension."""
    test_file = tmp_path / "image.png"
    test_file.write_bytes(b"\x89PNG")
    
    with patch("proxion_messenger_core.messaging.compose"), \
         patch("proxion_messenger_core.messaging.send"):
        
        attachment = send_file(mock_cert, mock_pod_client, str(test_file))
        
        assert attachment.mime_type == "image/png"


def test_send_file_raises_on_missing_file(mock_pod_client, mock_cert):
    """Test send_file() raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        send_file(mock_cert, mock_pod_client, "/nonexistent/file.pdf")



def test_file_attachment_structure():
    """Test FileAttachment dataclass."""
    attachment = FileAttachment(
        filename="test.pdf",
        mime_type="application/pdf",
        size=1024,
        stash_uri="stash://files/abc123/test.pdf",
        message_stash_uri="stash://messages/msg-xyz",
    )
    
    assert attachment.filename == "test.pdf"
    assert attachment.size == 1024


def test_receive_files_filters_file_messages():
    """Test receive_files() filters for type='file' messages."""
    mock_cert = MagicMock()
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        # Create mock messages: one file, one non-file
        msg_file = MagicMock()
        msg_file.content = json.dumps({
            "type": "file",
            "filename": "doc.pdf",
            "mime_type": "application/pdf",
            "size": 2048,
            "stash_uri": "stash://files/xyz/doc.pdf",
        })
        msg_file.message_id = "msg-1"
        
        msg_text = MagicMock()
        msg_text.content = "Just a text message"
        
        mock_receive.return_value = [msg_file, msg_text]
        
        result = receive_files(mock_cert, mock_client, mock_agent, b"signing-key")
        
        # Should only get the file attachment
        assert len(result) == 1
        assert result[0].filename == "doc.pdf"
        assert result[0].mime_type == "application/pdf"


def test_receive_files_handles_malformed_messages():
    """Test receive_files() skips malformed JSON."""
    mock_cert = MagicMock()
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        msg_bad = MagicMock()
        msg_bad.content = "not valid json"
        
        msg_good = MagicMock()
        msg_good.content = json.dumps({
            "type": "file",
            "filename": "file.pdf",
            "mime_type": "application/pdf",
            "size": 100,
            "stash_uri": "stash://files/abc/file.pdf",
        })
        msg_good.message_id = "msg-2"
        
        mock_receive.return_value = [msg_bad, msg_good]
        
        result = receive_files(mock_cert, mock_client, mock_agent, b"key")
        
        # Should skip the bad message and get only the good one
        assert len(result) == 1
        assert result[0].filename == "file.pdf"


def test_download_file_gets_and_writes(mock_pod_client, tmp_path):
    """Test download_file() gets from pod and writes to disk."""
    attachment = FileAttachment(
        filename="test.pdf",
        mime_type="application/pdf",
        size=100,
        stash_uri="stash://files/xyz/test.pdf",
        message_stash_uri="msg-123",
    )
    
    test_content = b"Downloaded content"
    mock_pod_client.get.return_value = test_content
    
    dest = tmp_path / "downloaded.txt"
    
    download_file(attachment, mock_pod_client, str(dest))
    
    # Verify GET was called
    mock_pod_client.get.assert_called_once_with(attachment.stash_uri)
    
    # Verify file was written
    assert dest.read_bytes() == test_content


def test_receive_files_missing_fields_skipped():
    """Test receive_files() skips messages with missing required fields."""
    mock_cert = MagicMock()
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        msg_incomplete = MagicMock()
        msg_incomplete.content = json.dumps({
            "type": "file",
            "filename": "file.pdf",
            # Missing mime_type, size, etc.
        })
        
        mock_receive.return_value = [msg_incomplete]
        
        result = receive_files(mock_cert, mock_client, mock_agent, b"key")
        
        # Should skip due to missing fields (KeyError)
        assert len(result) == 0

def test_send_file_enforces_size_limit(mock_pod_client, mock_cert, tmp_path):
    """Test send_file() raises ValueError for files > 10MB."""
    test_file = tmp_path / "big.mp4"
    test_file.touch()
    
    with patch("os.path.getsize", return_value=11 * 1024 * 1024):
        with pytest.raises(ValueError, match="10MB limit"):
            send_file(mock_cert, mock_pod_client, str(test_file))

def test_send_file_enforces_mime_allowlist(mock_pod_client, mock_cert, tmp_path):
    """Test send_file() raises ValueError for disallowed MIME types."""
    test_file = tmp_path / "script.sh"
    test_file.write_bytes(b"echo hack")
    
    with pytest.raises(ValueError, match="Unsupported MIME"):
        send_file(mock_cert, mock_pod_client, str(test_file))

def test_receive_files_filters_invalid_attachments():
    """Test receive_files() ignores oversized or invalid MIME attachments."""
    mock_cert = MagicMock()
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        msg_oversized = MagicMock()
        msg_oversized.content = json.dumps({
            "type": "file",
            "filename": "big.mp4",
            "mime_type": "video/mp4",
            "size": 11 * 1024 * 1024,
            "stash_uri": "stash://files/xyz/big.mp4",
        })
        msg_oversized.message_id = "msg-over"

        msg_bad_mime = MagicMock()
        msg_bad_mime.content = json.dumps({
            "type": "file",
            "filename": "hack.sh",
            "mime_type": "application/x-sh",
            "size": 100,
            "stash_uri": "stash://files/xyz/hack.sh",
        })
        msg_bad_mime.message_id = "msg-bad"

        mock_receive.return_value = [msg_oversized, msg_bad_mime]
        
        result = receive_files(mock_cert, mock_client, mock_agent, b"key")
        
        assert len(result) == 0
