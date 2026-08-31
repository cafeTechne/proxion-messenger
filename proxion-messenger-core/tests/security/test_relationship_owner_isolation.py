"""C2: imported relationships are owner-scoped, not globally visible."""
import json

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "rel.db"))


def _export_with_relationship(cert_id, issuer, subject):
    cert = {"certificate_id": cert_id, "issuer": issuer, "subject": subject,
            "id": cert_id}
    return {
        "relationships": [
            {
                "certificate_id": cert_id,
                "peer_pub_hex": subject,
                "peer_did": "did:key:zPeer",
                "cert_json": json.dumps(cert),
                "created_at": 1000,
                "expires_at": 9999999999,
            }
        ]
    }


def test_imported_relationship_not_visible_to_other_account(store):
    account_a = "did:key:zAccountA"
    account_b = "did:key:zAccountB"
    data = _export_with_relationship("cert-a", issuer=account_a, subject="peerpub")
    counts = store.import_data(data, owner_pub_hex=account_a, owner_webid=account_a)
    assert counts["relationships"] == 1

    # Account A sees its imported cert.
    a_rels = store.list_relationships(owner_webid=account_a)
    assert any(r.get("certificate_id") == "cert-a" for r in a_rels)

    # Account B must NOT see account A's imported cert.
    b_rels = store.list_relationships(owner_webid=account_b)
    assert not any(r.get("certificate_id") == "cert-a" for r in b_rels)


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
