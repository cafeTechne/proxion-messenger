import json
import uuid
import time
import secrets
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any

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
    
    def to_dict(self) -> dict:
        return {
            "@type": "InviteAcceptance",
            "invitation_id": self.invitation_id,
            "responder": self.responder,
            "challenge_response": self.challenge_response,
            "timestamp": self.timestamp,
            "signature": self.signature
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
        _CHALLENGE_CTX = b"proxion-handshake-v1:"
        return verifier_func(self.responder['public_key'], bytes.fromhex(self.challenge_response), _CHALLENGE_CTX + challenge_marker.encode())

    @classmethod
    def from_dict(cls, d: dict, strict: bool = False) -> "InviteAcceptance":
        if strict:
            # Check for unknown top-level fields
            allowed_fields = {
                "invitation_id", "responder", "challenge_response",
                "timestamp", "signature", "@type"
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
        return {
            "@type": "RelationshipCertificate",
            "version": self.version,
            "certificate_id": self.certificate_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "wireguard": self.wireguard,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "signature": self.signature
        }

    def sign(self, identity_key):
        data = self.to_dict()
        if 'signature' in data: del data['signature']
        canonical = json.dumps(data, sort_keys=True)
        if hasattr(identity_key, 'sign'):
             sig_bytes = identity_key.sign(canonical.encode())
             self.signature = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)

    def verify(self, verifier_func) -> bool:
        """Verify the issuer's signature on the certificate."""
        if not self.signature: return False
        data = self.to_dict()
        del data['signature']
        canonical = json.dumps(data, sort_keys=True)
        return verifier_func(self.issuer, bytes.fromhex(self.signature), canonical.encode())

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
        return obj
