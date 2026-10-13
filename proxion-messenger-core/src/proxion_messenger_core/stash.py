"""A directory-backed key-value store (the gateway's and CLI's local stash)."""

from __future__ import annotations

from pathlib import Path


class SimpleStash:
    """A simple key-value store for storing data in a directory."""
    
    def __init__(self, stash_dir: Path):
        self.dir = stash_dir
        self.dir.mkdir(parents=True, exist_ok=True)
    
    def get_sync(self, key: str):
        """Get a value by key synchronously. Returns None if not found."""
        path = self.dir / key
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
    
    async def get(self, key: str):
        """Get a value by key. Returns None if not found."""
        return self.get_sync(key)
    
    async def put(self, key: str, value: bytes):
        """Store a value by key."""
        path = self.dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    
    async def delete(self, key: str):
        """Delete a key. Does nothing if not found."""
        path = self.dir / key
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    
    async def list(self, prefix: str = ""):
        """List all keys with optional prefix."""
        result = []
        start_dir = self.dir / prefix if prefix else self.dir
        if not start_dir.exists():
            return result
        for path in start_dir.rglob("*"):
            if path.is_file():
                rel_path = path.relative_to(self.dir)
                result.append(str(rel_path).replace("\\", "/"))
        return result
