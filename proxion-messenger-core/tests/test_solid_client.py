"""Tests for proxion_messenger_core.solid_client — HTTP client for Solid LDP resources."""

from unittest.mock import MagicMock, patch
import pytest

from proxion_messenger_core.solid import SolidResolver
from proxion_messenger_core.solid_client import SolidClient, SolidError


@pytest.fixture
def resolver():
    return SolidResolver("https://alice.pod.test/")


@pytest.fixture
def mock_session():
    return MagicMock()


def test_solid_client_init_with_session(resolver, mock_session):
    """SolidClient can be initialized with an existing session."""
    client = SolidClient(resolver, session=mock_session)
    assert client._session is mock_session
    assert client._owns_session is False


def test_solid_client_init_creates_session(resolver):
    """SolidClient creates an httpx session if none is provided."""
    client = SolidClient(resolver)
    assert client._session is not None
    assert client._owns_session is True
    client.close()


def test_solid_client_get_success(resolver, mock_session):
    """get() resolves URI and returns response body on success."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"hello world"
    mock_session.get.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    data = client.get("stash://alice/data/file.txt")

    assert data == b"hello world"
    mock_session.get.assert_called_once()


def test_solid_client_get_not_found(resolver, mock_session):
    """get() raises SolidError on 404."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.get.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    with pytest.raises(SolidError) as exc_info:
        client.get("stash://alice/data/missing.txt")

    assert exc_info.value.status_code == 404


def test_solid_client_get_invalid_uri(resolver, mock_session):
    """get() raises SolidError on invalid stash:// URI."""
    client = SolidClient(resolver, session=mock_session)
    with pytest.raises(SolidError):
        client.get("http://example.com/file.txt")


def test_solid_client_put_success(resolver, mock_session):
    """put() sends data with correct Content-Type on success."""
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_session.put.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    client.put("stash://alice/data/file.txt", b"test data", content_type="text/plain")

    call_args = mock_session.put.call_args
    assert call_args[1]["content"] == b"test data"
    assert call_args[1]["headers"]["Content-Type"] == "text/plain"


def test_solid_client_put_forbidden(resolver, mock_session):
    """put() raises SolidError on 403."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_session.put.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    with pytest.raises(SolidError) as exc_info:
        client.put("stash://alice/protected.txt", b"data")

    assert exc_info.value.status_code == 403


def test_solid_client_delete_success(resolver, mock_session):
    """delete() sends DELETE request on success."""
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_session.delete.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    client.delete("stash://alice/data/file.txt")

    mock_session.delete.assert_called_once()


def test_solid_client_delete_not_found(resolver, mock_session):
    """delete() raises SolidError on 404."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.delete.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    with pytest.raises(SolidError):
        client.delete("stash://alice/missing.txt")


def _stream_ctx(mock_response):
    """Wrap a mock response in a context manager as httpx.stream() returns."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_response)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_solid_client_list_success(resolver, mock_session):
    """list() parses member URIs from Turtle response."""
    turtle_content = (
        "@prefix ldp: <http://www.w3.org/ns/ldp#> .\n"
        "<https://alice.pod.test/data/> ldp:contains <https://alice.pod.test/data/file1.txt> .\n"
        "<https://alice.pod.test/data/> ldp:contains <https://alice.pod.test/data/file2.txt> .\n"
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_bytes.return_value = iter([turtle_content.encode()])
    mock_session.stream.return_value = _stream_ctx(mock_response)

    client = SolidClient(resolver, session=mock_session)
    members = client.list("stash://alice/data/")

    assert len(members) >= 2
    assert "stash://pod/data/file1.txt" in members
    assert "stash://pod/data/file2.txt" in members


def test_solid_client_list_no_slash(resolver, mock_session):
    """list() raises SolidError if URI doesn't end with /."""
    client = SolidClient(resolver, session=mock_session)
    with pytest.raises(SolidError) as exc_info:
        client.list("stash://alice/data")

    assert "directory uri" in str(exc_info.value).lower()


def test_solid_client_list_not_found(resolver, mock_session):
    """list() raises SolidError on 404."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.stream.return_value = _stream_ctx(mock_response)

    client = SolidClient(resolver, session=mock_session)
    with pytest.raises(SolidError):
        client.list("stash://alice/missing/")


def test_solid_client_list_retries_on_401(resolver, mock_session):
    """list() must refresh auth and retry once on 401 (like get/put/delete),
    otherwise an expired token silently breaks pod listing."""
    turtle = (
        "@prefix ldp: <http://www.w3.org/ns/ldp#> .\n"
        "<x> ldp:contains <https://alice.pod.test/data/f1.txt> .\n"
    )
    r401 = MagicMock(); r401.status_code = 401; r401.read = MagicMock()
    r200 = MagicMock(); r200.status_code = 200
    r200.iter_bytes.return_value = iter([turtle.encode()])
    mock_session.stream.side_effect = [_stream_ctx(r401), _stream_ctx(r200)]

    client = SolidClient(resolver, session=mock_session)
    refreshed = []
    client._refresh_auth = lambda resp=None: refreshed.append(True)

    members = client.list("stash://alice/data/")
    assert refreshed == [True], "should have refreshed auth on 401"
    assert mock_session.stream.call_count == 2, "should have retried after refresh"
    assert any("f1.txt" in m for m in members)


def test_solid_client_set_acl(resolver, mock_session):
    """set_acl() PUTs valid Turtle WAC to resource.acl URL."""
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_session.put.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    owner_webid = "https://alice.pod/profile/card#me"
    subject_webid = "https://bob.pod/profile/card#me"
    client.set_acl("stash://alice/data/file.txt", owner_webid, subject_webid, subject_modes=["Read", "Write"])

    call_args = mock_session.put.call_args
    assert ".acl" in call_args[0][0]
    content = call_args[1]["content"].decode("utf-8")
    assert owner_webid in content
    assert subject_webid in content
    assert "acl:Control" in content
    assert "#owner" in content
    assert "#subject" in content
    assert "acl:Read" in content
    assert "acl:Write" in content
    assert call_args[1]["headers"]["Content-Type"] == "text/turtle"


def test_solid_client_set_acl_no_modes(resolver, mock_session):
    """set_acl() uses default modes when subject_modes not provided."""
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_session.put.return_value = mock_response

    client = SolidClient(resolver, session=mock_session)
    client.set_acl("stash://alice/file.txt", "https://alice.pod/profile/card#me", "https://bob.pod/profile/card#me")

    call_args = mock_session.put.call_args
    content = call_args[1]["content"].decode("utf-8")
    assert "acl:Read" in content
    assert "#subject" in content


def test_solid_client_context_manager(resolver, mock_session):
    """SolidClient can be used as context manager."""
    client = SolidClient(resolver, session=mock_session)
    with client:
        pass
    # Should not raise


def test_solid_client_close(resolver):
    """SolidClient.close() closes owned session."""
    client = SolidClient(resolver)
    session = client._session
    client.close()
    # Session was closed (MagicMock won't show this, but real httpx.Client would)
    assert client._session is not None  # Reference still exists


def test_auth_headers_sent_on_get(mock_session, resolver):
    client = SolidClient(
        resolver,
        session=mock_session,
        auth_headers={"Authorization": "Bearer tok123"},
    )
    mock_session.get.return_value = MagicMock(status_code=200, content=b"ok")
    client.get("stash://pod/file.txt")
    call_headers = mock_session.get.call_args[1]["headers"]
    assert call_headers.get("Authorization") == "Bearer tok123"


def test_auth_headers_sent_on_put(mock_session, resolver):
    client = SolidClient(
        resolver,
        session=mock_session,
        auth_headers={"Authorization": "Bearer tok123"},
    )
    mock_session.put.return_value = MagicMock(status_code=201)
    client.put("stash://pod/file.txt", b"data")
    call_headers = mock_session.put.call_args[1]["headers"]
    assert call_headers.get("Authorization") == "Bearer tok123"
    assert call_headers.get("Content-Type") == "application/octet-stream"


def test_no_auth_headers_by_default(mock_session, resolver):
    client = SolidClient(resolver, session=mock_session)
    mock_session.get.return_value = MagicMock(status_code=200, content=b"ok")
    client.get("stash://pod/file.txt")
    call_headers = mock_session.get.call_args[1].get("headers", {})
    assert "Authorization" not in call_headers
