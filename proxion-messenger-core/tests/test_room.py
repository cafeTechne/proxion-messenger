"""Unit tests for room.py — federated chat rooms."""

import pytest
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

from proxion_messenger_core.room import (
    RoomConfig, RoomMembership,
    create_room, invite_to_room, join_room,
    send_to_room, read_room, set_room_acl,
)
from proxion_messenger_core.federation import RelationshipCertificate
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def mock_pod_client():
    """Mock SolidClient."""
    client = MagicMock()
    client._resolver = MagicMock()
    client._resolver.pod_base_url = "http://localhost:3001/alice/"
    return client


@pytest.fixture
def alice_agent():
    """Generate Alice's agent state."""
    return AgentState.generate()


@pytest.fixture
def bob_agent():
    """Generate Bob's agent state."""
    return AgentState.generate()


@pytest.fixture
def mock_cert():
    """Mock RelationshipCertificate."""
    cert = MagicMock(spec=RelationshipCertificate)
    cert.certificate_id = uuid.uuid4().hex
    return cert


@pytest.fixture
def sample_room(alice_agent):
    """Create a sample room config."""
    room_id = uuid.uuid4().hex
    return RoomConfig(
        room_id=room_id,
        name="Test Room",
        owner_webid="http://localhost:3001/alice/profile/card#me",
        pod_url="http://localhost:3001/alice/",
        stash_root=f"stash://rooms/{room_id}/",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def test_create_room_puts_metadata(mock_pod_client, alice_agent):
    """Test that create_room() PUTs room.json metadata."""
    with patch("proxion_messenger_core.room.uuid.uuid4") as mock_uuid:
        mock_uuid.return_value.hex = "test-room-id"
        
        room = create_room(
            mock_pod_client,
            "http://localhost:3001/alice/profile/card#me",
            "Test Room",
        )
        
        assert room.room_id == "test-room-id"
        assert room.name == "Test Room"
        assert room.stash_root == "stash://rooms/test-room-id/"
        
        # Verify PUT was called with room.json
        mock_pod_client.put.assert_called_once()
        call_args = mock_pod_client.put.call_args
        assert call_args[0][0] == "stash://rooms/test-room-id/room.json"


def test_room_config_serialization(sample_room):
    """Test RoomConfig can be converted to/from dict."""
    from dataclasses import asdict
    
    d = asdict(sample_room)
    
    assert d["room_id"] == sample_room.room_id
    assert d["name"] == sample_room.name
    assert d["owner_webid"] == sample_room.owner_webid


def test_room_membership_holds_cert(sample_room, mock_cert):
    """Test RoomMembership structure."""
    membership = RoomMembership(
        room=sample_room,
        cert=mock_cert,
        member_webid="http://localhost:3002/bob/profile/card#me",
    )
    
    assert membership.room == sample_room
    assert membership.cert == mock_cert
    assert membership.member_webid == "http://localhost:3002/bob/profile/card#me"


def test_set_room_acl_generates_turtle(mock_pod_client, sample_room):
    """Test set_room_acl() generates Turtle with owner and member stanzas."""
    owner_webid = "http://localhost:3001/alice/profile/card#me"
    member_webids = [
        "http://localhost:3002/bob/profile/card#me",
        "http://localhost:3003/charlie/profile/card#me",
    ]

    acl_path = set_room_acl(sample_room, mock_pod_client, owner_webid, member_webids)

    # WAC container ACL lives at /.acl (slash before dot)
    assert acl_path == sample_room.stash_root.rstrip("/") + "/.acl"

    mock_pod_client.put.assert_called_once()
    call_args = mock_pod_client.put.call_args
    assert call_args[0][0] == acl_path

    # Check ACL content contains owner and member stanzas
    acl_content = call_args[0][1].decode("utf-8")
    assert "#owner" in acl_content
    assert "#members" in acl_content
    assert owner_webid in acl_content
    assert all(wid in acl_content for wid in member_webids)
    assert "acl:Read" in acl_content
    assert "acl:Write" in acl_content


def test_send_to_room_writes_message(mock_pod_client, sample_room, mock_cert):
    """Test send_to_room() writes a message JSON to the room's messages container."""
    membership = RoomMembership(
        room=sample_room,
        cert=mock_cert,
        member_webid="http://localhost:3002/bob/profile/card#me",
    )

    send_to_room(mock_pod_client, membership, "Hello room!")

    mock_pod_client.put.assert_called_once()
    call_args = mock_pod_client.put.call_args
    path = call_args[0][0]
    assert path.startswith(sample_room.stash_root.rstrip("/") + "/messages/")
    assert path.endswith(".json")

    data = json.loads(call_args[0][1].decode("utf-8"))
    assert data["content"] == "Hello room!"
    assert data["cert_id"] == sample_room.room_id


def test_send_to_room_accepts_room_config(mock_pod_client, sample_room):
    """Test send_to_room() accepts a RoomConfig directly (owner case)."""
    send_to_room(mock_pod_client, sample_room, "Owner message")

    mock_pod_client.put.assert_called_once()
    call_args = mock_pod_client.put.call_args
    path = call_args[0][0]
    assert path.startswith(sample_room.stash_root.rstrip("/") + "/messages/")

    data = json.loads(call_args[0][1].decode("utf-8"))
    assert data["content"] == "Owner message"
    assert data["cert_id"] == sample_room.room_id


def test_read_room_filters_by_since(sample_room, mock_cert):
    """Test read_room() filters messages by since timestamp."""
    from proxion_messenger_core.messaging import Message

    membership = RoomMembership(
        room=sample_room,
        cert=mock_cert,
        member_webid="http://localhost:3002/bob/profile/card#me",
    )

    msg1 = Message(
        message_id="msg1",
        cert_id=sample_room.room_id,
        from_pub_hex="aa",
        content="Early",
        timestamp=1775901600,  # 2026-04-11T10:00:00Z
        signature="",
    )
    msg2 = Message(
        message_id="msg2",
        cert_id=sample_room.room_id,
        from_pub_hex="aa",
        content="Later",
        timestamp=1775901900,  # 2026-04-11T10:05:00Z
        signature="",
    )

    mock_client = MagicMock()
    mock_client.list.return_value = [
        "http://localhost:3001/alice/room/messages/msg1.json",
        "http://localhost:3001/alice/room/messages/msg2.json",
    ]
    mock_client.get.side_effect = [
        json.dumps(msg1.to_dict()).encode(),
        json.dumps(msg2.to_dict()).encode(),
        # Second read_room call needs them again
        json.dumps(msg1.to_dict()).encode(),
        json.dumps(msg2.to_dict()).encode(),
    ]

    mock_agent = MagicMock()

    # No filter
    result = read_room(membership, mock_client, mock_agent, since=None)
    assert len(result) == 2

    # Filter: only messages after 10:02
    since = datetime.fromisoformat("2026-04-11T10:02:00+00:00")
    result = read_room(membership, mock_client, mock_agent, since=since)
    assert len(result) == 1
    assert result[0].content == "Later"


def test_invite_to_room_returns_json(sample_room, alice_agent):
    """Test invite_to_room() returns JSON string."""
    mock_store = MagicMock()
    
    with patch("proxion_messenger_core.handshake.create_invite") as mock_create_invite:
        mock_invite = MagicMock()
        mock_invite.invite_id = "invite-123"
        mock_invite.inviter_key = b"test-key"
        mock_invite.capabilities = []
        mock_create_invite.return_value = mock_invite
        
        result = invite_to_room(sample_room, alice_agent, mock_store)
        
        # Should be valid JSON string
        data = json.loads(result)
        assert "invite_id" in data
        assert data["invite_id"] == "invite-123"


def test_join_room_accepts_invite(alice_agent):
    """Test join_room() accepts an invite and returns membership."""
    invite_data = {
        "invite_id": "invite-123",
        "inviter_key": "test-key",
        "capabilities": [
            {"can": "read", "with_": "stash://rooms/test/"},
            {"can": "write", "with_": "stash://rooms/test/"},
        ],
    }
    invite_json = json.dumps(invite_data)
    mock_store = MagicMock()

    with patch("proxion_messenger_core.handshake.accept_invite") as mock_accept, \
         patch("proxion_messenger_core.handshake.receive_certificates") as mock_recv_certs:

        mock_cert = MagicMock()
        mock_recv_certs.return_value = [(mock_cert, True)]

        membership = join_room(invite_json, alice_agent, "http://localhost:3001/alice/profile/card#me", mock_store)

        assert membership.member_webid == "http://localhost:3001/alice/profile/card#me"
        assert membership.cert == mock_cert
        assert membership.room.stash_root == "stash://rooms/test/"


# ---------------------------------------------------------------------------
# Round 24 — topic, description, update_room_metadata
# ---------------------------------------------------------------------------

def test_room_config_has_topic_and_description_fields():
    """RoomConfig accepts topic and description kwargs."""
    room = RoomConfig(
        room_id="r-1",
        name="General",
        owner_webid="alice@example.com",
        pod_url="http://pod",
        stash_root="stash://rooms/r-1/",
        created_at="2026-04-11T00:00:00Z",
        topic="Welcome to general!",
        description="The main chat channel.",
    )
    assert room.topic == "Welcome to general!"
    assert room.description == "The main chat channel."


def test_update_room_metadata_writes_json():
    """update_room_metadata PUTs updated room.json with new topic."""
    from proxion_messenger_core.room import update_room_metadata
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    room = RoomConfig(
        room_id="r-2",
        name="Dev",
        owner_webid="alice@example.com",
        pod_url="http://pod",
        stash_root="stash://rooms/r-2/",
        created_at="2026-04-11T00:00:00Z",
    )

    updated = update_room_metadata(room, mock_client, topic="New topic")
    assert updated.topic == "New topic"
    mock_client.put.assert_called_once()
    path, body = mock_client.put.call_args[0]
    data = json.loads(body.decode("utf-8"))
    assert data["topic"] == "New topic"


def test_update_room_metadata_none_values_are_ignored():
    """Passing None for topic/description leaves existing values unchanged."""
    from proxion_messenger_core.room import update_room_metadata
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    room = RoomConfig(
        room_id="r-3",
        name="Art",
        owner_webid="alice@example.com",
        pod_url="http://pod",
        stash_root="stash://rooms/r-3/",
        created_at="2026-04-11T00:00:00Z",
        topic="Original topic",
        description="Original description",
    )

    update_room_metadata(room, mock_client, topic=None, description=None)
    assert room.topic == "Original topic"
    assert room.description == "Original description"
