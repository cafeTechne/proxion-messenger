"""Tests for proxion_messenger_core.solid_auth — capability-token-enforcing Solid client."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import secrets

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.context import RequestContext
from proxion_messenger_core.pop import fingerprint, make_challenge, sign_challenge
from proxion_messenger_core.solid_auth import AuthenticatedSolidClient
from proxion_messenger_core.solid_client import SolidClient, SolidError
from proxion_messenger_core.solid import SolidResolver
from proxion_messenger_core.tokens import issue_token


@pytest.fixture
def identity_key():
    """Generate a fresh Ed25519 private key for testing."""
    return Ed25519PrivateKey.generate()


@pytest.fixture
def signing_key():
    """Generate a test HMAC signing key."""
    return secrets.token_bytes(32)


@pytest.fixture
def resolver():
    return SolidResolver("https://alice.pod.test/")


@pytest.fixture
def mock_solid_client():
    """Create a mock SolidClient."""
    return MagicMock(spec=SolidClient)


@pytest.fixture
def token(identity_key, signing_key):
    """Create a token with read/write permissions."""
    holder_pub_bytes = identity_key.public_key().public_bytes_raw()
    from proxion_messenger_core.pop import fingerprint
    holder_fp = fingerprint(holder_pub_bytes)

    token = issue_token(
        permissions=[
            ("read", "stash://alice/shared/"),
            ("write", "stash://alice/shared/"),
        ],
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
        aud="test-aud",
        caveats=[],
        holder_key_fingerprint=holder_fp,
        signing_key=signing_key,
    )
    return token


def test_authenticated_solid_client_get_allowed(mock_solid_client, token, identity_key, signing_key):
    """get() succeeds when token permits the resource."""
    mock_solid_client.get.return_value = b"file content"

    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    data = client.get("stash://alice/shared/file.txt")
    assert data == b"file content"
    mock_solid_client.get.assert_called_once_with("stash://alice/shared/file.txt")


def test_authenticated_solid_client_get_denied(mock_solid_client, token, identity_key, signing_key):
    """get() raises PermissionError when token does not permit the resource."""
    # Token only permits read on stash://alice/shared/
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    with pytest.raises(PermissionError) as exc_info:
        client.get("stash://bob/private/file.txt")

    assert "denied" in str(exc_info.value).lower()
    mock_solid_client.get.assert_not_called()


def test_authenticated_solid_client_put_allowed(mock_solid_client, token, identity_key, signing_key):
    """put() succeeds when token permits the resource."""
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    client.put("stash://alice/shared/newfile.txt", b"new data", content_type="text/plain")
    mock_solid_client.put.assert_called_once_with(
        "stash://alice/shared/newfile.txt",
        b"new data",
        "text/plain",
    )


def test_authenticated_solid_client_put_denied(mock_solid_client, token, identity_key, signing_key):
    """put() raises PermissionError when token does not permit write."""
    # Token only allows write on stash://alice/shared/
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    with pytest.raises(PermissionError):
        client.put("stash://alice/private/file.txt", b"data")

    mock_solid_client.put.assert_not_called()


def test_authenticated_solid_client_delete_allowed(mock_solid_client, token, identity_key, signing_key):
    """delete() succeeds when token permits write (deletion requires write)."""
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    client.delete("stash://alice/shared/file.txt")
    mock_solid_client.delete.assert_called_once_with("stash://alice/shared/file.txt")


def test_authenticated_solid_client_delete_denied(mock_solid_client, token, identity_key, signing_key):
    """delete() raises PermissionError when token does not permit write."""
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    with pytest.raises(PermissionError):
        client.delete("stash://alice/private/file.txt")

    mock_solid_client.delete.assert_not_called()


def test_authenticated_solid_client_audience_mismatch(mock_solid_client, token, identity_key, signing_key):
    """_check_allowed fails when audience doesn't match token."""
    # Token is for "test-aud" but we pass "wrong-aud"
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="wrong-aud",
    )

    with pytest.raises(PermissionError) as exc_info:
        client.get("stash://alice/shared/file.txt")

    assert "denied" in str(exc_info.value).lower()


def test_authenticated_solid_client_expired_token(mock_solid_client, identity_key, signing_key):
    """_check_allowed fails when token is expired."""
    holder_pub_bytes = identity_key.public_key().public_bytes_raw()
    from proxion_messenger_core.pop import fingerprint
    holder_fp = fingerprint(holder_pub_bytes)

    # Issue the token in the past so it is already expired relative to "now"
    issued_at = datetime.now(timezone.utc) - timedelta(hours=2)
    exp = issued_at + timedelta(hours=1)  # expired 1 hour ago
    expired_token = issue_token(
        permissions=[("read", "stash://alice/shared/")],
        exp=exp,
        aud="test-aud",
        caveats=[],
        holder_key_fingerprint=holder_fp,
        signing_key=signing_key,
        now=issued_at,
    )

    client = AuthenticatedSolidClient(
        mock_solid_client,
        expired_token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    with pytest.raises(PermissionError) as exc_info:
        client.get("stash://alice/shared/file.txt")

    assert "denied" in str(exc_info.value).lower()


def test_authenticated_solid_client_hierarchical_permission(mock_solid_client, identity_key, signing_key):
    """get() succeeds when token permits a parent directory."""
    holder_pub_bytes = identity_key.public_key().public_bytes_raw()
    from proxion_messenger_core.pop import fingerprint
    holder_fp = fingerprint(holder_pub_bytes)

    # Token permits read on stash://alice/data/
    token = issue_token(
        permissions=[("read", "stash://alice/data/")],
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
        aud="test-aud",
        caveats=[],
        holder_key_fingerprint=holder_fp,
        signing_key=signing_key,
    )

    mock_solid_client.get.return_value = b"file content"

    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    # Should succeed because stash://alice/data/file.txt is under stash://alice/data/
    data = client.get("stash://alice/data/file.txt")
    assert data == b"file content"


def test_authenticated_solid_client_solid_error_propagates(mock_solid_client, token, identity_key, signing_key):
    """Underlying SolidError from get() propagates."""
    mock_solid_client.get.side_effect = SolidError("Network error", status_code=500)

    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )

    with pytest.raises(SolidError) as exc_info:
        client.get("stash://alice/shared/file.txt")

    assert exc_info.value.status_code == 500


def test_authenticated_solid_client_list_allowed(mock_solid_client, token, identity_key, signing_key):
    """list() succeeds when token permits read on the container."""
    mock_solid_client.list.return_value = ["stash://alice/shared/file.txt"]
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )
    members = client.list("stash://alice/shared/")
    assert members == ["stash://alice/shared/file.txt"]
    mock_solid_client.list.assert_called_once_with("stash://alice/shared/")


def test_authenticated_solid_client_list_denied(mock_solid_client, token, identity_key, signing_key):
    """list() raises PermissionError when container is outside token scope."""
    client = AuthenticatedSolidClient(
        mock_solid_client,
        token,
        identity_key,
        signing_key,
        aud="test-aud",
    )
    with pytest.raises(PermissionError):
        client.list("stash://alice/private/")
    mock_solid_client.list.assert_not_called()
