"""Export/import must not bring back a contact the owner removed or blocked.

The export left out the revocations table and blocks, and import ignored the
relationships.revoked flag, so restoring a backup authorized removed contacts
again. Import also skipped the max-validity clamp save_relationship applies.
"""
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.federation import (
    Capability, MAX_CERT_VALIDITY_SECONDS, RelationshipCertificate,
)
from proxion_messenger_core.local_store import LocalStore


def _raw(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


OWNER = Ed25519PrivateKey.generate()
OWNER_PUB = _raw(OWNER)
OWNER_DID = pub_key_to_did(OWNER_PUB)


def _cert(peer_priv, owner=OWNER, **kw):
    c = RelationshipCertificate(issuer=_raw(owner).hex(), subject=_raw(peer_priv).hex(),
                                capabilities=[Capability(with_="stash://dm/", can="crud/write")], **kw)
    c.sign(owner)
    return c


def _peer():
    p = Ed25519PrivateKey.generate()
    return p, pub_key_to_did(_raw(p))


def _roundtrip(src, dst, owner_pub=OWNER_PUB, owner_did=OWNER_DID):
    exp = json.loads(json.dumps(src.export_all(minimize=False)))
    return exp, dst.import_data(exp, owner_pub_hex=owner_pub.hex(), owner_webid=owner_did)


def test_removed_contact_stays_removed_after_import(tmp_path):
    src = LocalStore(str(tmp_path / "a.db"))
    kept, kept_did = _peer()
    gone, gone_did = _peer()
    c_kept, c_gone = _cert(kept), _cert(gone)
    src.save_relationship(c_kept.to_dict(), peer_did=kept_did, owner_webid=OWNER_DID)
    src.save_relationship(c_gone.to_dict(), peer_did=gone_did, owner_webid=OWNER_DID)
    # What _handle_revoke_contact does.
    src.mark_revoked(c_gone.certificate_id, gone_did)
    src.revoke_relationship(c_gone.certificate_id)
    src.revoke_relationships_for_peer(gone_did)

    dst = LocalStore(str(tmp_path / "b.db"))
    exp, counts = _roundtrip(src, dst)
    assert exp["revocations"] and "blocks" in exp
    assert dst.get_relationship_by_did(kept_did)
    assert dst.get_relationship_by_did(gone_did) is None
    assert dst.is_revoked(gone_did)
    assert gone_did in dst.get_revoked_dids()
    assert counts["relationships"] == 1 and counts["revocations"] == 1


def test_revoked_flag_alone_is_honoured(tmp_path):
    """An old cert left revoked after a re-add (no revocation row) is not revived."""
    src = LocalStore(str(tmp_path / "a.db"))
    bob, bob_did = _peer()
    old = _cert(bob)
    src.save_relationship(old.to_dict(), peer_did=bob_did, owner_webid=OWNER_DID)
    src.revoke_relationship(old.certificate_id)
    dst = LocalStore(str(tmp_path / "b.db"))
    _roundtrip(src, dst)
    assert dst.get_relationship_by_cert_id(old.certificate_id) is None


def test_import_skips_a_contact_revoked_on_this_gateway(tmp_path):
    """A backup taken before the removal cannot undo it."""
    src = LocalStore(str(tmp_path / "a.db"))
    bob, bob_did = _peer()
    src.save_relationship(_cert(bob).to_dict(), peer_did=bob_did, owner_webid=OWNER_DID)
    dst = LocalStore(str(tmp_path / "b.db"))
    dst.mark_revoked("cert-from-the-removal", bob_did)
    _, counts = _roundtrip(src, dst)
    assert counts["relationships"] == 0
    assert dst.get_relationship_by_did(bob_did) is None


def test_device_revocation_and_blocks_survive_import(tmp_path):
    src = LocalStore(str(tmp_path / "a.db"))
    src.mark_revoked("device:did:key:zDev", "did:key:zDev")
    src.set_block(OWNER_DID, "did:key:zPest", True)
    dst = LocalStore(str(tmp_path / "b.db"))
    _roundtrip(src, dst)
    assert dst.is_revoked("did:key:zDev")
    assert dst.is_blocked_by(OWNER_DID, "did:key:zPest")


def test_import_clamps_an_over_long_cert_expiry(tmp_path):
    src = LocalStore(str(tmp_path / "a.db"))
    bob, bob_did = _peer()
    now = int(time.time())
    long_cert = _cert(bob, created_at=now, expires_at=now + 10 * 365 * 86400)
    # Write the raw row as an older build would have, without the clamp.
    with src._conn() as c:
        c.execute("INSERT INTO relationships (certificate_id, peer_pub_hex, peer_did, cert_json, "
                  "created_at, expires_at, owner_webid) VALUES (?,?,?,?,?,?,?)",
                  (long_cert.certificate_id, long_cert.subject, bob_did,
                   json.dumps(long_cert.to_dict()), now, long_cert.expires_at, OWNER_DID))
    dst = LocalStore(str(tmp_path / "b.db"))
    _roundtrip(src, dst)
    with dst._conn() as c:
        stored = c.execute("SELECT expires_at FROM relationships WHERE certificate_id=?",
                           (long_cert.certificate_id,)).fetchone()[0]
    assert stored <= now + MAX_CERT_VALIDITY_SECONDS


from gwharness import free_port as _free_port


def test_http_import_applies_revocations_without_a_restart(tmp_path):
    from gwharness import start_gateway
    from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    agent = AgentState.generate()
    ws_port, http_port = _free_port(), _free_port()
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                                             public_url=f"ws://127.0.0.1:{ws_port}",
                                             db_path=str(tmp_path / "gw.db")),
                        read_state=ReadState())
    start_gateway(gw, ws_port, http_port)

    src = LocalStore(str(tmp_path / "a.db"))
    bob, bob_did = _peer()
    c = _cert(bob, owner=agent.identity_key)
    src.save_relationship(c.to_dict(), peer_did=bob_did)
    src.mark_revoked(c.certificate_id, bob_did)
    src.revoke_relationships_for_peer(bob_did)
    body = json.dumps(src.export_all(minimize=False))

    r = httpx.post(f"http://127.0.0.1:{http_port}/import", content=body,
                   headers={"Content-Type": "application/json"}, timeout=10)
    assert r.status_code == 200, r.text
    assert bob_did in gw._revoked_dids
    assert gw._store.get_relationship_by_did(bob_did) is None
