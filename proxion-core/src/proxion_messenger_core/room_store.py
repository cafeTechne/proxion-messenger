"""Disk persistence layer for chat rooms."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .room import RoomConfig, RoomMembership


@dataclass
class RoomStore:
    """Persists room configurations and memberships to disk as JSON files.
    
    Parameters
    ----------
    base_dir : Path
        Root directory for room storage (e.g., ~/.proxion/rooms/).
        Created automatically if it doesn't exist.
    """
    base_dir: Path
    
    def __post_init__(self):
        """Ensure base directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def _room_dir(self, room_id: str) -> Path:
        """Get the directory for a room."""
        return self.base_dir / room_id
    
    def _config_path(self, room_id: str) -> Path:
        """Get the config file path for a room."""
        return self._room_dir(room_id) / "config.json"
    
    def _membership_path(self, room_id: str) -> Path:
        """Get the membership file path for a room."""
        return self._room_dir(room_id) / "memberships.json"
    
    def _write_atomic(self, path: Path, data: dict) -> None:
        """Write JSON data atomically using a .tmp file and .bak backup."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        bak = path.with_suffix(path.suffix + ".bak")
        
        # 1. Write to temporary file
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        # 2. Before replacing, create a backup of current if it exists
        if path.exists():
            import shutil
            try:
                shutil.copy2(path, bak)
            except Exception:
                pass # Best effort backup

        # 3. Atomic rename
        tmp.replace(path)
    
    def save_room(self, config: RoomConfig) -> None:
        """Save a room configuration to disk.
        
        Parameters
        ----------
        config : RoomConfig
            Room configuration to save.
        """
        from .room import RoomConfig as RC
        
        room_dir = self._room_dir(config.room_id)
        room_dir.mkdir(parents=True, exist_ok=True)
        
        config_path = self._config_path(config.room_id)
        config_dict = asdict(config)
        self._write_atomic(config_path, config_dict)
    
    def load_room(self, room_id: str) -> RoomConfig:
        """Load a room configuration from disk.
        
        Parameters
        ----------
        room_id : str
            The room ID to load.
        
        Returns
        -------
        RoomConfig
            The loaded room configuration.
        
        Raises
        ------
        FileNotFoundError
            If the room config file doesn't exist.
        """
        from .room import RoomConfig as RC
        
        config_path = self._config_path(room_id)
        if not config_path.exists():
            raise FileNotFoundError(f"Room config not found: {room_id}")
        
        data = json.loads(config_path.read_text())
        return RC(**data)
    
    def list_rooms(self) -> list[RoomConfig]:
        """List all saved rooms.
        
        Returns
        -------
        list[RoomConfig]
            List of all room configurations.
        """
        from .room import RoomConfig as RC
        
        rooms = []
        for room_dir in self.base_dir.iterdir():
            if room_dir.is_dir():
                try:
                    config = self.load_room(room_dir.name)
                    rooms.append(config)
                except (FileNotFoundError, json.JSONDecodeError, TypeError):
                    # Skip invalid room directories
                    pass
        return sorted(rooms, key=lambda r: r.created_at)
    
    def save_membership(self, membership: RoomMembership) -> None:
        """Save a room membership to disk.
        
        Parameters
        ----------
        membership : RoomMembership
            Room membership to save.
        """
        room_dir = self._room_dir(membership.room.room_id)
        room_dir.mkdir(parents=True, exist_ok=True)
        
        membership_path = self._membership_path(membership.room.room_id)
        membership_dict = asdict(membership)
        # Serialize cert using its dict representation
        if hasattr(membership.cert, 'to_dict'):
            membership_dict['cert'] = membership.cert.to_dict()
        self._write_atomic(membership_path, membership_dict)
    
    def load_membership(self, room_id: str) -> RoomMembership:
        """Load a room membership from disk.
        
        Parameters
        ----------
        room_id : str
            The room ID whose membership to load.
        
        Returns
        -------
        RoomMembership
            The loaded room membership.
        
        Raises
        ------
        FileNotFoundError
            If the membership file doesn't exist.
        """
        from .room import RoomConfig, RoomMembership
        from .federation import RelationshipCertificate
        
        membership_path = self._membership_path(room_id)
        if not membership_path.exists():
            raise FileNotFoundError(f"Membership not found: {room_id}")
        
        data = json.loads(membership_path.read_text())
        room_data = data['room']
        room = RoomConfig(**room_data)
        
        # Reconstruct cert from dict
        cert_data = data['cert']
        cert = RelationshipCertificate(**cert_data)
        
        return RoomMembership(
            room=room,
            cert=cert,
            member_webid=data['member_webid'],
        )
    
    def delete_room(self, room_id: str) -> None:
        """Delete a room and its membership data from disk.
        
        Parameters
        ----------
        room_id : str
            The room ID to delete.
        """
        import shutil
        room_dir = self._room_dir(room_id)
        if room_dir.exists():
            shutil.rmtree(room_dir)
