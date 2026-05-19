"""Tests for WAC ACL functions in solid_client and solid_auth."""

from unittest.mock import MagicMock, patch
import types

import pytest

from proxion_messenger_core.solid import SolidResolver
from proxion_messenger_core.solid_client import SolidClient
from proxion_messenger_core.solid_auth import set_thread_read_acl


@pytest.fixture
def resolver():
    """SolidResolver for alice's pod."""
    return SolidResolver("http://localhost:3001/alice/")


@pytest.fixture
def mock_session():
    """Mock HTTP session."""
    return MagicMock()


@pytest.fixture
def client(resolver, mock_session):
    """SolidClient with mocked session."""
    mock_session.put.return_value = MagicMock(status_code=200)
    return SolidClient(resolver, session=mock_session)


class TestSetACL:
    """Tests for SolidClient.set_acl()"""

    def test_set_acl_puts_to_acl_url(self, client, mock_session):
        """set_acl() PUTs to the .acl URL."""
        client.set_acl(
            "stash://alice/messages/",
            "https://alice.pod/profile/card#me",
            "https://bob.pod/profile/card#me",
        )

        # Assert PUT was called with the correct ACL URL
        mock_session.put.assert_called_once()
        call_args = mock_session.put.call_args
        assert call_args[0][0] == "http://localhost:3001/alice/messages/.acl"

    def test_set_acl_turtle_content_type(self, client, mock_session):
        """set_acl() sends Content-Type: text/turtle header."""
        client.set_acl(
            "stash://alice/messages/",
            "https://alice.pod/profile/card#me",
            "https://bob.pod/profile/card#me",
        )

        call_args = mock_session.put.call_args
        headers = call_args[1]["headers"]
        assert headers["Content-Type"] == "text/turtle"

    def test_set_acl_turtle_has_owner_stanza(self, client, mock_session):
        """set_acl() generates a turtle document with owner stanza."""
        client.set_acl(
            "stash://alice/messages/",
            "https://alice.pod/profile/card#me",
            "https://bob.pod/profile/card#me",
        )

        call_args = mock_session.put.call_args
        content = call_args[1]["content"]
        turtle = content.decode("utf-8")

        # Check for owner stanza markers
        assert "#owner" in turtle
        assert "alice.pod" in turtle
        assert "acl:Control" in turtle

    def test_set_acl_turtle_has_subject_stanza(self, client, mock_session):
        """set_acl() generates turtle with subject stanza (Read only by default)."""
        client.set_acl(
            "stash://alice/messages/",
            "https://alice.pod/profile/card#me",
            "https://bob.pod/profile/card#me",
        )

        call_args = mock_session.put.call_args
        content = call_args[1]["content"]
        turtle = content.decode("utf-8")

        # Check for subject stanza markers
        assert "#subject" in turtle
        assert "bob.pod" in turtle
        # Default is Read only, no Write/Control for subject
        assert "acl:Read" in turtle

    def test_set_acl_custom_subject_modes(self, client, mock_session):
        """set_acl() with custom subject_modes includes those modes."""
        client.set_acl(
            "stash://alice/messages/",
            "https://alice.pod/profile/card#me",
            "https://bob.pod/profile/card#me",
            subject_modes=["Read", "Write"],
        )

        call_args = mock_session.put.call_args
        content = call_args[1]["content"]
        turtle = content.decode("utf-8")

        # Check that Write mode appears in subject stanza
        assert "acl:Write" in turtle


class TestSetThreadReadACL:
    """Tests for set_thread_read_acl()"""

    def test_set_thread_read_acl_calls_set_acl(self):
        """set_thread_read_acl() calls set_acl_auto with correct container path."""
        # Create a mock pod_client
        pod_client = MagicMock()

        # Create a mock cert with certificate_id
        cert = types.SimpleNamespace(certificate_id="cert-abc-123")

        # Mock set_acl_auto to return the expected .acl path
        with patch('proxion_messenger_core.acp.set_acl_auto') as mock_set_acl_auto:
            mock_set_acl_auto.return_value = "stash://messages/thread/cert-abc-123/.acl"
            with patch('proxion_messenger_core.messaging.thread_path', return_value='stash://messages/thread/cert-abc-123/'):
                # Call the function
                set_thread_read_acl(
                    pod_client,
                    cert,
                    "https://alice.pod/profile/card#me",
                    "https://bob.pod/profile/card#me",
                )

                # Assert set_acl_auto was called with the correct container path
                mock_set_acl_auto.assert_called_once()
                call_args = mock_set_acl_auto.call_args

                # First positional arg is the pod_client
                assert call_args[0][0] == pod_client

                # Second positional arg is the container path
                container_path = call_args[0][1]
                assert container_path == "stash://messages/thread/cert-abc-123/"

                # Check webids are passed
                owner_webid = call_args[0][2]
                subject_webid = call_args[0][3]
                assert owner_webid == "https://alice.pod/profile/card#me"
                assert subject_webid == "https://bob.pod/profile/card#me"

                # Check subject_modes kwarg is ["Read"]
                assert call_args[1]["subject_modes"] == ["Read"]

    def test_set_thread_read_acl_returns_acl_path(self):
        """set_thread_read_acl() returns the .acl path."""
        pod_client = MagicMock()
        cert = types.SimpleNamespace(certificate_id="cert-xyz-789")

        result = set_thread_read_acl(
            pod_client,
            cert,
            "https://alice.pod/profile/card#me",
            "https://bob.pod/profile/card#me",
        )

        # Result should end with .acl
        assert result.endswith(".acl")
        # Should contain the cert_id
        assert "cert-xyz-789" in result
