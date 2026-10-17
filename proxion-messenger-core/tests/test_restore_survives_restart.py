"""POST /restore must survive a gateway restart.

The restore used to swap the identity in memory and write an agent.json that no
launcher reads, while run_gateway.py (the Tauri sidecar entry) loads
identity.key and store.key. After a restart the gateway was back on its old
identity although the user had been told the restore worked. LocalStore secrets
are wrapped with a key derived from the identity, so they must be re-wrapped
for the restored identity too, or they become unreadable.

These tests run the real run_gateway.py in a subprocess with a temp data dir
and blank pod settings, because the bug only shows across a process restart.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.keyfiles import load_or_create_agent_keys, persist_restored_keys
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "proxion-messenger-core" / "src"
PASSPHRASE = "correct horse battery staple"

_TLS_CTX = ssl.create_default_context()
_TLS_CTX.check_hostname = False
_TLS_CTX.verify_mode = ssl.CERT_NONE


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _GatewayProcess:
    """One run_gateway.py process on a temp data dir, pod disabled."""

    def __init__(self, data_dir: Path, tls_dir: Path):
        from proxion_messenger_core.tls import ensure_self_signed_cert

        cert, key = ensure_self_signed_cert(tls_dir)
        self.http_port = _free_port()
        ws_port = _free_port()
        env = dict(
            os.environ,
            PROXION_DATA_DIR=str(data_dir),
            PROXION_HOST="127.0.0.1",
            PROXION_HTTP_PORT=str(self.http_port),
            PROXION_WS_PORT=str(ws_port),
            # Non-empty so run_gateway skips the UPnP attempt.
            PROXION_PUBLIC_URL=f"https://127.0.0.1:{self.http_port}",
            PROXION_SSL_CERT=str(cert),
            PROXION_SSL_KEY=str(key),
            PROXION_REQUIRE_AUTH="0",
            PROXION_CSS_URL="",
            PROXION_CSS_EMAIL="",
            PROXION_CSS_PASSWORD="",
            PROXION_ADMIN_API_TOKEN="",
            PROXION_API_TOKEN="",
            PYTHONPATH=str(SRC),
        )
        self.log: list[str] = []
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "run_gateway.py")],
            cwd=str(REPO), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("gateway exited:\n" + "".join(self.log))
            if self.address() and self._port_open():
                return
            time.sleep(0.2)
        self.stop()
        raise RuntimeError("gateway did not start:\n" + "".join(self.log))

    def _pump(self):
        for line in self.proc.stdout:
            self.log.append(line)

    def _port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.http_port), timeout=0.5):
                return True
        except OSError:
            return False

    def address(self) -> str | None:
        for line in list(self.log):
            if "Address :" in line:
                return line.split("Address :", 1)[1].strip()
        return None

    def post_restore(self, kit: bytes, headers: dict) -> tuple[int, bytes]:
        h = {"Content-Type": "application/json", "Origin": f"https://127.0.0.1:{self.http_port}"}
        h.update(headers)
        req = urllib.request.Request(
            f"https://127.0.0.1:{self.http_port}/restore", data=kit, method="POST", headers=h,
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=_TLS_CTX) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)


@pytest.fixture
def gateways():
    started: list[_GatewayProcess] = []

    def _start(data_dir: Path, tls_dir: Path) -> _GatewayProcess:
        gp = _GatewayProcess(data_dir, tls_dir)
        started.append(gp)
        return gp

    yield _start
    for gp in started:
        gp.stop()


def _raw(key) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _b64(n: int = 32) -> str:
    return base64.b64encode(os.urandom(n)).decode("ascii")


def _seed_gateway_b(data_dir: Path) -> dict:
    """Give gateway B an identity and some LocalStore secrets wrapped with it."""
    b_agent = AgentState.generate()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "identity.key").write_bytes(_raw(b_agent.identity_key))
    (data_dir / "store.key").write_bytes(_raw(b_agent.store_key))
    store = LocalStore(str(data_dir / "proxion.db"), db_wrap_key=b_agent.db_wrap_key())
    secrets = {
        "root": _b64(), "send": _b64(), "recv": _b64(),
        "prekey": _b64(), "chain": _b64(), "wg": _b64(),
    }
    store.save_dm_session({
        "session_id": "s1", "peer_webid": "did:key:zpeer", "owner_webid": "did:key:zowner",
        "root_key": secrets["root"], "send_chain_key": secrets["send"],
        "recv_chain_key": secrets["recv"], "send_count": 0, "recv_count": 0,
    })
    store.save_prekey(7, "did:key:zowner", _b64(), secrets["prekey"], one_time=False)
    store.save_sender_key("room-1", "did:key:zsender", secrets["chain"], 0)
    store.save_wg_local_identity(_b64(), secrets["wg"])
    return {"agent": b_agent, "secrets": secrets}


def _assert_secrets_readable(db_path: Path, wrap_key: bytes, secrets: dict):
    store = LocalStore(str(db_path), db_wrap_key=wrap_key)
    sess = store.get_dm_session("did:key:zowner", "did:key:zpeer")
    assert sess is not None
    assert sess["root_key"] == secrets["root"]
    assert sess["send_chain_key"] == secrets["send"]
    assert sess["recv_chain_key"] == secrets["recv"]
    assert store.get_signed_prekey("did:key:zowner")["priv_wrapped_b64"] == secrets["prekey"]
    assert store.get_sender_key("room-1", "did:key:zsender")["chain_key_b64"] == secrets["chain"]
    assert store.get_wg_local_identity()["priv_wrapped_b64"] == secrets["wg"]


def _restore_and_restart(gateways, tmp_path, kit: bytes, headers: dict, expected_agent: AgentState):
    data_b = tmp_path / "gw-b"
    tls = tmp_path / "tls"
    seeded = _seed_gateway_b(data_b)
    old_did = pub_key_to_did(seeded["agent"].identity_pub_bytes)
    new_did = pub_key_to_did(expected_agent.identity_pub_bytes)
    assert old_did != new_did

    b = gateways(data_b, tls)
    assert b.address().startswith(old_did)
    status, body = b.post_restore(kit, headers)
    assert status == 200, body
    b.stop()

    b2 = gateways(data_b, tls)
    assert b2.address().startswith(new_did), (
        f"restart came back as {b2.address()!r}, expected the restored {new_did}"
    )
    b2.stop()

    # The restored keys are on disk in the launcher's raw format...
    assert (data_b / "identity.key").read_bytes() == _raw(expected_agent.identity_key)
    assert (data_b / "store.key").read_bytes() == _raw(expected_agent.store_key)
    # ...no half-written restore files are left behind...
    assert not [p.name for p in data_b.iterdir() if ".restore" in p.name or p.suffix == ".tmp"]
    # ...and every wrapped LocalStore secret unwraps under the restored identity.
    _assert_secrets_readable(data_b / "proxion.db", expected_agent.db_wrap_key(), seeded["secrets"])


def test_passphrase_restore_survives_restart(gateways, tmp_path):
    a_agent = AgentState.generate()
    kit = a_agent.export_backup(passphrase=PASSPHRASE.encode())
    _restore_and_restart(
        gateways, tmp_path, kit, {"x-proxion-passphrase": PASSPHRASE}, a_agent,
    )


def test_recipient_key_restore_survives_restart(gateways, tmp_path):
    a_agent = AgentState.generate()
    escrow = X25519PrivateKey.generate()
    escrow_pub = escrow.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    kit = a_agent.export_backup(recipient_pubkey_hex=escrow_pub.hex())
    assert json.loads(kit)["backup_mode"] == "recipient_key"
    _restore_and_restart(
        gateways, tmp_path, kit, {"x-proxion-recipient-privkey": _raw(escrow).hex()}, a_agent,
    )


# ---------------------------------------------------------------------------
# Interrupted restore: the launcher completes or discards it on next start
# ---------------------------------------------------------------------------

def _pending_restore(tmp_path, *, db_committed: bool):
    """Simulate a crash after the pending key files were written."""
    seeded = _seed_gateway_b(tmp_path)
    new = AgentState.generate()
    (tmp_path / "identity.key.restore").write_bytes(_raw(new.identity_key))
    (tmp_path / "store.key.restore").write_bytes(_raw(new.store_key))
    if db_committed:
        LocalStore(str(tmp_path / "proxion.db"),
                   db_wrap_key=seeded["agent"].db_wrap_key()).rewrap_secrets(new.db_wrap_key())
    return seeded, new


def test_interrupted_restore_after_db_commit_is_completed(tmp_path):
    seeded, new = _pending_restore(tmp_path, db_committed=True)
    identity, store = load_or_create_agent_keys(tmp_path)
    assert _raw(identity) == _raw(new.identity_key)
    assert _raw(store) == _raw(new.store_key)
    assert not list(tmp_path.glob("*.restore"))
    _assert_secrets_readable(tmp_path / "proxion.db", new.db_wrap_key(), seeded["secrets"])


def test_interrupted_restore_before_db_commit_is_discarded(tmp_path):
    seeded, _new = _pending_restore(tmp_path, db_committed=False)
    identity, store = load_or_create_agent_keys(tmp_path)
    assert _raw(identity) == _raw(seeded["agent"].identity_key)
    assert _raw(store) == _raw(seeded["agent"].store_key)
    assert not list(tmp_path.glob("*.restore"))
    _assert_secrets_readable(tmp_path / "proxion.db", seeded["agent"].db_wrap_key(), seeded["secrets"])


def test_interrupted_restore_between_renames_is_completed(tmp_path):
    seeded, new = _pending_restore(tmp_path, db_committed=True)
    os.replace(tmp_path / "identity.key.restore", tmp_path / "identity.key")
    identity, store = load_or_create_agent_keys(tmp_path)
    assert _raw(identity) == _raw(new.identity_key)
    assert _raw(store) == _raw(new.store_key)


def test_persist_restored_keys_switches_live_store_and_wraps_legacy_rows(tmp_path):
    seeded = _seed_gateway_b(tmp_path)
    store = LocalStore(str(tmp_path / "proxion.db"), db_wrap_key=seeded["agent"].db_wrap_key())
    # A legacy raw (pre-wrapping) row gets wrapped on the way.
    legacy = _b64()
    conn = sqlite3.connect(str(tmp_path / "proxion.db"))
    conn.execute("UPDATE sender_keys SET chain_key_b64=?", (legacy,))
    conn.commit()
    conn.close()
    seeded["secrets"]["chain"] = legacy

    new = AgentState.generate()
    persist_restored_keys(tmp_path, new, store)
    # Writes after the restore use the new key too.
    store.save_sender_key("room-2", "did:key:zsender", "Y2hhaW4=", 0)
    reopened = LocalStore(str(tmp_path / "proxion.db"), db_wrap_key=new.db_wrap_key())
    assert reopened.get_sender_key("room-2", "did:key:zsender")["chain_key_b64"] == "Y2hhaW4="
    _assert_secrets_readable(tmp_path / "proxion.db", new.db_wrap_key(), seeded["secrets"])
    conn = sqlite3.connect(str(tmp_path / "proxion.db"))
    stored = conn.execute("SELECT chain_key_b64 FROM sender_keys WHERE room_id='room-1'").fetchone()[0]
    conn.close()
    assert stored.startswith("{") and legacy not in stored
