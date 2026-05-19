import json
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone
from proxion_messenger_core.voice import join_voice_channel, leave_voice_channel, get_voice_channel_state, VoiceChannelState
from proxion_messenger_core.messaging import Message
from proxion_messenger_core.federation import RelationshipCertificate
from proxion_messenger_core.persist import AgentState

@pytest.fixture
def mock_cert():
    cert = MagicMock(spec=RelationshipCertificate)
    cert.certificate_id = "cert-123"
    return cert

@pytest.fixture
def mock_client():
    client = MagicMock()
    # Mock identity_key chain to return serializable values
    mock_id_key = MagicMock()
    mock_id_key.public_key().public_bytes().hex.return_value = "alice-pub-hex"
    mock_id_key.sign().hex.return_value = "sig-hex"
    client.identity_key = mock_id_key
    return client

def test_join_voice_channel_sends_message(mock_cert, mock_client):
    join_voice_channel(mock_cert, mock_client, "lounge", "alice-webid")
    
    # Check if messaging.send was called (via mock_client.put or mock of send)
    # Actually join_voice_channel calls messaging.send directly.
    # We should patch messaging.send.
    with MagicMock() as mock_send:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("proxion_messenger_core.messaging.send", mock_send)
            join_voice_channel(mock_cert, mock_client, "lounge", "alice-webid")
            assert mock_send.call_count == 1
            msg = mock_send.call_args[0][0]
            payload = json.loads(msg.content)
            assert payload["type"] == "voice_join"
            assert payload["channel_id"] == "lounge"
            assert payload["webid"] == "alice-webid"

def test_get_voice_channel_state_replays_events(mock_cert, mock_client):
    # Mocking messaging.receive
    ts1 = "2026-04-11T12:00:00Z"
    ts2 = "2026-04-11T12:01:00Z"
    
    msg1 = Message(
        message_id="m1", cert_id="c1", from_pub_hex="p1", 
        content=json.dumps({"type": "voice_join", "channel_id": "lounge", "webid": "alice", "timestamp": ts1}),
        timestamp=100, signature="s1"
    )
    msg2 = Message(
        message_id="m2", cert_id="c1", from_pub_hex="p1", 
        content=json.dumps({"type": "voice_join", "channel_id": "lounge", "webid": "bob", "timestamp": ts2}),
        timestamp=101, signature="s2"
    )
    msg3 = Message(
        message_id="m3", cert_id="c1", from_pub_hex="p1", 
        content=json.dumps({"type": "voice_leave", "channel_id": "lounge", "webid": "alice", "timestamp": "2026-04-11T12:02:00Z"}),
        timestamp=102, signature="s3"
    )
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("proxion_messenger_core.messaging.receive", MagicMock(return_value=[msg1, msg2, msg3]))
        
        state = get_voice_channel_state(mock_cert, mock_client, MagicMock(), b"key", "lounge")
        
        assert state.channel_id == "lounge"
        assert "bob" in state.participants
        assert "alice" not in state.participants
        assert len(state.participants) == 1
        assert state.updated_at == "2026-04-11T12:02:00Z"
