"""Blocklist management for filtering unwanted WebIDs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Set

class Blocklist:
    """Manages a persistent list of blocked WebIDs."""
    
    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.blocked_webids: Set[str] = set()
        self.load()

    def _write_atomic(self, data: list[str]):
        """Write blocklist atomically."""
        tmp = self.storage_path.with_suffix(".tmp")
        bak = self.storage_path.with_suffix(".bak")
        
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            
        if self.storage_path.exists():
            import shutil
            shutil.copy2(self.storage_path, bak)
            
        tmp.replace(self.storage_path)

    def load(self):
        """Load blocked WebIDs from disk."""
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, "r") as f:
                data = json.load(f)
                self.blocked_webids = set(data)
        except Exception:
            self.blocked_webids = set()

    def save(self):
        """Save blocked WebIDs to disk."""
        self._write_atomic(list(self.blocked_webids))

    def block(self, webid: str):
        """Block a WebID."""
        self.blocked_webids.add(webid)
        self.save()

    def unblock(self, webid: str):
        """Unblock a WebID."""
        if webid in self.blocked_webids:
            self.blocked_webids.remove(webid)
            self.save()

    def is_blocked(self, webid: str) -> bool:
        """Check if a WebID is blocked."""
        return webid in self.blocked_webids
