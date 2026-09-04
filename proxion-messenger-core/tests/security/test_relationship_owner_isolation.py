"""C2: imported relationships are owner-scoped, not globally visible.

Also covers the import-integrity hardening for a hostile backup file: the
certificate signature is verified and the peer identity is derived from the
verified cert, dm_threads/messages are scoped to the importing owner, and a
legitimate self-backup still restores end to end.
"""
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "rel.db"))


def _pub_hex(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _signed_rel(issuer_priv, subject_hex, cert_id):
    """Export row carrying a genuine issuer-signed certificate.

    The sibling ``peer_did`` is deliberately bogus so tests confirm the peer
    identity is taken from the verified cert, not the forgeable JSON field.
    """
    from proxion_messenger_core.federation import Capability, RelationshipCertificate

    cert = RelationshipCertificate(
        issuer=_pub_hex(issuer_priv),
        subject=subject_hex,
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        certificate_id=cert_id,
    )
    cert.sign(issuer_priv)
    return {
        "certificate_id": cert_id,
        "peer_pub_hex": subject_hex,
        "peer_did": "did:key:zBOGUS",
        "cert_json": json.dumps(cert.to_dict()),
        "created_at": cert.created_at,
        "expires_at": cert.expires_at,
    }


def test_imported_relationship_not_visible_to_other_account(store):
    owner = Ed25519PrivateKey.generate()
    owner_hex = _pub_hex(owner)
    owner_did = pub_key_to_did(owner.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    account_b = "did:key:zAccountB"
    peer = Ed25519PrivateKey.generate()
    peer_hex = _pub_hex(peer)

    data = {"relationships": [_signed_rel(owner, peer_hex, "cert-a")]}
    counts = store.import_data(data, owner_pub_hex=owner_hex, owner_webid=owner_did)
    assert counts["relationships"] == 1

    # Owner sees its imported cert.
    a_rels = store.list_relationships(owner_webid=owner_did)
    assert any(r.get("certificate_id") == "cert-a" for r in a_rels)

    # A different account must NOT see it.
    b_rels = store.list_relationships(owner_webid=account_b)
    assert not any(r.get("certificate_id") == "cert-a" for r in b_rels)


def test_import_derives_peer_did_from_verified_cert(store):
    """peer_did is computed from the cert subject, not the sibling JSON."""
    owner = Ed25519PrivateKey.generate()
    owner_hex = _pub_hex(owner)
    owner_did = pub_key_to_did(owner.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    peer = Ed25519PrivateKey.generate()
    peer_hex = _pub_hex(peer)
    peer_did = pub_key_to_did(peer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    data = {"relationships": [_signed_rel(owner, peer_hex, "cert-derive")]}
    store.import_data(data, owner_pub_hex=owner_hex, owner_webid=owner_did)

    assert store.get_relationship_by_did(peer_did) is not None
    assert store.get_relationship_by_did("did:key:zBOGUS") is None


def test_ownerless_backcompat_wildcard_default(store):
    """Legacy owner-less rows stay visible to any account by default
    (single-account back-compat), but are hidden when a caller opts into
    strict isolation."""
    # save_relationship with no owner leaves owner_webid=''.
    cert = {"certificate_id": "cert-legacy", "id": "cert-legacy",
            "subject": "peerpub", "created_at": 1000, "expires_at": 9999999999}
    store.save_relationship(cert, peer_did="did:key:zPeer")

    any_account = "did:key:zSomeone"
    default_view = store.list_relationships(owner_webid=any_account)
    assert any(r.get("certificate_id") == "cert-legacy" for r in default_view)

    strict_view = store.list_relationships(
        owner_webid=any_account, include_ownerless=False
    )
    assert not any(r.get("certificate_id") == "cert-legacy" for r in strict_view)


# ── B2: dm_threads stamped with the importing owner ────────────────────────


def test_import_dm_threads_stamped_with_importer(store):
    owner_did = "did:key:zImporter"
    other = "did:key:zVictim"
    data = {"dm_threads": [{
        "thread_id": "dm-1", "peer_webid": "did:key:zPeer",
        "display_name": "Peer", "owner_webid": other, "created_at": 5,
    }]}
    counts = store.import_data(data, owner_webid=owner_did)
    assert counts["dm_threads"] == 1
    assert any(t["thread_id"] == "dm-1" for t in store.get_dm_threads(owner_did))
    assert store.get_dm_threads(other) == []


# ── Imported messages keep imported=1 provenance (not owner-scoped by thread:
#    relay/federated history participates via a relationship, not a dm_thread) ──


def test_import_message_restored_for_own_dm_thread(store):
    owner_did = "did:key:zImporter"
    data = {
        "dm_threads": [{
            "thread_id": "dm-own", "peer_webid": "did:key:zPeer",
            "display_name": "Peer", "owner_webid": owner_did, "created_at": 1,
        }],
        "messages": [{
            "message_id": "m-own", "thread_id": "dm-own", "thread_type": "dm",
            "from_webid": "did:key:zPeer", "content": "hi",
            "timestamp": "2024-01-01T00:00:00Z",
        }],
    }
    counts = store.import_data(data, owner_webid=owner_did)
    assert counts["messages"] == 1
    assert any(m["message_id"] == "m-own" for m in store.get_messages("dm-own"))


# ── B4: display_names scoped to the owner's contacts ───────────────────────


def test_import_display_names_scoped_to_contacts(store):
    owner_did = "did:key:zImporter"
    data = {
        "dm_threads": [{
            "thread_id": "dm-c", "peer_webid": "did:key:zContact",
            "display_name": "Contact", "owner_webid": owner_did, "created_at": 1,
        }],
        "display_names": [
            {"webid": "did:key:zContact", "display_name": "Contact", "updated_at": 1},
            {"webid": owner_did, "display_name": "Me", "updated_at": 1},
            {"webid": "did:key:zStranger", "display_name": "Nope", "updated_at": 1},
        ],
    }
    counts = store.import_data(data, owner_webid=owner_did)
    assert counts["display_names"] == 2
    assert store.get_display_name("did:key:zContact") == "Contact"
    assert store.get_display_name(owner_did) == "Me"
    assert store.get_display_name("did:key:zStranger") is None


# ── Self-backup round-trip must still restore everything ───────────────────


def test_self_backup_round_trip_restores(tmp_path):
    owner = Ed25519PrivateKey.generate()
    owner_hex = _pub_hex(owner)
    owner_did = pub_key_to_did(owner.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    peer = Ed25519PrivateKey.generate()
    peer_hex = _pub_hex(peer)
    peer_did = pub_key_to_did(peer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    src = LocalStore(str(tmp_path / "src.db"))
    # A relationship the owner issued to the peer.
    from proxion_messenger_core.federation import Capability, RelationshipCertificate
    cert = RelationshipCertificate(
        issuer=owner_hex, subject=peer_hex,
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        certificate_id="cert-self",
    )
    cert.sign(owner)
    src.save_relationship(cert.to_dict(), peer_did=peer_did, owner_webid=owner_did)
    src.save_dm_thread("dm-self", peer_did, "Peer", owner_webid=owner_did)
    src.save_message("m-self", "dm-self", "dm", peer_did, "Peer", "hi", "2024-01-01T00:00:00Z")
    src.save_display_name(peer_did, "Peer")

    export = src.export_all(minimize=False)

    dst = LocalStore(str(tmp_path / "dst.db"))
    counts = dst.import_data(export, owner_pub_hex=owner_hex, owner_webid=owner_did)

    assert counts["relationships"] == 1
    assert counts["dm_threads"] == 1
    assert counts["messages"] == 1
    assert counts["display_names"] == 1
    assert dst.get_relationship_by_did(peer_did) is not None
    assert any(t["thread_id"] == "dm-self" for t in dst.get_dm_threads(owner_did))
    assert any(m["message_id"] == "m-self" for m in dst.get_messages("dm-self"))
    assert dst.get_display_name(peer_did) == "Peer"
