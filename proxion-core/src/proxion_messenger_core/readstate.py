"""Read state tracking for message inbox management."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ReadState:
    """Tracks the last-read message for each thread/room.
    
    Allows `poll_inbox` to return only unread messages without rescanning.
    
    Parameters
    ----------
    _marks : dict[str, str]
        Maps thread_id or room_id to last-read message_id.
    """
    _marks: dict[str, str] = field(default_factory=dict)
    _seen: set[str] = field(default_factory=set)
    _last_poll: Optional[str] = None
    
    def mark_read(self, thread_id: str, message_id: str) -> None:
        # ... existing ...
        self._marks[thread_id] = message_id

    def is_seen(self, message_id: str) -> bool:
        """Check if a message has been processed/broadcasted."""
        return message_id in self._seen

    def mark_seen(self, message_id: str) -> None:
        """Mark a message as processed/broadcasted."""
        self._seen.add(message_id)

    def get_last_poll_time(self) -> Optional[datetime]:
        """Get the last successful poll timestamp."""
        if not self._last_poll:
            return None
        from datetime import datetime, timezone
        return datetime.fromisoformat(self._last_poll)

    def set_last_poll_time(self, dt: datetime) -> None:
        """Set the last successful poll timestamp."""
        self._last_poll = dt.isoformat()

    def last_read(self, thread_id: str) -> Optional[str]:
        # ... existing ...
        return self._marks.get(thread_id)
    
    def save(self, path: Path) -> None:
        """Save read state to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "marks": self._marks,
            "seen": list(self._seen),
            "last_poll": self._last_poll
        }
        path.write_text(json.dumps(data, indent=2))
    
    @classmethod
    def load(cls, path: Path) -> ReadState:
        """Load read state from a JSON file."""
        if not path.exists():
            return cls()
        
        data = json.loads(path.read_text())
        if "marks" in data:
            return cls(
                _marks=data["marks"],
                _seen=set(data.get("seen", [])),
                _last_poll=data.get("last_poll")
            )
        # Compatibility with old format (just the marks dict)
        return cls(_marks=data)
