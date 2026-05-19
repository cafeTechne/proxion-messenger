"""Minimal launcher for Proxion gateway — Tauri sidecar, Docker, and direct runs.

Reads all configuration from environment variables. Generates or loads
Ed25519 identity key and X25519 store key from the data directory (raw bytes).
"""
import asyncio
import os
import sys
from pathlib import Path

# Load .env from the project root if python-dotenv is available.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def _base_dir() -> Path:
    """Resolve base directory — handles PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _load_or_create_raw_key(key_path: Path, key_cls, generate_fn):
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    if key_path.exists():
        return key_cls.from_private_bytes(key_path.read_bytes())
    key = generate_fn()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    return key


async def main():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    from proxion_messenger_core.logging_config import configure_logging

    _log_fmt = os.environ.get("PROXION_LOG_FORMAT", "text")
    _log_level = os.environ.get("PROXION_LOG_LEVEL", "INFO")
    _base_for_log = Path(os.environ.get("PROXION_DATA_DIR", str(Path(__file__).resolve().parent / "data")))
    configure_logging(
        json_output=(_log_fmt.lower() == "json"),
        log_level=_log_level,
        log_dir=str(_base_for_log / "logs"),
    )

    base = _base_dir()
    data_dir = Path(os.environ.get("PROXION_DATA_DIR", str(base / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)

    identity_key = _load_or_create_raw_key(
        data_dir / "identity.key", Ed25519PrivateKey, Ed25519PrivateKey.generate
    )
    store_key = _load_or_create_raw_key(
        data_dir / "store.key", X25519PrivateKey, X25519PrivateKey.generate
    )

    agent = AgentState(identity_key=identity_key, store_key=store_key)

    http_port_str = os.environ.get("PROXION_HTTP_PORT", "8080")

    # Web dir: env var > bundled web/ > None (Tauri serves its own assets in production)
    bundled_web = base / "web"
    web_dir = os.environ.get("PROXION_WEB_DIR") or (str(bundled_web) if bundled_web.exists() else None)

    config = GatewayConfig(
        http_port=int(http_port_str) if http_port_str else None,
        web_dir=web_dir,
        db_path=str(data_dir / "proxion.db"),
    )

    gw = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=config,
        read_state=ReadState(),
    )

    # Signal Tauri (or any parent) that the gateway is ready
    print("PROXION_GATEWAY_READY", flush=True)

    await gw.run()


if __name__ == "__main__":
    asyncio.run(main())
