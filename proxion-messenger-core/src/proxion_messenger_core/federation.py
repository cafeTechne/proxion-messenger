import json
import uuid
import time
import hashlib
import secrets
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any

# R86: call-binding capability advertisement. A modern gateway issues per-browser
# delegation certs so its users' calls can be identity-verified across gateways; it
# advertises that in the signed invite and relationship cert so a contact knows, from
# the relationship alone, that this party binds its calls. A call from such a contact
# that arrives with no binding proof is then treated as a downgrade, not a legacy client.
# Two markers on a cert, one per role, so a single shared cert tells both parties:
#   CALL_BINDING_CAP        — the artifact's issuer/signer (invite issuer, cert issuer) binds calls
#   CALL_BINDING_PEER_CAP   — the cert's SUBJECT binds calls (set by the issuer from the invite)
# They ride in the existing signed `capabilities` list, so old peers preserve them in the
# signed canonical (signatures still verify) and simply ignore them. See PLAN_ROUND_86.
CALL_BINDING_CAP = "proxion://cap/call-binding"
CALL_BINDING_PEER_CAP = "proxion://cap/call-binding/peer"

# Signature context for the handshake challenge_response (the acceptor signs the
# invite's challenge_marker). Shared with handshake.py so both sides agree.
HANDSHAKE_CHALLENGE_CTX = b"proxion-handshake-v1:"

# Signature context for a RelationshipCertificate's SUBJECT consent. The subject
# signs a binding over issuer||subject with its own identity key, making the
# subject's consent to THIS pairing durable and non-transferable: because the
# signed message names the issuer, the proof cannot be lifted out of one cert and
# replayed under a different (attacker-chosen) issuer. This is the R113 fix — an
# issuer-only cert is a single-party token the counterparty fully controls, so any
# ingest that treats "our owner is the subject" as authorization must instead
# require a subject signature only the real owner could have produced.
SUBJECT_CONSENT_CTX = b"proxion-rel-subject-consent-v1:"

# Cert-bound subject consent (R115). The v1 message above names only the
# (issuer, subject) pair, so a counter-signature authorizes ANY cert with that
# pair: an issuer who keeps a subject's old signature can re-issue a fresh
# certificate (new certificate_id, reset expiry, chosen capabilities) after the
# subject revoked, re-attach the retained signature, and verify_mutual would
# still pass (a revoked relationship resurrected). The v2 message additionally
# names the specific certificate_id and a digest of the capability set, so one
# counter-signature authorizes exactly one certificate and cannot be transplanted
# onto a re-minted one. created_at/expires_at are deliberately NOT bound: the
# subject counter-signs during the handshake (accept_invite), before the issuer
# has built the certificate and chosen its validity window, so those values are
# not known to the signer. Binding certificate_id already pins the cert identity;
# re-minting under the same id is blocked separately at ingest (revoked ids stay
# revoked).
SUBJECT_CONSENT_CTX_V2 = b"proxion-rel-subject-consent-v2:"

# The consent scheme a cert's subject_signature uses. New certs stamp this into
# the issuer-signed body so it cannot be stripped to force the legacy pair-only
# verification path (a downgrade): removing it changes the issuer canonical and
# breaks the issuer signature. A cert with no consent_version predates R115 and
# is verified against the legacy pair message.
CONSENT_VERSION = 2


def subject_consent_message(issuer_hex: str, subject_hex: str) -> bytes:
    """The legacy (v1) bytes a cert subject signs to prove consent to the pairing."""
    return SUBJECT_CONSENT_CTX + issuer_hex.encode() + b"|" + subject_hex.encode()


def capabilities_digest(capabilities) -> str:
    """Stable hex digest of a capability set (Capability objects OR plain dicts).

    Order is preserved: the handshake carries the subject's capability list into
    the issued cert unchanged, so the subject and the verifier hash the same
    sequence. Only the (with, can, caveats) triple of each entry is covered.
    """
    items = []
    for c in capabilities or []:
        if isinstance(c, dict):
            with_ = c.get("with") or c.get("with_") or ""
            can = c.get("can", "")
            caveats = c.get("caveats", {})
        else:
            with_ = getattr(c, "with_", "") or ""
            can = getattr(c, "can", "")
            caveats = getattr(c, "caveats", {})
        items.append({"with": with_, "can": can, "caveats": caveats})
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def subject_consent_message_v2(
    issuer_hex: str, subject_hex: str, certificate_id, caps_digest: str
) -> bytes:
    """The cert-bound (v2) bytes a cert subject signs to prove consent (R115).

    Binds the pairing to one specific certificate_id and capability set so the
    counter-signature authorizes exactly that certificate.
    """
    return (
        SUBJECT_CONSENT_CTX_V2
        + issuer_hex.encode()
        + b"|"
        + subject_hex.encode()
        + b"|"
        + str(certificate_id or "").encode()
        + b"|"
        + caps_digest.encode()
    )


def call_binding_capability(peer: bool = False) -> "Capability":
    """The marker capability advertising call-binding support (issuer, or subject if peer)."""
    return Capability(with_=CALL_BINDING_PEER_CAP if peer else CALL_BINDING_CAP, can="v1")


def _caps_have(caps, with_uri: str) -> bool:
    """True if a capabilities list (Capability objects OR plain dicts) contains with_uri."""
    for c in caps or []:
        got = getattr(c, "with_", None) if not isinstance(c, dict) else (c.get("with") or c.get("with_"))
        if got == with_uri:
            return True
    return False


def cert_dict_peer_binds_calls(cert_dict: dict, peer_pub_hex: str) -> bool:
    """Whether the PEER (identified by pubkey hex) in a stored cert advertises call binding.

    The peer may be the cert issuer or subject; check the marker for their role.
    """
    caps = cert_dict.get("capabilities", [])
    if cert_dict.get("issuer") == peer_pub_hex:
        return _caps_have(caps, CALL_BINDING_CAP)
    if cert_dict.get("subject") == peer_pub_hex:
        return _caps_have(caps, CALL_BINDING_PEER_CAP)
    return False

def cert_authorizes_owner(cert: "RelationshipCertificate", owner_pub_hex: str, verifier_func) -> bool:
    """Whether *cert* may be saved as a relationship row for this owner (R113).

    A stored relationship row IS the authorization (it grants the peer
    DM/reaction/file/voice), so every ingest path must confirm the owner actually
    consented before writing one:

    * owner is the cert ISSUER  -> the owner issued it; the issuer signature
      (``verify``) is sufficient.
    * owner is the cert SUBJECT -> the issuer signature alone is a single-party
      token the counterparty controls, so require the durable subject
      counter-signature by the owner's own key (``verify_mutual``). Only the real
      owner could have produced it, closing the hostile-import residual.
    * owner is neither party    -> refuse.

    When *owner_pub_hex* is empty the owner-party filter is not applied and only
    the issuer signature is checked (legacy no-owner ingest).
    """
    if not owner_pub_hex:
        return cert.verify(verifier_func)
    if owner_pub_hex == cert.issuer:
        return cert.verify(verifier_func)
    if owner_pub_hex == cert.subject:
        return cert.verify_mutual(verifier_func)
    return False


def _normalize_endpoint_hints(hints: list) -> list:
    """Normalize endpoint hints: trim, lowercase scheme+host, remove trailing slash, deduplicate."""
    import urllib.parse as _up
    seen = []
    seen_set = set()
    for hint in hints:
        if not isinstance(hint, str):
            continue
        h = hint.strip()
        try:
            parsed = _up.urlparse(h)
            # lowercase scheme and host
            normalized = _up.urlunparse((
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/"),
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))
            # ensure we have at least scheme://host
            if not normalized or normalized not in seen_set:
                seen_set.add(normalized)
                seen.append(normalized)
        except Exception:
            continue
    return seen

@dataclass
class Capability:
    """UCAN-style capability."""
    with_: str  # Resource URI (e.g. stash://alice/shared/bob)
    can: str    # Action (e.g. crud/read)
    caveats: Dict[str, Any] = field(default_factory=dict) # quota_mb, etc.

    def to_dict(self):
        return {"with": self.with_, "can": self.can, "caveats": self.caveats}

    @classmethod
    def from_dict(cls, d: dict) -> "Capability":
        return cls(
            with_=d.get("with") or d.get("with_"),
            can=d["can"],
            caveats=d.get("caveats", {}),
        )

@dataclass
class FederationInvite:
    """A signed invitation to federate."""
    issuer: Dict[str, str] # {public_key, did}
    endpoint_hints: List[str]
    capabilities: List[Capability]
    
    version: int = 1
    invitation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: int = field(default_factory=lambda: int(time.time()))
    expires_at: int = field(default_factory=lambda: int(time.time()) + 86400)
    nonce: str = field(default_factory=lambda: secrets.token_hex(32))
    challenge_marker: str = field(default_factory=lambda: secrets.token_hex(32))
    certificate_id: Optional[str] = None
    signature: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "@type": "FederationInvite",
            "version": self.version,
            "invitation_id": self.invitation_id,
            "issuer": self.issuer,
            "endpoint_hints": self.endpoint_hints,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "challenge_marker": self.challenge_marker,
            "certificate_id": self.certificate_id,
            "signature": self.signature
        }

    def sign(self, identity_key):
        """Sign the invite with Identity Key."""
        data = self.to_dict()
        if 'signature' in data: del data['signature']
        
        canonical = json.dumps(data, sort_keys=True)
        # Assuming identity_key has .sign() returning hex or bytes
        # For simplicity in this mock, we use a placeholder if no key provided
        if hasattr(identity_key, 'sign'):
            sig_bytes = identity_key.sign(canonical.encode())
            self.signature = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)

    def verify(self, verifier_func) -> bool:
        """Verify signature using a provided verifier function (pubkey, sig, data)."""
        if not self.signature: return False
        data = self.to_dict()
        del data['signature']
        canonical = json.dumps(data, sort_keys=True)
        return verifier_func(self.issuer['public_key'], bytes.fromhex(self.signature), canonical.encode())

    @classmethod
    def from_dict(cls, d: dict, strict: bool = False) -> "FederationInvite":
        # Normalize endpoint hints (trim, lowercase scheme+host, trailing-slash, dedup)
        raw_hints = d.get("endpoint_hints", [])
        normalized_hints = _normalize_endpoint_hints(raw_hints)
        if raw_hints and not normalized_hints:
            raise ValueError("invalid_endpoint_hints: normalized list is empty")

        if strict:
            # Check for unknown top-level fields
            allowed_fields = {
                "issuer", "endpoint_hints", "capabilities", "version",
                "invitation_id", "invite_id", "created_at", "expires_at",
                "nonce", "challenge_marker", "certificate_id", "signature",
                "@type"
            }
            unknown = set(d.keys()) - allowed_fields
            if unknown:
                raise ValueError(f"unknown_invite_fields: {', '.join(sorted(unknown))}")

            # Bounds checking for invitation_id
            invitation_id = d.get("invitation_id") or d.get("invite_id")
            if invitation_id and len(str(invitation_id)) > 64:
                raise ValueError("invalid_certificate_policy: invitation_id too long")

            # Validate endpoint_hints
            hints = d.get("endpoint_hints", [])
            if len(hints) > 10:
                raise ValueError("invalid_endpoint_hints: too many endpoints")
            for hint in hints:
                if not isinstance(hint, str) or len(hint) > 256:
                    raise ValueError("invalid_endpoint_hints: endpoint too long")
                if not hint.startswith(("http://", "https://")):
                    raise ValueError("invalid_endpoint_hints: must use http/https")

            # Validate nonce format
            nonce = d.get("nonce")
            if nonce:
                import re as _re
                if not _re.match(r"^[0-9a-fA-F]{32,128}$", nonce):
                    raise ValueError("invalid_nonce_format")

            # Validate challenge_marker format
            challenge = d.get("challenge_marker")
            if challenge:
                import re as _re
                if not _re.match(r"^[0-9a-fA-F]{32,128}$", challenge):
                    raise ValueError("invalid_nonce_format")

        caps = [Capability.from_dict(c) for c in d.get("capabilities", [])]
        obj = cls(
            issuer=d.get("issuer", {}),
            endpoint_hints=normalized_hints,
            capabilities=caps,
        )
        obj.version = d.get("version", 1)
        obj.invitation_id = d.get("invitation_id") or d.get("invite_id") or str(uuid.uuid4())
        obj.created_at = d.get("created_at") or int(time.time())
        obj.expires_at = d.get("expires_at") or (obj.created_at + 86400)
        obj.nonce = d.get("nonce") or secrets.token_hex(32)
        obj.challenge_marker = d.get("challenge_marker") or secrets.token_hex(32)
        obj.certificate_id = d.get("certificate_id")
        obj.signature = d.get("signature")
        return obj

@dataclass
class InviteAcceptance:
    """Response to an invite, proving possession."""
    invitation_id: str
    responder: Dict[str, Any] # {public_key, endpoint_hints}
    challenge_response: str   # Signature of challenge_marker

    timestamp: int = field(default_factory=lambda: int(time.time()))
    signature: Optional[str] = None
    # Durable proof that the responder (the future cert SUBJECT) consented to the
    # pairing: an Ed25519 signature over subject_consent_message(issuer, subject).
    # The issuer copies it into the RelationshipCertificate it builds, so the
    # subject's consent survives past the ephemeral handshake. See R113.
    subject_consent: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "@type": "InviteAcceptance",
            "invitation_id": self.invitation_id,
            "responder": self.responder,
            "challenge_response": self.challenge_response,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "subject_consent": self.subject_consent,
        }

    def sign(self, identity_key):
        data = self.to_dict()
        if 'signature' in data: del data['signature']
        canonical = json.dumps(data, sort_keys=True)
        if hasattr(identity_key, 'sign'):
             sig_bytes = identity_key.sign(canonical.encode())
             self.signature = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)

    def verify(self, verifier_func) -> bool:
        """Verify the responder's signature on the acceptance data."""
        if not self.signature: return False
        data = self.to_dict()
        del data['signature']
        canonical = json.dumps(data, sort_keys=True)
        return verifier_func(self.responder['public_key'], bytes.fromhex(self.signature), canonical.encode())

    def verify_challenge(self, verifier_func, challenge_marker: str) -> bool:
        """Verify the signature on the challenge_marker."""
        return verifier_func(self.responder['public_key'], bytes.fromhex(self.challenge_response), HANDSHAKE_CHALLENGE_CTX + challenge_marker.encode())

    @classmethod
    def from_dict(cls, d: dict, strict: bool = False) -> "InviteAcceptance":
        if strict:
            # Check for unknown top-level fields
            allowed_fields = {
                "invitation_id", "responder", "challenge_response",
                "timestamp", "signature", "subject_consent", "@type"
            }
            unknown = set(d.keys()) - allowed_fields
            if unknown:
                raise ValueError(f"unknown_invite_fields: {', '.join(sorted(unknown))}")

            # Bounds checking for invitation_id
            invitation_id = d.get("invitation_id")
            if invitation_id and len(str(invitation_id)) > 64:
                raise ValueError("invalid_certificate_policy: invitation_id too long")

            # Validate challenge_response format
            challenge_resp = d.get("challenge_response")
            if challenge_resp:
                import re as _re
                if not _re.match(r"^[0-9a-fA-F]{32,512}$", challenge_resp):
                    raise ValueError("invalid_nonce_format")

        obj = cls(
            invitation_id=d["invitation_id"],
            responder=d["responder"],
            challenge_response=d["challenge_response"],
        )
        obj.timestamp = d.get("timestamp", 0)
        obj.signature = d.get("signature")
        obj.subject_consent = d.get("subject_consent")
        return obj

@dataclass
class RelationshipCertificate:
    """The mutual capability token."""
    issuer: str # pubkey
    subject: str # pubkey
    capabilities: List[Capability]

    version: int = 1
    certificate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: int = field(default_factory=lambda: int(time.time()))
    expires_at: int = field(default_factory=lambda: int(time.time()) + (90 * 86400)) # 90 days
    wireguard: Dict[str, Any] = field(default_factory=dict)
    signature: Optional[str] = None
    # Counter-signature by the SUBJECT's identity key. For a v2 cert this covers
    # subject_consent_message_v2 (issuer||subject||certificate_id||caps_digest);
    # for a legacy cert it covers the pair-only subject_consent_message. Proves the
    # subject consented to this specific pairing (R113/R115). Kept out of the
    # issuer's canonical signing bytes so it can be attached independently and never
    # disturbs the issuer signature (back-compat).
    subject_signature: Optional[str] = None
    # Consent scheme of subject_signature. Set on every freshly built cert so it
    # rides in the issuer-signed body (a downgrade to legacy verification would
    # change the canonical and break the issuer signature). None means the cert
    # predates R115 and its subject_signature is verified against the legacy pair
    # message. See CONSENT_VERSION.
    consent_version: Optional[int] = CONSENT_VERSION

    def validate_policy(self) -> None:
        """Raise ValueError('invalid_certificate_policy') on policy violations."""
        if self.created_at > self.expires_at:
            raise ValueError("invalid_certificate_policy: created_at > expires_at")
        max_validity = 365 * 86400
        if self.expires_at - self.created_at > max_validity:
            raise ValueError("certificate_too_long_lived")
        if not self.capabilities or len(self.capabilities) > 32:
            raise ValueError("invalid_certificate_policy: capabilities length must be 1..32")
        for cap in self.capabilities:
            if not cap.can or not cap.with_:
                raise ValueError("invalid_certificate_policy: empty can or with field")
        now = int(time.time())
        if self.expires_at < now:
            raise ValueError("certificate_expired")

    def to_dict(self) -> dict:
        d = {
            "@type": "RelationshipCertificate",
            "version": self.version,
            "certificate_id": self.certificate_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "wireguard": self.wireguard,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "signature": self.signature,
            "subject_signature": self.subject_signature,
        }
        # Emit consent_version only when set so legacy certs (version absent)
        # serialize byte-for-byte as they did pre-R115 and their issuer signature
        # still verifies. A v2 cert always carries it, inside the issuer canonical.
        if self.consent_version is not None:
            d["consent_version"] = self.consent_version
        return d

    def _issuer_canonical(self) -> bytes:
        """Canonical bytes covered by the issuer signature.

        The subject counter-signature is excluded so it can be attached before or
        after the issuer signs without disturbing it, and so certs predating the
        subject_signature field verify byte-for-byte as they did before (the field
        is simply absent from the canonical on both old and new serializations).
        """
        data = self.to_dict()
        data.pop('signature', None)
        data.pop('subject_signature', None)
        return json.dumps(data, sort_keys=True).encode()

    def sign(self, identity_key):
        if hasattr(identity_key, 'sign'):
             sig_bytes = identity_key.sign(self._issuer_canonical())
             self.signature = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)

    def verify(self, verifier_func) -> bool:
        """Verify the issuer's signature on the certificate."""
        if not self.signature: return False
        return verifier_func(self.issuer, bytes.fromhex(self.signature), self._issuer_canonical())

    def _subject_consent_bytes(self) -> bytes:
        """The message the subject_signature is expected to cover for THIS cert.

        A cert carrying a consent_version uses the cert-bound v2 message (pinned to
        this certificate_id and capability set); a legacy cert (no version) uses
        the pair-only v1 message. The two paths never mix, so an old pair-only
        signature can never satisfy a v2 cert and a v2 cert cannot be downgraded to
        the pair message.
        """
        if (self.consent_version or 1) >= 2:
            return subject_consent_message_v2(
                self.issuer,
                self.subject,
                self.certificate_id,
                capabilities_digest(self.capabilities),
            )
        return subject_consent_message(self.issuer, self.subject)

    def attach_subject_consent(self, subject_identity_key) -> None:
        """Counter-sign THIS certificate with the SUBJECT's identity key (R113/R115).

        Produces a durable, cert-bound proof of the subject's consent over
        subject_consent_message_v2 (issuer, subject, certificate_id, capability
        digest). Only the holder of the subject key can produce it, so an issuer
        (or anyone who reads the stored cert) cannot fabricate consent for a party
        they do not control, replay this proof under a different issuer, or
        transplant it onto a re-minted certificate with a different id or caps.
        """
        self.consent_version = CONSENT_VERSION
        sig_bytes = subject_identity_key.sign(self._subject_consent_bytes())
        self.subject_signature = (
            sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)
        )

    def verify_mutual(self, verifier_func) -> bool:
        """Verify BOTH the issuer signature AND the subject's consent signature.

        ``verify()`` alone only proves the issuer signed the cert, a single-party
        token the issuer fully controls. ``verify_mutual`` additionally requires a
        valid subject counter-signature (see :meth:`attach_subject_consent`),
        proving the party named as ``subject`` genuinely consented to THIS
        certificate. A v2 cert is checked only against its cert-bound message and a
        legacy cert only against the pair message, so a retained pair-only
        signature cannot authorize a freshly minted (v2) certificate.
        """
        if not self.verify(verifier_func):
            return False
        if not self.subject_signature:
            return False
        try:
            return verifier_func(
                self.subject,
                bytes.fromhex(self.subject_signature),
                self._subject_consent_bytes(),
            )
        except (ValueError, TypeError):
            return False

    @classmethod
    def from_dict(cls, d: dict) -> "RelationshipCertificate":
        caps = [Capability.from_dict(c) for c in d.get("capabilities", [])]
        obj = cls(
            issuer=d["issuer"],
            subject=d["subject"],
            capabilities=caps,
            wireguard=d.get("wireguard", {}),
        )
        obj.version = d.get("version", 1)
        obj.certificate_id = d["certificate_id"]
        obj.created_at = d["created_at"]
        obj.expires_at = d["expires_at"]
        obj.signature = d.get("signature")
        obj.subject_signature = d.get("subject_signature")
        # Absent for pre-R115 certs (verified against the legacy pair message).
        obj.consent_version = d.get("consent_version")
        return obj
