"""RS-side validation for capability tokens."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .context import RequestContext
from .federation import RelationshipCertificate
from .pop import PopProof, verify_pop
from .tokens import Token, verify_integrity
from .revocation import RevocationList


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: Optional[str] = None


ALLOW = Decision(True, None)


def _deny(reason: str) -> Decision:
    return Decision(False, reason)


def _default_pop_check(token: Token, proof: object) -> bool:
    """Default PoP verifier: requires a :class:`~proxion_messenger_core.pop.PopProof`.

    Callers that need a custom scheme (e.g. WebAuthn, mTLS) can supply their
    own ``proof_verifier`` to :func:`validate_request` instead.
    """
    if not isinstance(proof, PopProof):
        return False
    return verify_pop(token, proof)


def _cert_sig_ok(cert: RelationshipCertificate) -> bool:
    """Return True if the delegation cert's Ed25519 signature checks out."""
    try:
        import json as _json
        data = cert.to_dict()
        data.pop("signature", None)
        canonical = _json.dumps(data, sort_keys=True).encode()
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(cert.issuer))
        pub.verify(bytes.fromhex(cert.signature), canonical)
        return True
    except Exception:
        return False


def validate_request(
    token: Token,
    ctx: RequestContext,
    proof: object,
    signing_key: bytes,
    revocation_list: Optional[RevocationList] = None,
    proof_verifier: Optional[Callable[[Token, RequestContext, object], bool]] = None,
    cert: Optional[RelationshipCertificate] = None,
    parent_token: Optional[Token] = None,
    *,
    receipt_writer: Optional[Callable[["Token", "RequestContext", "Decision"], None]] = None,
    delegation_cert: Optional[RelationshipCertificate] = None,
) -> Decision:
    try:
        def _decide(d: Decision) -> Decision:
            if receipt_writer is not None:
                try:
                    receipt_writer(token, ctx, d)
                except Exception:
                    pass
            return d

        # Certificate expiry check — if the backing cert is known and expired, deny.
        if cert is not None:
            cert_exp_ts = cert.expires_at
            now_ts = ctx.now.timestamp()
            if now_ts >= cert_exp_ts:
                return _decide(_deny("cert_expired"))
        if revocation_list is not None:
            try:
                if revocation_list.is_revoked(token, ctx.now):
                    return _decide(_deny("revoked"))
            except Exception:
                return _decide(_deny("revocation_error"))
        verify_integrity(token, signing_key)
        # Delegation chain check — if parent_token is provided, verify the link
        if parent_token is not None:
            if token.parent_token_id != parent_token.token_id:
                return _decide(_deny("chain_token_id_mismatch"))
            if not token.permissions.issubset(parent_token.permissions):
                return _decide(_deny("chain_permission_widening"))

        # Delegation cert check — validate sub-cert signature, subject, and scope
        if delegation_cert is not None:
            # 1. Check delegation cert expiry
            if delegation_cert.expires_at <= ctx.now.timestamp():
                return _decide(_deny("delegation_cert_expired"))
            # 2. Verify the delegation cert's Ed25519 signature
            if not _cert_sig_ok(delegation_cert):
                return _decide(_deny("delegation_cert_invalid_signature"))
            # 3. Confirm the presenter's public key matches the delegation cert subject
            if isinstance(proof, PopProof):
                presenter_pub_hex = proof.public_key_bytes.hex()
            else:
                presenter_pub_hex = None
            if presenter_pub_hex != delegation_cert.subject:
                return _decide(_deny("delegation_cert_subject_mismatch"))
            # 4. Confirm token permissions ⊆ delegation cert capabilities
            from .certtoken import _permission_covered  # safe: no circular dep
            for perm in token.permissions:
                if not _permission_covered(perm, delegation_cert):
                    return _decide(_deny("delegation_cert_scope_exceeded"))

        if ctx.now >= token.exp:
            return _decide(_deny("expired"))
        if token.aud != ctx.aud:
            return _decide(_deny("audience_mismatch"))
        if proof_verifier is not None:
            if not proof_verifier(token, ctx, proof):
                return _decide(_deny("invalid_proof"))
        else:
            if not _default_pop_check(token, proof):
                return _decide(_deny("invalid_proof"))
        # Permission Check (with Prefix Support)
        allowed_by_permission = False
        for p_action, p_resource in token.permissions:
            if p_action == ctx.action:
                if p_resource == ctx.resource:
                    allowed_by_permission = True
                    break
                # Hierarchical check: if permission is for /data/ and resource is /data/photos
                if p_resource.endswith("/") and ctx.resource.startswith(p_resource):
                    allowed_by_permission = True
                    break
                # Root wildcard
                if p_resource == "/":
                    allowed_by_permission = True
                    break

        if not allowed_by_permission:
            return _decide(_deny("permission_missing"))
        for caveat in token.caveats:
            try:
                if not caveat.evaluate(ctx):
                    return _decide(_deny("caveat_failed"))
            except Exception:
                return _decide(_deny("caveat_error"))
        return _decide(ALLOW)
    except Exception as exc:
        _ = exc
        return _deny("error")
