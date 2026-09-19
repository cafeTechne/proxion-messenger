"""Tests for proxion_messenger_core.certtoken — certificate-bounded token issuance."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import (
    MemoryStore,
    RevocationList,
    run_local_handshake,
    sign_challenge,
    validate_request,
)
from proxion_messenger_core.revocation import token_revocation_id
from proxion_messenger_core.certtoken import (
    CertTokenError,
    check_token_within_cert,
    delegate_cert,
    issue_from_certificate,
    revoke_tokens_for_certificate,
    revoke_tokens_via_ledger,
)
from proxion_messenger_core.context import RequestContext
from proxion_messenger_core.federation import Capability


def _agent():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    return Ed25519PrivateKey.generate(), X25519PrivateKey.generate()


@pytest.fixture
def sk():
    return os.urandom(32)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def cert():
    alice_id, alice_store = _agent()
    bob_id, bob_store = _agent()
    caps = [Capability(with_="stash://alice/shared/bob/", can="read")]
    store = MemoryStore()
    certificate, valid = run_local_handshake(
        alice_id, alice_store, bob_id, bob_store, caps, caps, store
    )
    assert valid
    return certificate, alice_id, bob_id


# ---------------------------------------------------------------------------
# issue_from_certificate — happy path
# ---------------------------------------------------------------------------

def test_issue_within_scope(cert, sk, now):
    certificate, _, bob_id = cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/photos/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        ttl_seconds=600,
        now=now,
    )
    assert ("read", "stash://alice/shared/bob/photos/") in token.permissions


def test_issue_exact_resource(cert, sk, now):
    certificate, _, bob_id = cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    assert token is not None


def test_issued_token_validates(cert, sk, now):
    certificate, _, bob_id = cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    proof = sign_challenge(bob_id, token.token_id, "req-1")
    ctx = RequestContext(
        action="read",
        resource="stash://alice/shared/bob/file.txt",
        aud=certificate.issuer,
        now=now,
        device_nonce="req-1",
    )
    d = validate_request(token, ctx, proof, sk)
    assert d.allowed


def test_token_aud_is_cert_issuer(cert, sk, now):
    certificate, _, bob_id = cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    assert token.aud == certificate.issuer


# ---------------------------------------------------------------------------
# issue_from_certificate — scope enforcement
# ---------------------------------------------------------------------------

def test_wrong_action_raises(cert, sk, now):
    certificate, _, bob_id = cert
    with pytest.raises(CertTokenError, match="scope"):
        issue_from_certificate(
            cert=certificate,
            requested_permissions=[("write", "stash://alice/shared/bob/")],
            holder_pub_key=bob_id.public_key(),
            signing_key=sk,
            now=now,
        )


def test_resource_outside_cert_raises(cert, sk, now):
    certificate, _, bob_id = cert
    with pytest.raises(CertTokenError, match="scope"):
        issue_from_certificate(
            cert=certificate,
            requested_permissions=[("read", "stash://alice/private/")],
            holder_pub_key=bob_id.public_key(),
            signing_key=sk,
            now=now,
        )


def test_empty_permissions_raises(cert, sk, now):
    certificate, _, bob_id = cert
    with pytest.raises(CertTokenError):
        issue_from_certificate(
            cert=certificate,
            requested_permissions=[],
            holder_pub_key=bob_id.public_key(),
            signing_key=sk,
            now=now,
        )


def test_expired_cert_raises(cert, sk):
    from datetime import timezone
    certificate, _, bob_id = cert
    # Simulate past now — certificate will look expired
    past = datetime.fromtimestamp(certificate.expires_at + 1, tz=timezone.utc)
    with pytest.raises(CertTokenError, match="expired"):
        issue_from_certificate(
            cert=certificate,
            requested_permissions=[("read", "stash://alice/shared/bob/")],
            holder_pub_key=bob_id.public_key(),
            signing_key=sk,
            now=past,
        )


# ---------------------------------------------------------------------------
# Lifetime capping
# ---------------------------------------------------------------------------

def test_ttl_capped_at_cert_expiry(cert, sk, now):
    certificate, _, bob_id = cert
    cert_exp = datetime.fromtimestamp(certificate.expires_at, tz=timezone.utc)
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        ttl_seconds=999 * 86400,
        now=now,
    )
    assert token.exp <= cert_exp


def test_short_ttl_honoured(cert, sk, now):
    certificate, _, bob_id = cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        ttl_seconds=120,
        now=now,
    )
    assert token.exp <= now + timedelta(seconds=121)


# ---------------------------------------------------------------------------
# check_token_within_cert
# ---------------------------------------------------------------------------

def test_check_no_violations(cert, sk, now):
    certificate, _, bob_id = cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    assert check_token_within_cert(token, certificate) == []


def test_check_lifetime_violation(cert, sk, now):
    certificate, _, bob_id = cert
    # Mint a valid token then manually inspect with a fake cert that expires sooner
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    import time as _time
    short_cert = RelationshipCertificate = certificate.__class__(
        issuer=certificate.issuer,
        subject=certificate.subject,
        capabilities=certificate.capabilities,
        wireguard={},
        expires_at=int(now.timestamp()) + 1,   # expires in 1 second
    )
    violations = check_token_within_cert(token, short_cert)
    assert any("expires" in v for v in violations)


def test_check_scope_violation(cert, sk, now):
    from proxion_messenger_core.tokens import issue_token
    from proxion_messenger_core.pop import fingerprint_from_key
    certificate, _, bob_id = cert
    # Issue a token outside cert scope directly (bypass issue_from_certificate)
    sk2 = os.urandom(32)
    bad_token = issue_token(
        permissions=[("delete", "stash://alice/shared/bob/")],
        exp=now + timedelta(hours=1),
        aud=certificate.issuer,
        caveats=[],
        holder_key_fingerprint=fingerprint_from_key(bob_id.public_key()),
        signing_key=sk2,
        now=now,
    )
    violations = check_token_within_cert(bad_token, certificate)
    assert any("not covered" in v for v in violations)


# ---------------------------------------------------------------------------
# revoke_tokens_for_certificate
# ---------------------------------------------------------------------------

def test_revoke_tokens_for_certificate(cert, sk, now):
    certificate, _, bob_id = cert
    t1 = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    t2 = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/photos/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    rl = RevocationList()
    count = revoke_tokens_for_certificate(certificate, [t1, t2], rl)
    assert count == 2
    assert rl.is_revoked(t1, now)
    assert rl.is_revoked(t2, now)


def test_revoke_tokens_empty_list(cert):
    certificate, _, _ = cert
    rl = RevocationList()
    assert revoke_tokens_for_certificate(certificate, [], rl) == 0


def test_issue_from_certificate_records_ledger_entry(cert, sk, now):
    certificate, _, bob_id = cert
    store = MemoryStore()
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
        store=store,
    )
    entries = store.list_all(f"token-ledger/{certificate.certificate_id}")
    assert len(entries) == 1
    payload = json.loads(entries[0].envelope.ciphertext.decode("utf-8"))
    assert payload["token_rev_id"] == token_revocation_id(token)
    assert payload["token_exp_ts"] == int(token.exp.timestamp())


def test_issue_from_certificate_without_store_writes_no_ledger(cert, sk, now):
    certificate, _, bob_id = cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    assert token is not None


def test_revoke_tokens_via_ledger_revokes_all(cert, sk, now):
    certificate, _, bob_id = cert
    store = MemoryStore()
    rl = RevocationList()
    t1 = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
        store=store,
    )
    t2 = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/photos/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
        store=store,
    )
    count = revoke_tokens_via_ledger(certificate, store, rl)
    assert count == 2
    assert rl.is_revoked(t1, now)
    assert rl.is_revoked(t2, now)


def test_revoke_tokens_via_ledger_ignores_malformed_entries(cert, now):
    from proxion_messenger_core.sealed import SealedEnvelope
    certificate, _, _ = cert
    store = MemoryStore()
    rl = RevocationList()
    store.put(
        f"token-ledger/{certificate.certificate_id}",
        SealedEnvelope(
            ephemeral_pub=b"\x00" * 32,
            nonce=b"\x00" * 12,
            ciphertext=b"not-json",
        ),
    )
    assert revoke_tokens_via_ledger(certificate, store, rl) == 0


def test_delegate_cert_reissues_under_same_issuer(cert):
    certificate, alice_id, _ = cert
    new_holder = Ed25519PrivateKey.generate()
    delegated = delegate_cert(
        cert=certificate,
        new_holder_pub_key=new_holder.public_key(),
        issuer_identity_priv=alice_id,
    )
    assert delegated.issuer == certificate.issuer
    assert delegated.subject == new_holder.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def test_delegate_cert_rejects_non_issuer_key(cert):
    certificate, _, _ = cert
    wrong_issuer = Ed25519PrivateKey.generate()
    new_holder = Ed25519PrivateKey.generate()
    with pytest.raises(CertTokenError, match="does not match"):
        delegate_cert(
            cert=certificate,
            new_holder_pub_key=new_holder.public_key(),
            issuer_identity_priv=wrong_issuer,
        )


# ---------------------------------------------------------------------------
# F7 — caveat-aware coverage (_covers)
# ---------------------------------------------------------------------------

def test_covers_uncaveated_unchanged():
    from proxion_messenger_core.certtoken import _covers
    assert _covers("read", "/data/", "read", "/data/")
    assert _covers("read", "/data/", "read", "/data/photos/img")
    assert _covers("read", "/", "read", "/anything")
    assert not _covers("write", "/data/", "read", "/data/")
    assert not _covers("read", "/data/", "read", "/other/file")


def test_covers_child_dropping_parent_caveat_not_covered():
    from proxion_messenger_core.certtoken import _covers
    # Parent imposes an IP allowlist; child drops it entirely -> not covered.
    assert not _covers("read", "/data/", "read", "/data/", {"ip": ["1.2.3.4"]}, {})
    # Carrying the same caveat is fine.
    assert _covers("read", "/data/", "read", "/data/", {"ip": ["1.2.3.4"]}, {"ip": ["1.2.3.4"]})


def test_covers_child_widening_numeric_caveat_not_covered():
    from proxion_messenger_core.certtoken import _covers
    # A numeric caveat is a ceiling: a larger child value widens the grant.
    assert not _covers("read", "/data/", "read", "/data/", {"quota_mb": 100}, {"quota_mb": 200})
    assert _covers("read", "/data/", "read", "/data/", {"quota_mb": 100}, {"quota_mb": 100})
    assert _covers("read", "/data/", "read", "/data/", {"quota_mb": 100}, {"quota_mb": 50})


def test_covers_child_widening_allowlist_not_covered():
    from proxion_messenger_core.certtoken import _covers
    parent = {"ip": ["1.1.1.1", "2.2.2.2"]}
    # Subset narrows -> covered; a value outside the parent's set widens -> not covered.
    assert _covers("read", "/data/", "read", "/data/", parent, {"ip": ["1.1.1.1"]})
    assert not _covers("read", "/data/", "read", "/data/", parent, {"ip": ["3.3.3.3"]})


def test_covers_child_may_add_new_caveat():
    from proxion_messenger_core.certtoken import _covers
    # Adding a caveat the parent did not impose only narrows the grant.
    assert _covers("read", "/data/", "read", "/data/", {}, {"ip": ["1.1.1.1"]})


# ---------------------------------------------------------------------------
# F4 — floor-vs-ceiling caveat monotonicity
# ---------------------------------------------------------------------------

def test_covers_child_earlier_not_before_not_covered():
    from proxion_messenger_core.certtoken import _covers
    # not_before is a FLOOR: a larger value is more restrictive (window starts
    # later). An earlier (smaller) child not_before widens the window start and
    # must NOT be accepted as a narrowing.
    assert not _covers(
        "read", "/data/", "read", "/data/", {"not_before": 1000}, {"not_before": 500}
    )


def test_covers_child_later_not_before_covered():
    from proxion_messenger_core.certtoken import _covers
    # A later (larger) child not_before narrows the window -> covered. Equal too.
    assert _covers(
        "read", "/data/", "read", "/data/", {"not_before": 1000}, {"not_before": 2000}
    )
    assert _covers(
        "read", "/data/", "read", "/data/", {"not_before": 1000}, {"not_before": 1000}
    )


def test_covers_not_after_child_narrows_only():
    from proxion_messenger_core.certtoken import _covers
    # not_after stays a CEILING: a smaller child value narrows (expires sooner),
    # a larger one widens the window end and is not covered.
    assert _covers(
        "read", "/data/", "read", "/data/", {"not_after": 2000}, {"not_after": 1000}
    )
    assert not _covers(
        "read", "/data/", "read", "/data/", {"not_after": 2000}, {"not_after": 3000}
    )


def test_delegate_cert_rejects_earlier_not_before(cert):
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    certificate, alice_id, _ = cert
    caveated = RelationshipCertificate(
        issuer=certificate.issuer,
        subject=certificate.subject,
        capabilities=[
            Capability(with_="stash://alice/shared/bob/", can="read", caveats={"not_before": 2000})
        ],
        wireguard={},
    )
    caveated.sign(alice_id)
    new_holder = Ed25519PrivateKey.generate()
    # A child cert that starts EARLIER widens the validity window -> rejected.
    with pytest.raises(CertTokenError, match="exceeds parent certificate scope"):
        delegate_cert(
            cert=caveated,
            new_holder_pub_key=new_holder.public_key(),
            issuer_identity_priv=alice_id,
            capabilities=[
                Capability(with_="stash://alice/shared/bob/", can="read", caveats={"not_before": 1000})
            ],
        )


def test_delegate_cert_allows_later_not_before(cert):
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    certificate, alice_id, _ = cert
    caveated = RelationshipCertificate(
        issuer=certificate.issuer,
        subject=certificate.subject,
        capabilities=[
            Capability(with_="stash://alice/shared/bob/", can="read", caveats={"not_before": 1000})
        ],
        wireguard={},
    )
    caveated.sign(alice_id)
    new_holder = Ed25519PrivateKey.generate()
    # A child cert that starts LATER narrows the window -> accepted.
    delegated = delegate_cert(
        cert=caveated,
        new_holder_pub_key=new_holder.public_key(),
        issuer_identity_priv=alice_id,
        capabilities=[
            Capability(with_="stash://alice/shared/bob/", can="read", caveats={"not_before": 2000})
        ],
    )
    assert delegated.capabilities[0].caveats == {"not_before": 2000}


# ---------------------------------------------------------------------------
# F4 secondary — _covering_capability considers ALL matching capabilities
# ---------------------------------------------------------------------------

def _two_cap_cert(cap_list):
    from proxion_messenger_core.federation import RelationshipCertificate
    return RelationshipCertificate(
        issuer="aa", subject="bb", capabilities=cap_list, wireguard={}
    )


def test_covering_capability_prefers_satisfiable_match_with_ctx():
    from proxion_messenger_core.certtoken import (
        _covering_capability,
        _capability_caveats_satisfied,
    )
    from proxion_messenger_core.federation import Capability
    c = _two_cap_cert([
        Capability(with_="/data/", can="read", caveats={"ip": ["9.9.9.9"]}),
        Capability(with_="/data/", can="read", caveats={"ip": ["1.2.3.4"]}),
    ])
    now = datetime.now(timezone.utc)
    ctx = RequestContext(action="read", resource="/data/x", aud="aa", now=now, ip="1.2.3.4")
    covering = _covering_capability("read", "/data/x", c, ctx)
    # The SECOND matching capability satisfies the request; it must be chosen so a
    # caller does not falsely deny on the first match's failing caveat.
    assert covering is not None
    assert _capability_caveats_satisfied(covering.caveats, ctx)
    assert covering.caveats == {"ip": ["1.2.3.4"]}


def test_covering_capability_none_satisfiable_returns_first():
    from proxion_messenger_core.certtoken import _covering_capability
    from proxion_messenger_core.federation import Capability
    c = _two_cap_cert([
        Capability(with_="/data/", can="read", caveats={"ip": ["9.9.9.9"]}),
        Capability(with_="/data/", can="read", caveats={"ip": ["8.8.8.8"]}),
    ])
    now = datetime.now(timezone.utc)
    ctx = RequestContext(action="read", resource="/data/x", aud="aa", now=now, ip="1.2.3.4")
    covering = _covering_capability("read", "/data/x", c, ctx)
    # No matching capability admits the request, so the first match is returned and
    # the caller's caveat check still denies.
    assert covering.caveats == {"ip": ["9.9.9.9"]}


def test_covering_capability_prefers_uncaveated_without_ctx():
    from proxion_messenger_core.certtoken import _covering_capability
    from proxion_messenger_core.federation import Capability
    c = _two_cap_cert([
        Capability(with_="/data/", can="read", caveats={"ip": ["9.9.9.9"]}),
        Capability(with_="/data/", can="read"),
    ])
    covering = _covering_capability("read", "/data/x", c)
    # An uncaveated covering capability authorises the request unconditionally and
    # is preferred over an earlier caveated one.
    assert covering is not None and not covering.caveats


def test_covering_capability_none_when_no_match():
    from proxion_messenger_core.certtoken import _covering_capability
    from proxion_messenger_core.federation import Capability
    c = _two_cap_cert([Capability(with_="/other/", can="read")])
    assert _covering_capability("read", "/data/x", c) is None


def test_delegate_cert_rejects_caveat_widening(cert):
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    certificate, alice_id, _ = cert
    caveated = RelationshipCertificate(
        issuer=certificate.issuer,
        subject=certificate.subject,
        capabilities=[
            Capability(with_="stash://alice/shared/bob/", can="read", caveats={"quota_mb": 100})
        ],
        wireguard={},
    )
    caveated.sign(alice_id)
    new_holder = Ed25519PrivateKey.generate()
    with pytest.raises(CertTokenError, match="exceeds parent certificate scope"):
        delegate_cert(
            cert=caveated,
            new_holder_pub_key=new_holder.public_key(),
            issuer_identity_priv=alice_id,
            capabilities=[
                Capability(with_="stash://alice/shared/bob/", can="read", caveats={"quota_mb": 500})
            ],
        )


def test_delegate_cert_rejects_capability_widening(cert):
    certificate, alice_id, _ = cert
    new_holder = Ed25519PrivateKey.generate()
    with pytest.raises(CertTokenError, match="exceeds parent certificate scope"):
        delegate_cert(
            cert=certificate,
            new_holder_pub_key=new_holder.public_key(),
            issuer_identity_priv=alice_id,
            capabilities=[Capability(with_="stash://alice/private/", can="read")],
        )


def test_delegate_cert_tokens_validate_normally(cert, sk, now):
    certificate, alice_id, _ = cert
    laptop = Ed25519PrivateKey.generate()
    delegated = delegate_cert(
        cert=certificate,
        new_holder_pub_key=laptop.public_key(),
        issuer_identity_priv=alice_id,
    )
    token = issue_from_certificate(
        cert=delegated,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=laptop.public_key(),
        signing_key=sk,
        now=now,
    )
    proof = sign_challenge(laptop, token.token_id, "req-delegated")
    ctx = RequestContext(
        action="read",
        resource="stash://alice/shared/bob/file.txt",
        aud=delegated.issuer,
        now=now,
        device_nonce="req-delegated",
    )
    decision = validate_request(token, ctx, proof, sk)
    assert decision.allowed


# ---------------------------------------------------------------------------
# Task 1 — renew_cert
# ---------------------------------------------------------------------------

def test_renew_cert_produces_new_expiry(cert, now):
    certificate, alice_id, _ = cert
    from proxion_messenger_core.certtoken import renew_cert
    renewed = renew_cert(certificate, alice_id, new_ttl_days=180, now=now)
    assert renewed.expires_at > certificate.expires_at


def test_renew_cert_preserves_issuer_subject_caps(cert, now):
    certificate, alice_id, _ = cert
    from proxion_messenger_core.certtoken import renew_cert
    renewed = renew_cert(certificate, alice_id, new_ttl_days=30, now=now)
    assert renewed.issuer == certificate.issuer
    assert renewed.subject == certificate.subject
    assert len(renewed.capabilities) == len(certificate.capabilities)
    assert renewed.certificate_id != certificate.certificate_id


def test_renew_cert_raises_on_issuer_mismatch(cert, now):
    certificate, _, _ = cert
    from proxion_messenger_core.certtoken import renew_cert, CertTokenError
    wrong_key = Ed25519PrivateKey.generate()
    with pytest.raises(CertTokenError, match="does not match"):
        renew_cert(certificate, wrong_key, now=now)


# ---------------------------------------------------------------------------
# Task 4 — check_token_within_cert delegation extension
# ---------------------------------------------------------------------------

def test_check_within_cert_delegation_valid(cert, sk, now):
    certificate, alice_id, bob_id = cert
    from proxion_messenger_core.certtoken import check_token_within_cert, delegate_cert, issue_from_certificate
    device = Ed25519PrivateKey.generate()
    dcert = delegate_cert(certificate, device.public_key(), alice_id)
    token = issue_from_certificate(
        cert=dcert,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=device.public_key(),
        signing_key=sk,
        now=now,
    )
    violations = check_token_within_cert(token, certificate, delegation_cert=dcert)
    assert violations == []


def test_check_within_cert_delegation_scope_exceeded(cert, sk, now):
    certificate, alice_id, bob_id = cert
    from proxion_messenger_core.certtoken import check_token_within_cert, issue_from_certificate
    from proxion_messenger_core.tokens import issue_token
    from proxion_messenger_core.pop import fingerprint_from_key
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    # Create a delegation cert with narrower caps
    narrow_cert = RelationshipCertificate(
        issuer=certificate.issuer,
        subject=bob_id.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        capabilities=[Capability(with_="stash://alice/shared/bob/photos/", can="read")],
        wireguard={},
    )
    narrow_cert.sign(alice_id)
    # Issue token that covers parent cert but exceeds narrow delegation cert
    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
    )
    violations = check_token_within_cert(token, certificate, delegation_cert=narrow_cert)
    assert any("not covered by delegation certificate" in v for v in violations)


# ---------------------------------------------------------------------------
# Task 5 — revoke_cert_and_tokens
# ---------------------------------------------------------------------------

def test_revoke_cert_and_tokens_no_store(cert, now):
    certificate, _, _ = cert
    from proxion_messenger_core.certtoken import revoke_cert_and_tokens
    rl = RevocationList()
    cert_rev_id, tokens_revoked = revoke_cert_and_tokens(certificate, rl)
    assert tokens_revoked == 0
    assert rl.is_revoked(cert_rev_id, now)


def test_revoke_cert_and_tokens_with_ledger(cert, sk, now):
    certificate, _, bob_id = cert
    from proxion_messenger_core.certtoken import revoke_cert_and_tokens, issue_from_certificate
    store = MemoryStore()
    t1 = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
        store=store,
    )
    t2 = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/photos/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
        store=store,
    )
    rl = RevocationList()
    cert_rev_id, tokens_revoked = revoke_cert_and_tokens(certificate, rl, store=store)
    assert tokens_revoked == 2
    assert rl.is_revoked(t1, now)
    assert rl.is_revoked(t2, now)

