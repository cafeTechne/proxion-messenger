"""Certificate-bounded token issuance.

This module is the bridge between the *federation* layer
(:class:`~proxion_messenger_core.federation.RelationshipCertificate`) and the
*capability-token* layer (:class:`~proxion_messenger_core.tokens.Token`).

Without this module the two layers are independent: a certificate can be
established via the handshake and tokens can be issued independently, but
nothing enforces that the token's permissions actually fall within what the
certificate grants.  This module provides that enforcement.

Conceptual model
----------------
A :class:`~proxion_messenger_core.federation.RelationshipCertificate` establishes a
bilateral trust relationship between an *issuer* (Alice) and a *subject*
(Bob).  It lists the high-level capabilities Bob may exercise
(e.g. ``crud/read`` on ``stash://alice/shared/bob``).

A :class:`~proxion_messenger_core.tokens.Token` is the fine-grained, short-lived
authorization artefact that Bob actually presents at a resource server.  Its
``permissions`` field is a set of ``(action, resource)`` pairs.

:func:`issue_from_certificate` mints a Token whose permissions are derived
from the certificate's ``Capability`` list.  It enforces three rules:

1. **Scope**: every ``(action, resource)`` in the requested permissions must
   be covered by at least one ``Capability`` in the certificate
   (action matches ``can``, resource is equal to or a sub-path of ``with_``).
2. **Lifetime**: the token's expiry cannot exceed the certificate's expiry.
3. **Subject binding**: the token's ``holder_key_fingerprint`` is set from the
   subject's Ed25519 public key, so the subject must prove possession via PoP
   before the token is accepted.

:func:`check_token_within_cert` performs the same scope and lifetime checks
without minting a new token — useful for a resource server that wants to
confirm that a presented token was legitimately derived from a specific
certificate before looking up the certificate in a trust store.

Revocation interaction
----------------------
Revoking the certificate (via :func:`~proxion_messenger_core.revoke.revoke_and_broadcast`)
does **not** automatically revoke tokens minted by :func:`issue_from_certificate`
in this EI0 implementation.  When an issuer revokes a certificate they SHOULD
also revoke all outstanding tokens minted under it.  Call
:func:`revoke_tokens_for_certificate` to do this in one step.

Example
-------
::

    # After completing the federation handshake:
    cert, _ = run_local_handshake(alice_id, alice_store, bob_id, bob_store,
                                  alice_caps, bob_caps, store)

    # Alice mints a short-lived read token for Bob:
    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[('read', 'stash://alice/shared/bob/photos/')],
        holder_pub_key=bob_id.public_key(),
        signing_key=alice_signing_key,
        ttl_seconds=3600,
    )

    # Bob presents the token at Alice's resource server:
    proof = sign_challenge(bob_id, token.token_id, request_nonce)
    decision = validate_request(token, ctx, proof, alice_signing_key, alice_rl)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .errors import ProxionError
from .federation import Capability, RelationshipCertificate
from .pop import fingerprint
from .revocation import RevocationList, certificate_revocation_id, token_revocation_id
from .sealed import SealedEnvelope
from .store import MemoryStore
from .tokens import Token, issue_token
from .context import Caveat


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CertTokenError(ProxionError):
    """Raised when a token cannot be minted or validated against a certificate."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cert_not_after(cert: RelationshipCertificate) -> datetime:
    """Return the certificate's expiry as a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(cert.expires_at, tz=timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _covers(cap_action: str, cap_resource: str, action: str, resource: str) -> bool:
    """Return True if a certificate Capability covers a requested (action, resource).

    Coverage rules:

    * The action must match exactly (case-sensitive).
    * The resource is covered if it equals ``cap_resource`` OR if
      ``cap_resource`` ends with ``"/"`` and ``resource`` starts with it
      (hierarchical sub-path check), OR if ``cap_resource == "/"``
      (wildcard root).

    These rules mirror those in :func:`~proxion_messenger_core.validator.validate_request`
    for consistency.
    """
    if cap_action != action:
        return False
    if cap_resource == resource:
        return True
    if cap_resource == "/":
        return True
    if cap_resource.endswith("/") and resource.startswith(cap_resource):
        return True
    return False


def _permission_covered(
    perm: Tuple[str, str],
    cert: RelationshipCertificate,
) -> bool:
    """Return True if *perm* is covered by at least one Capability in *cert*."""
    action, resource = perm
    for cap in cert.capabilities:
        if _covers(cap.can, cap.with_, action, resource):
            return True
    return False


# ---------------------------------------------------------------------------
# Scope and lifetime validation
# ---------------------------------------------------------------------------

def check_token_within_cert(
    token: Token,
    cert: RelationshipCertificate,
    delegation_cert: Optional[RelationshipCertificate] = None,
) -> List[str]:
    """Validate that *token* is within the scope and lifetime of *cert*.

    Does **not** verify the token's HMAC signature or revocation status —
    call :func:`~proxion_messenger_core.validator.validate_request` for those.  This
    function only checks that the permissions and expiry are consistent with
    the certificate's grants.

    Parameters
    ----------
    token:
        The token to validate.
    cert:
        The certificate that should bound this token.

    Returns
    -------
    list[str]
        A list of violation strings.  An empty list means the token is within
        bounds.  Each string describes one failed check, e.g.::

            ["permission ('write', 'stash://alice/x') not covered by certificate",
             "token expires 2026-06-01 after certificate expiry 2026-05-01"]
    """
    violations: List[str] = []

    cert_exp = _cert_not_after(cert)
    tok_exp = token.exp if token.exp.tzinfo else token.exp.replace(tzinfo=timezone.utc)

    if tok_exp > cert_exp:
        violations.append(
            f"token expires {tok_exp.date()} after certificate expiry {cert_exp.date()}"
        )

    for perm in token.permissions:
        if not _permission_covered(perm, cert):
            violations.append(
                f"permission {perm!r} not covered by certificate"
            )

    if delegation_cert is not None:
        if delegation_cert.issuer != cert.issuer:
            violations.append(
                "delegation cert issuer does not match root cert issuer"
            )
        for perm in token.permissions:
            if not _permission_covered(perm, delegation_cert):
                violations.append(
                    f"{perm!r} not covered by delegation certificate"
                )

    return violations


# ---------------------------------------------------------------------------
# Certificate-bounded token issuance
# ---------------------------------------------------------------------------

def issue_from_certificate(
    cert: RelationshipCertificate,
    requested_permissions: Iterable[Tuple[str, str]],
    holder_pub_key: Ed25519PublicKey,
    signing_key: bytes,
    ttl_seconds: int = 3600,
    caveats: Sequence[Caveat] = (),
    now: Optional[datetime] = None,
    store: Optional[MemoryStore] = None,
) -> Token:
    """Mint a capability token bounded by a :class:`~proxion_messenger_core.federation.RelationshipCertificate`.

    Enforces that:

    * Every requested permission is covered by the certificate's capabilities.
    * The token's expiry does not exceed the certificate's expiry.
    * The token is bound to the holder's Ed25519 public key via
      ``holder_key_fingerprint`` (so the holder must prove possession via PoP).

    Parameters
    ----------
    cert:
        The certificate that authorises this token.  Its ``capabilities``
        define the maximum scope; its ``expires_at`` defines the maximum
        lifetime.
    requested_permissions:
        An iterable of ``(action, resource)`` tuples describing what the
        token should permit.  Each pair must be covered by at least one
        ``Capability`` in *cert* — :exc:`CertTokenError` is raised otherwise.
    holder_pub_key:
        The Ed25519 public key of the token holder (typically the certificate
        subject's identity key).  Used to populate ``holder_key_fingerprint``.
    signing_key:
        32-byte HMAC key used to sign the token.  This is the issuer's
        symmetric token-signing key — it MUST be kept secret and should differ
        from any WireGuard or identity key.
    ttl_seconds:
        Desired token lifetime in seconds from *now*.  Capped at the remaining
        lifetime of the certificate if the certificate expires sooner.
        Default: 3600 (one hour).
    caveats:
        Additional :class:`~proxion_messenger_core.context.Caveat` constraints to embed
        in the token (e.g. IP allowlist, time window).
    now:
        Override the current time for testing.  Defaults to
        ``datetime.now(UTC)``.

    Returns
    -------
    Token
        A freshly minted token ready to be delivered to the holder.

    Raises
    ------
    CertTokenError
        If any requested permission is not covered by the certificate, or if
        the certificate is already expired.
    """
    now_dt = (now or _now_utc()).astimezone(timezone.utc)
    cert_exp = _cert_not_after(cert)

    if now_dt >= cert_exp:
        raise CertTokenError(
            f"certificate {cert.certificate_id[:8]}… expired at {cert_exp.isoformat()}"
        )

    perms = list(requested_permissions)
    if not perms:
        raise CertTokenError("requested_permissions must be non-empty")

    # Scope check — fail fast with a detailed error listing all violations.
    violations = [
        f"{perm!r} not covered by certificate"
        for perm in perms
        if not _permission_covered(perm, cert)
    ]
    if violations:
        raise CertTokenError(
            f"requested permissions exceed certificate scope: {'; '.join(violations)}"
        )

    # Cap the expiry at the certificate's natural end.
    desired_exp = now_dt + timedelta(seconds=ttl_seconds)
    token_exp = min(desired_exp, cert_exp)

    holder_fp = fingerprint(
        holder_pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    )

    token = issue_token(
        permissions=perms,
        exp=token_exp,
        aud=cert.issuer,      # audience = Alice's identity pub key (issuer of the cert)
        caveats=list(caveats),
        holder_key_fingerprint=holder_fp,
        signing_key=signing_key,
        now=now_dt,
    )
    if store is not None:
        mailbox = f"token-ledger/{cert.certificate_id}"
        ledger_entry = {
            "token_rev_id": token_revocation_id(token),
            "token_exp_ts": int(token.exp.timestamp()),
        }
        envelope = SealedEnvelope(
            ephemeral_pub=b"\x00" * 32,
            nonce=b"\x00" * 12,
            ciphertext=json.dumps(ledger_entry, separators=(",", ":")).encode("utf-8"),
        )
        store.put(mailbox, envelope)
    return token


def revoke_tokens_via_ledger(
    cert: RelationshipCertificate,
    store: MemoryStore,
    revocation_list: RevocationList,
) -> int:
    """Revoke all token IDs recorded in the certificate's token ledger mailbox."""
    mailbox = f"token-ledger/{cert.certificate_id}"
    entries = store.take_all(mailbox)
    cert_exp = _cert_not_after(cert)
    count = 0
    for sm in entries:
        try:
            payload = json.loads(sm.envelope.ciphertext.decode("utf-8"))
            rev_id = str(payload["token_rev_id"])
            exp_ts = int(payload["token_exp_ts"])
            tok_exp = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        except Exception:
            continue
        revocation_list.revoke_until(rev_id, min(tok_exp, cert_exp))
        count += 1
    return count


def delegate_cert(
    cert: RelationshipCertificate,
    new_holder_pub_key: Ed25519PublicKey,
    issuer_identity_priv: Ed25519PrivateKey,
    capabilities: Optional[Sequence[Capability]] = None,
    expires_at: Optional[int] = None,
) -> RelationshipCertificate:
    """Issue a sub-certificate for a second holder under the same issuer.

    This is issuer-mediated delegation: only the original issuer can create
    delegated certs. Subject-side unilateral delegation is not supported.
    """
    issuer_pub_hex = issuer_identity_priv.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    ).hex()
    if issuer_pub_hex != cert.issuer:
        raise CertTokenError("issuer_identity_priv does not match certificate issuer")

    delegated_caps = list(capabilities) if capabilities is not None else list(cert.capabilities)
    for dcap in delegated_caps:
        if not any(_covers(p.can, p.with_, dcap.can, dcap.with_) for p in cert.capabilities):
            raise CertTokenError(
                f"delegated capability {(dcap.can, dcap.with_)!r} exceeds parent certificate scope"
            )

    delegated_expires_at = cert.expires_at if expires_at is None else min(expires_at, cert.expires_at)
    delegated = RelationshipCertificate(
        issuer=cert.issuer,
        subject=new_holder_pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        capabilities=delegated_caps,
        wireguard=dict(cert.wireguard or {}),
        expires_at=delegated_expires_at,
    )
    delegated.sign(issuer_identity_priv)
    return delegated


# ---------------------------------------------------------------------------
# Certificate renewal
# ---------------------------------------------------------------------------

def renew_cert(
    cert: RelationshipCertificate,
    issuer_identity_priv: Ed25519PrivateKey,
    new_ttl_days: int = 90,
    now: Optional[datetime] = None,
) -> RelationshipCertificate:
    """Re-issue a RelationshipCertificate with a fresh expiry.

    Parameters
    ----------
    cert:
        The certificate to renew.
    issuer_identity_priv:
        The issuer's Ed25519 private key — must match ``cert.issuer``.
    new_ttl_days:
        Lifetime of the renewed certificate in days (default: 90).
    now:
        Override "current time" for testing.  Defaults to ``datetime.now(UTC)``.

    Returns
    -------
    RelationshipCertificate
        A freshly signed certificate with a new ID and updated expiry,
        preserving ``issuer``, ``subject``, ``capabilities``, and ``wireguard``.

    Raises
    ------
    CertTokenError
        If ``issuer_identity_priv`` does not match ``cert.issuer``.
    """
    issuer_pub_hex = issuer_identity_priv.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    ).hex()
    if issuer_pub_hex != cert.issuer:
        raise CertTokenError("issuer_identity_priv does not match certificate issuer")

    now_dt = (now or _now_utc()).astimezone(timezone.utc)
    new_expires_at = int((now_dt + timedelta(days=new_ttl_days)).timestamp())

    renewed = RelationshipCertificate(
        issuer=cert.issuer,
        subject=cert.subject,
        capabilities=list(cert.capabilities),
        wireguard=dict(cert.wireguard or {}),
        expires_at=new_expires_at,
        certificate_id=str(uuid.uuid4()),
    )
    renewed.sign(issuer_identity_priv)
    return renewed


# ---------------------------------------------------------------------------
# Certificate-scoped bulk revocation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cascade revocation convenience
# ---------------------------------------------------------------------------

def revoke_cert_and_tokens(
    cert: RelationshipCertificate,
    revocation_list: RevocationList,
    store: Optional[MemoryStore] = None,
) -> Tuple[str, int]:
    """Revoke the certificate and all tokens in the ledger in one call.

    Returns
    -------
    (cert_rev_id, tokens_revoked)
        ``cert_rev_id`` is the revocation ID added to ``revocation_list``.
        ``tokens_revoked`` is 0 if no store was provided.
    """
    cert_exp = _cert_not_after(cert)
    cert_rev_id = certificate_revocation_id(cert)
    revocation_list.revoke_until(cert_rev_id, cert_exp)
    tokens_revoked = 0
    if store is not None:
        tokens_revoked = revoke_tokens_via_ledger(cert, store, revocation_list)
    return cert_rev_id, tokens_revoked


def revoke_tokens_for_certificate(
    cert: RelationshipCertificate,
    tokens: Iterable[Token],
    revocation_list: RevocationList,
) -> int:
    """Revoke all *tokens* that were issued under *cert* in the local revocation list.

    This implements local cascade revocation: when a certificate is revoked,
    call this function with every token minted by
    :func:`issue_from_certificate` for that certificate to ensure they are
    all denied immediately on the local resource server.

    For propagation to peers, call
    :func:`~proxion_messenger_core.revoke.revoke_and_broadcast` for each token
    individually (or batch them with
    :func:`~proxion_messenger_core.revoke.broadcast_revocation`).

    Parameters
    ----------
    cert:
        The revoked certificate — used to derive the ``not_after`` bound for
        each revocation entry.
    tokens:
        The tokens to revoke.  Typically obtained from an issuer-side token
        store that indexes by ``certificate_id``.
    revocation_list:
        The local revocation list to update.

    Returns
    -------
    int
        The number of tokens revoked.
    """
    cert_exp = _cert_not_after(cert)
    count = 0
    for token in tokens:
        rev_id = token_revocation_id(token)
        # Use the earlier of the token's own expiry and the certificate's expiry.
        tok_exp = token.exp if token.exp.tzinfo else token.exp.replace(tzinfo=timezone.utc)
        until = min(tok_exp, cert_exp)
        revocation_list.revoke_until(rev_id, until)
        count += 1
    return count
