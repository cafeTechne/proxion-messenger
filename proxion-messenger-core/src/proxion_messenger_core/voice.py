"""Voice signaling stub for WebRTC calls over federated Pods.

This module defines the data structures and message format for voice invites
and signaling. Actual audio codec and WebRTC implementation is deferred.
Future rounds will add real WebRTC using STUN/TURN over the WireGuard relay.
"""

from __future__ import annotations

import uuid
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .federation import RelationshipCertificate
    from .solid_client import SolidClient
    from .persist import AgentState


@dataclass
class VoiceInvite:
    """A WebRTC voice call invitation.
    
    Parameters
    ----------
    session_id : str
        Unique UUID for this voice session.
    caller_webid : str
        WebID of the caller.
    room_id : str, optional
        Room ID if this is a group call. None for DM calls.
    sdp_offer : str
        WebRTC SDP offer (for future WebRTC integration).
    created_at : str
        ISO 8601 timestamp when the invite was created.
    """
    session_id: str
    caller_webid: str
    room_id: Optional[str]
    sdp_offer: str
    created_at: str

@dataclass
class VoiceAnswer:
    """A WebRTC SDP answer."""
    session_id: str
    sdp_answer: str

@dataclass
class IceCandidate:
    """A WebRTC ICE candidate."""
    session_id: str
    candidate: str
    sdp_mid: Optional[str] = None
    sdp_mline_index: Optional[int] = None


def signal_voice_invite(
    cert: "RelationshipCertificate",
    pod_client: "SolidClient",
    sdp_offer: str,
    session_id: str,
    caller_webid: str,
    room_id: Optional[str] = None,
) -> VoiceInvite:
    """Write a voice call invitation to the pod so the recipient's gateway picks it up.

    The ``session_id`` must be the one already assigned by the gateway so both
    sides refer to the same session.  ``caller_webid`` is the DID or pod WebID
    of the caller, passed explicitly rather than derived from the pod client.
    """
    from .messaging import compose, send

    now_iso = datetime.now(timezone.utc).isoformat()
    invite = VoiceInvite(
        session_id=session_id,
        caller_webid=caller_webid,
        room_id=room_id,
        sdp_offer=sdp_offer,
        created_at=now_iso,
    )
    payload: dict = {
        "type": "voice_invite",
        "session_id": invite.session_id,
        "caller_webid": invite.caller_webid,
        "sdp_offer": invite.sdp_offer,
        "created_at": invite.created_at,
    }
    if room_id is not None:
        payload["room_id"] = room_id

    msg = compose(
        identity_key=getattr(pod_client, "_identity_key", None),
        cert=cert,
        content=json.dumps(payload),
    )
    send(msg, pod_client)
    return invite


def receive_voice_invites(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    holder_state: AgentState,
    signing_key: bytes,
) -> list[VoiceInvite]:
    """Receive voice call invitations from a peer.
    
    Calls messaging.receive() and filters for messages with type="voice_invite".
    
    Parameters
    ----------
    cert : RelationshipCertificate
        The relationship certificate for the peer.
    pod_client : SolidClient
        Authenticated client for reading messages.
    holder_state : AgentState
        The receiver's agent state (for cert validation).
    signing_key : bytes
        The sender's HMAC signing key (for message validation).
    
    Returns
    -------
    list[VoiceInvite]
        List of received voice invites.
    """
    from .messaging import receive
    
    messages = receive(cert, pod_client, holder_state, signing_key)
    
    invites = []
    for msg in messages:
        try:
            payload = json.loads(msg.content)
            if payload.get("type") == "voice_invite":
                invite = VoiceInvite(
                    session_id=payload["session_id"],
                    caller_webid=payload["caller_webid"],
                    room_id=payload.get("room_id"),  # May be absent for DM calls
                    sdp_offer=payload["sdp_offer"],
                    created_at=payload["created_at"],
                )
                invites.append(invite)
        except (json.JSONDecodeError, KeyError, TypeError):
            # Skip malformed messages
            pass
    return invites


@dataclass
class VoiceChannelState:
    channel_id: str
    participants: list[str]   # list of WebIDs currently "in" the channel
    updated_at: str

def join_voice_channel(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    channel_id: str,
    my_webid: str,
) -> None:
    """Signal entry into a voice channel."""
    from .messaging import compose, send
    payload = {
        "type": "voice_join",
        "channel_id": channel_id,
        "webid": my_webid,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    msg = compose(
        identity_key=getattr(pod_client, "identity_key", None),
        cert=cert,
        content=json.dumps(payload),
    )
    send(msg, pod_client)

def leave_voice_channel(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    channel_id: str,
    my_webid: str,
) -> None:
    """Signal exit from a voice channel."""
    from .messaging import compose, send
    payload = {
        "type": "voice_leave",
        "channel_id": channel_id,
        "webid": my_webid,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    msg = compose(
        identity_key=getattr(pod_client, "identity_key", None),
        cert=cert,
        content=json.dumps(payload),
    )
    send(msg, pod_client)

def get_voice_channel_state(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    holder_state: AgentState,
    signing_key: bytes,
    channel_id: str,
) -> VoiceChannelState:
    """Reconstruct voice channel participant list by replaying signaling messages."""
    from .messaging import receive
    messages = receive(cert, pod_client, holder_state, signing_key)
    
    participants = set()
    latest_ts = ""
    
    for msg in messages:
        try:
            payload = json.loads(msg.content)
            m_type = payload.get("type")
            m_channel = payload.get("channel_id")
            m_webid = payload.get("webid")
            m_ts = payload.get("timestamp", "")
            
            if m_channel != channel_id:
                continue
                
            if m_type == "voice_join":
                participants.add(m_webid)
            elif m_type == "voice_leave":
                if m_webid in participants:
                    participants.remove(m_webid)
            
            if m_ts > latest_ts:
                latest_ts = m_ts
        except Exception:
            pass

    return VoiceChannelState(
        channel_id=channel_id,
        participants=list(participants),
        updated_at=latest_ts or datetime.now(timezone.utc).isoformat()
    )

def signal_voice_answer(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    session_id: str,
    sdp_answer: str,
) -> None:
    """Send an SDP answer to the caller."""
    from .messaging import compose, send
    payload = {
        "type": "voice_answer",
        "session_id": session_id,
        "sdp_answer": sdp_answer,
    }
    msg = compose(
        identity_key=getattr(pod_client, "_identity_key", None),
        cert=cert,
        content=json.dumps(payload),
    )
    send(msg, pod_client)

def signal_ice_candidate(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    session_id: str,
    candidate: str,
    sdp_mid: Optional[str] = None,
    sdp_mline_index: Optional[int] = None,
) -> None:
    """Send an ICE candidate to the peer."""
    from .messaging import compose, send
    payload = {
        "type": "ice_candidate",
        "session_id": session_id,
        "candidate": candidate,
        "sdp_mid": sdp_mid,
        "sdp_mline_index": sdp_mline_index,
    }
    msg = compose(
        identity_key=getattr(pod_client, "_identity_key", None),
        cert=cert,
        content=json.dumps(payload),
    )
    send(msg, pod_client)

def receive_voice_answers(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    holder_state: AgentState,
    signing_key: bytes,
) -> list[VoiceAnswer]:
    """Receive voice call answers from a peer."""
    from .messaging import receive
    messages = receive(cert, pod_client, holder_state, signing_key)
    answers = []
    for msg in messages:
        try:
            payload = json.loads(msg.content)
            if payload.get("type") == "voice_answer":
                answers.append(VoiceAnswer(
                    session_id=payload["session_id"],
                    sdp_answer=payload["sdp_answer"],
                ))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return answers

def signal_voice_hangup(
    cert: "RelationshipCertificate",
    pod_client: "SolidClient",
    session_id: str,
) -> None:
    """Write a hangup signal to the pod so the peer's gateway cleans up the session."""
    from .messaging import compose, send
    payload = {
        "type": "voice_hangup",
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    msg = compose(
        identity_key=getattr(pod_client, "_identity_key", None),
        cert=cert,
        content=json.dumps(payload),
    )
    send(msg, pod_client)


def receive_ice_candidates(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    holder_state: AgentState,
    signing_key: bytes,
    session_id: Optional[str] = None,
) -> list[IceCandidate]:
    """Receive ICE candidates from a peer, optionally filtered by session_id."""
    from .messaging import receive
    messages = receive(cert, pod_client, holder_state, signing_key)
    candidates = []
    for msg in messages:
        try:
            payload = json.loads(msg.content)
            if payload.get("type") == "ice_candidate":
                sid = payload.get("session_id")
                if session_id and sid != session_id:
                    continue
                candidates.append(IceCandidate(
                    session_id=sid,
                    candidate=payload["candidate"],
                    sdp_mid=payload.get("sdp_mid"),
                    sdp_mline_index=payload.get("sdp_mline_index"),
                ))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return candidates

