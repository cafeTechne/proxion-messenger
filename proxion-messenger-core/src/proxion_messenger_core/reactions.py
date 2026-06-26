"""Message reactions module."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .messaging import compose, send, Message

if TYPE_CHECKING:
    from .federation import RelationshipCertificate
    from .solid_client import SolidClient


@dataclass
class Reaction:
    emoji: str              # e.g. "👍"
    sender_webid: str
    message_id: str         # the message being reacted to
    reaction_message_id: str  # the reaction message's own ID
    timestamp: int


def add_reaction(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    identity_key,
    target_message_id: str,
    emoji: str,
) -> Reaction:
    """Add an emoji reaction to a message.
    
    Composes a message with message_type="reaction" and content containing the emoji.
    """
    content = json.dumps({"emoji": emoji, "target": target_message_id})
    msg = compose(
        identity_key=identity_key,
        cert=cert,
        content=content,
        reply_to_id=target_message_id,
        message_type="reaction"
    )
    send(msg, pod_client)
    return Reaction(
        emoji=emoji,
        sender_webid=msg.from_pub_hex,
        message_id=target_message_id,
        reaction_message_id=msg.message_id,
        timestamp=msg.timestamp
    )


def remove_reaction(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    reaction_message_id: str,
) -> None:
    """Remove a previously added reaction by deleting its message."""
    from .messaging import message_path
    path = message_path(cert.certificate_id, reaction_message_id)
    pod_client.delete(path)


def get_reactions(
    messages: list[Message],
    target_message_id: str,
) -> dict[str, list[str]]:
    """Return an aggregated view of reactions for a specific message from a list of messages.
    
    Returns {emoji: [sender_webid1, sender_webid2, ...]}
    Pure function — works on already-fetched message list.
    """
    reactions: dict[str, list[str]] = {}
    for m in messages:
        if m.message_type == "reaction" and m.reply_to_id == target_message_id:
            try:
                data = json.loads(m.content)
                emoji = data.get("emoji")
                if emoji:
                    if emoji not in reactions:
                        reactions[emoji] = []
                    reactions[emoji].append(m.from_pub_hex)
            except Exception:
                continue
    return reactions
