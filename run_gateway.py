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


def _auto_tls_enabled() -> bool:
    return os.environ.get("PROXION_AUTO_TLS", "1").strip().lower() not in ("0", "false", "no", "off")


async def main():
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

    # Raw identity.key / store.key. Also finishes (or rolls back) an identity
    # restore that was interrupted between its database and key-file steps.
    from proxion_messenger_core.keyfiles import load_or_create_agent_keys
    identity_key, store_key = load_or_create_agent_keys(data_dir, data_dir / "proxion.db")

    agent = AgentState(identity_key=identity_key, store_key=store_key)

    # Auto-generate self-signed TLS cert on first run when no cert is configured.
    # Enables HTTPS (required for getUserMedia on non-localhost origins).
    # The desktop app turns this off with PROXION_AUTO_TLS=0: its webview dials
    # ws://127.0.0.1 and cannot accept a self-signed cert.
    if not os.environ.get("PROXION_SSL_CERT") and _auto_tls_enabled():
        try:
            from proxion_messenger_core.tls import ensure_self_signed_cert
            import pathlib as _pathlib
            _tls_dir = _pathlib.Path.home() / ".proxion" / "tls"
            _cert, _key = ensure_self_signed_cert(_tls_dir)
            os.environ["PROXION_SSL_CERT"] = str(_cert)
            os.environ["PROXION_SSL_KEY"] = str(_key)
        except Exception as _tls_err:
            import logging as _logging
            _logging.getLogger(__name__).warning("TLS cert generation failed: %s", _tls_err)

    http_port_str = os.environ.get("PROXION_HTTP_PORT", "8080")
    http_port = int(http_port_str) if http_port_str else 8080

    # Attempt UPnP port mapping so the gateway is internet-reachable without manual config.
    _upnp_mapped_port: int | None = None
    if not os.environ.get("PROXION_PUBLIC_URL"):
        try:
            from proxion_messenger_core.upnp import try_upnp_map
            _upnp_result = try_upnp_map(http_port)
            if _upnp_result:
                os.environ["PROXION_PUBLIC_URL"] = _upnp_result
                os.environ["PROXION_UPNP_MAPPED"] = "1"
                _upnp_mapped_port = http_port
        except Exception:
            pass

    # Web dir: env var > bundled web/ > None (Tauri serves its own assets in production)
    bundled_web = base / "web"
    web_dir = os.environ.get("PROXION_WEB_DIR") or (str(bundled_web) if bundled_web.exists() else None)

    config = GatewayConfig(
        http_port=http_port,
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

    # Human-readable startup output — useful when running the .exe directly
    print("PROXION_GATEWAY_READY", flush=True)
    try:
        _addr = gw._proxion_address()
        print(f"  Address : {_addr}", flush=True)
    except Exception:
        pass
    print(f"  Web UI  : http://127.0.0.1:{http_port}", flush=True)
    if config.public_url:
        _how = " (via UPnP)" if os.environ.get("PROXION_UPNP_MAPPED") == "1" else ""
        print(f"  Public  : {config.public_url}{_how}", flush=True)
    else:
        print("  Public  : not reachable from internet — open the app for setup guide", flush=True)

    # Remove UPnP mapping cleanly on shutdown
    if _upnp_mapped_port:
        import atexit
        from proxion_messenger_core.upnp import remove_upnp_map
        atexit.register(remove_upnp_map, _upnp_mapped_port)

    await gw.run()


if __name__ == "__main__":
    asyncio.run(main())
