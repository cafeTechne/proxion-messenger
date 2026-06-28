"""Isolated, no-TLS gateway launcher for automated tests (H2 federation harness).

Unlike run_gateway.py this does NOT auto-generate a TLS cert — two self-signed
gateways POSTing to each other's /relay would fail cert verification. Plain
http/ws on 127.0.0.1 is a secure browser context anyway (localhost), so the web
app and even getUserMedia work. Everything is driven by env:

    PROXION_DATA_DIR    fresh identity + db (isolation)
    PROXION_HTTP_PORT   web + relay HTTP port
    PROXION_WS_PORT     websocket port
    PROXION_WEB_DIR     static web/ dir to serve

Caller should also set: PROXION_HOST=127.0.0.1, PROXION_PUBLIC_URL= (empty, so the
ws url derives to ws://host:wsPort), PROXION_REQUIRE_AUTH=0, PROXION_CSS_URL= (pod
off), PROXION_ALLOW_PRIVATE_RELAY=1 (loopback cross-gateway relay).
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "proxion-messenger-core" / "src"))


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

    data_dir = Path(os.environ["PROXION_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)

    import logging
    logging.basicConfig(level=os.environ.get("PROXION_LOG_LEVEL", "INFO"),
                        format="%(levelname)s %(name)s %(message)s")
    identity_key = _load_or_create_raw_key(
        data_dir / "identity.key", Ed25519PrivateKey, Ed25519PrivateKey.generate)
    store_key = _load_or_create_raw_key(
        data_dir / "store.key", X25519PrivateKey, X25519PrivateKey.generate)
    agent = AgentState(identity_key=identity_key, store_key=store_key)

    config = GatewayConfig(
        http_port=int(os.environ["PROXION_HTTP_PORT"]),
        web_dir=os.environ.get("PROXION_WEB_DIR"),
        db_path=str(data_dir / "proxion.db"),
    )  # host/port/public_url/ssl all come from env defaults (no PROXION_SSL_CERT → no TLS)

    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=config, read_state=ReadState(),
    )
    print("PROXION_GATEWAY_READY", flush=True)
    try:
        print("  Address :", gw._proxion_address(), flush=True)
    except Exception:
        pass
    await gw.run()


if __name__ == "__main__":
    asyncio.run(main())
