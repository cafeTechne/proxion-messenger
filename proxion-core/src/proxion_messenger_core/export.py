"""Chat history export to JSON and Markdown."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from .messaging import receive, apply_edits

if TYPE_CHECKING:
    from .federation import RelationshipCertificate
    from .solid_client import SolidClient
    from .room import RoomMembership


def _msg_to_dict(m) -> dict:
    return {
        "message_id": m.message_id,
        "from_pub_hex": m.from_pub_hex,
        "content": m.content,
        "timestamp": m.timestamp,
        "message_type": m.message_type,
        "reply_to_id": m.reply_to_id,
    }


def export_thread_to_json(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    output_path: str,
    holder_state=None,
    signing_key: Optional[bytes] = None,
) -> int:
    """Fetch all DM messages, apply edits, write JSON array to *output_path*.

    Returns the number of messages exported.
    """
    messages = receive(cert, pod_client)
    messages = apply_edits(messages)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([_msg_to_dict(m) for m in messages], f, indent=2)

    return len(messages)


def export_thread_to_markdown(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    output_path: str,
    holder_state=None,
    signing_key: Optional[bytes] = None,
    display_names: Optional[dict] = None,
) -> int:
    """Fetch all DM messages, apply edits, write Markdown to *output_path*.

    Returns the number of messages exported.
    """
    messages = receive(cert, pod_client)
    messages = apply_edits(messages)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## Thread Export — {date_str}", ""]

    for m in messages:
        name = (display_names or {}).get(m.from_pub_hex, m.from_pub_hex[:8])
        ts = datetime.fromtimestamp(m.timestamp, tz=timezone.utc).strftime("%H:%M")
        lines.append(f"**{name}** ({ts}): {m.content}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return len(messages)


def export_room_to_json(
    membership,
    pod_client: SolidClient,
    output_path: str,
) -> int:
    """Fetch all room messages, apply edits, write JSON array to *output_path*.

    Returns the number of messages exported.
    """
    from .room import read_room

    messages = read_room(membership.room, pod_client)
    messages = apply_edits(messages)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([_msg_to_dict(m) for m in messages], f, indent=2)

    return len(messages)


def export_room_to_markdown(
    membership,
    pod_client: SolidClient,
    output_path: str,
    display_names: Optional[dict] = None,
) -> int:
    """Fetch all room messages, apply edits, write Markdown to *output_path*.

    Returns the number of messages exported.
    """
    from .room import read_room

    messages = read_room(membership.room, pod_client)
    messages = apply_edits(messages)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## Room Export — {date_str}", ""]

    for m in messages:
        name = (display_names or {}).get(m.from_pub_hex, m.from_pub_hex[:8])
        ts = datetime.fromtimestamp(m.timestamp, tz=timezone.utc).strftime("%H:%M")
        lines.append(f"**{name}** ({ts}): {m.content}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return len(messages)
