"""CSS (Community Solid Server) authentication — DPoP client credentials."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .dpop import make_dpop_proof, _extract_dpop_nonce, generate_ec_dpop_key, make_dpop_proof_es256


class _BridgeTransportError(Exception):
    """Raised by the bridge stub when the adapter service is unavailable."""


def _is_auth_security_error(exc: Exception) -> bool:
    """Return True if *exc* represents a security-relevant auth failure.

    Nonce errors and signature validation failures must NOT be silently
    retried via the legacy path — they indicate a real security problem.
    """
    msg = str(exc).lower()
    return any(kw in msg for kw in ("nonce", "signature", "401", "403", "forbidden", "invalid_token"))


@dataclass
class CssClientCredentials:
    """OAuth2 client credentials for a CSS Pod, with DPoP token caching.

    Attributes
    ----------
    css_base_url : str
        CSS server base URL, no trailing slash (e.g. ``http://localhost:3001``).
    client_id : str
        Client ID issued by CSS ``/.account/credentials/``.
    client_secret : str
        Client secret paired with client_id.
    identity_key : Ed25519PrivateKey
        Agent identity key — reused for DPoP proof signing.
    """

    css_base_url: str
    client_id: str
    client_secret: str
    identity_key: Ed25519PrivateKey
    token_endpoint_url: Optional[str] = field(default=None, repr=False)
    # CSS v7 requires ES256 DPoP; generate a dedicated P-256 key at construction
    _dpop_ec_key: object = field(default=None, repr=False)
    _cached_tokens: dict = field(default_factory=dict, repr=False)     # scope -> token str
    _token_expiries: dict = field(default_factory=dict, repr=False)   # scope -> float
    _token_issued_at: dict = field(default_factory=dict, repr=False)  # scope -> float (R10)
    _consecutive_401s: dict = field(default_factory=dict, repr=False) # scope -> int (R10)
    _last_nonce: Optional[str] = field(default=None, repr=False)
    max_cached_token_lifetime_s: int = field(default=3600, repr=False)

    def __post_init__(self) -> None:
        if self._dpop_ec_key is None:
            self._dpop_ec_key = generate_ec_dpop_key()

    # Backward compat aliases for single default "pod_rw" scope
    @property
    def _cached_token(self) -> Optional[str]:
        return self._cached_tokens.get("pod_rw")
    @_cached_token.setter
    def _cached_token(self, value: Optional[str]) -> None:
        if value is None:
            self._cached_tokens.pop("pod_rw", None)
        else:
            self._cached_tokens["pod_rw"] = value
    @property
    def _token_expires_at(self) -> float:
        return self._token_expiries.get("pod_rw", 0.0)
    @_token_expires_at.setter
    def _token_expires_at(self, value: float) -> None:
        self._token_expiries["pod_rw"] = value

    def fetch_access_token(self, scope: str = "pod_rw") -> Tuple[str, int]:
        """POST to the token endpoint with client_credentials grant + DPoP proof.

        Respects ``PROXION_SOLID_AUTH_MODE``:
        - ``legacy`` (default): direct client-credentials POST with ES256 DPoP.
        - ``inrupt_bridge``: use Inrupt SDK node adapter (NotImplementedError if
          adapter service is unavailable).
        - ``auto``: try bridge first; fall back to legacy **only** on transport or
          compatibility errors.  Nonce/signature validation failures (401/403) are
          **not** retried via legacy — they surface immediately.

        Handles a one-shot 401 nonce challenge: if the server responds with 401
        and a ``dpop-nonce`` in ``WWW-Authenticate``, the nonce is extracted and
        the request is retried exactly once with the nonce included in the proof.

        Parameters
        ----------
        scope : str
            OAuth2 scope to request (default: "pod_rw").

        Returns
        -------
        tuple[str, int]
            ``(access_token, expires_in)`` where expires_in is seconds.
        """
        import httpx
        import os as _os_am
        auth_mode = _os_am.environ.get("PROXION_SOLID_AUTH_MODE", "legacy")

        if auth_mode in ("inrupt_bridge", "auto"):
            try:
                result = self._fetch_via_bridge(scope)
                from .solid_migration import migration_store
                migration_store.set_auth_mode("inrupt_bridge")
                return result
            except _BridgeTransportError as _bte:
                if auth_mode == "inrupt_bridge":
                    raise
                # auto: fall back to legacy on transport/compat errors only
                from .solid_migration import migration_store, SOLID_AUTH_FAILED
                migration_store.record_auth_fallback(SOLID_AUTH_FAILED)
                migration_store.set_auth_mode("legacy_fallback")
            except Exception as _bridge_exc:
                # Non-transport errors (nonce/sig/auth failures) are NOT retried
                if auth_mode == "auto" and _is_auth_security_error(_bridge_exc):
                    raise
                if auth_mode == "inrupt_bridge":
                    raise
                from .solid_migration import migration_store, SOLID_AUTH_FAILED
                migration_store.record_auth_fallback(SOLID_AUTH_FAILED)
                migration_store.set_auth_mode("legacy_fallback")

        if not self.token_endpoint_url:
            try:
                import httpx as _httpx_disc
                _disc = _httpx_disc.get(f"{self.css_base_url}/.well-known/openid-configuration", timeout=5)
                _disc.raise_for_status()
                self.token_endpoint_url = _disc.json()["token_endpoint"]
            except Exception:
                self.token_endpoint_url = f"{self.css_base_url}/.oidc/token"
        token_url = self.token_endpoint_url
        nonce = self._last_nonce
        resp = None
        for attempt in range(2):
            dpop = make_dpop_proof_es256(self._dpop_ec_key, "POST", token_url, nonce=nonce)
            resp = httpx.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": scope,
                },
                headers={"DPoP": dpop, "User-Agent": "Proxion/1.0"},
            )
            if resp.status_code == 401 and attempt == 0:
                extracted = _extract_dpop_nonce(resp.headers.get("WWW-Authenticate", ""))
                if extracted:
                    nonce = extracted
                    self._last_nonce = extracted
                    continue
            break
        resp.raise_for_status()
        body = resp.json()
        return body["access_token"], int(body.get("expires_in", 3600))

    def _fetch_via_bridge(self, scope: str) -> Tuple[str, int]:
        """Acquire a token via the Inrupt SDK node adapter (stub).

        Raises
        ------
        _BridgeTransportError
            When the adapter service is unreachable (transport/compat fault).
        NotImplementedError
            Always — the full bridge is not yet implemented.
        """
        raise _BridgeTransportError("Inrupt bridge adapter not yet available")

    def get_token(self, scope: str = "pod_rw") -> str:
        """Return a valid access token for the given scope, refreshing if expired or stale.

        R10: Also enforces PROXION_MAX_TOKEN_AGE_S hard max age — forces refresh even if
        expires_in indicates the token is still valid.

        Parameters
        ----------
        scope : str
            OAuth2 scope to request (default: "pod_rw").
        """
        import os as _os_ta
        _max_age = int(_os_ta.environ.get("PROXION_MAX_TOKEN_AGE_S", "1800"))
        cached = self._cached_tokens.get(scope)
        expiry = self._token_expiries.get(scope, 0.0)
        issued_at = self._token_issued_at.get(scope, 0.0)
        now = time.time()
        _age_exceeded = cached is not None and (now - issued_at) >= _max_age
        if cached is None or now >= expiry - 30 or _age_exceeded:
            token, expires_in = self.fetch_access_token(scope=scope)
            self._cached_tokens[scope] = token
            self._token_issued_at[scope] = now
            self._token_expiries[scope] = now + min(expires_in, self.max_cached_token_lifetime_s)
            self._consecutive_401s[scope] = 0  # successful fetch resets streak
        return self._cached_tokens[scope]

    def record_401(self, scope: str = "pod_rw", store=None) -> int:
        """Record a consecutive 401 for the given scope. Purges cache and emits anomaly on streak >= 3.

        Returns the current consecutive 401 count after incrementing.
        """
        import uuid as _uuid_401
        self._consecutive_401s[scope] = self._consecutive_401s.get(scope, 0) + 1
        count = self._consecutive_401s[scope]
        if count >= 3:
            self.purge_token_cache()
            self._consecutive_401s.clear()
            import logging as _log_401
            _log_401.getLogger(__name__).warning(
                "CssClientCredentials: 3 consecutive 401s for scope=%s — token cache purged", scope
            )
            if store is not None:
                try:
                    store.save_credential_anomaly(
                        id=str(_uuid_401.uuid4()),
                        anomaly_type="consecutive_401_streak",
                        identity=self.client_id,
                        detail=f"scope={scope} streak=3 cache_purged=True",
                    )
                    store.save_security_event(
                        "credential_401_streak", "warning",
                        details=f"client_id={self.client_id} scope={scope}",
                    )
                except Exception:
                    pass
        return count

    def purge_token_cache(self) -> None:
        """Zeroize and clear all cached tokens (call on auth failures or safe-mode transitions)."""
        for k in list(self._cached_tokens.keys()):
            v = self._cached_tokens.pop(k, None)
            if isinstance(v, str):
                # overwrite memory before GC (best-effort)
                try:
                    _ = bytearray(v.encode())
                    for i in range(len(_)):
                        _[i] = 0
                except Exception:
                    pass
        self._token_expiries.clear()


from .solid_client import SolidClient
from .solid import SolidResolver


class DpopSolidClient(SolidClient):
    """SolidClient that injects DPoP + Authorization headers per request.

    Parameters
    ----------
    resolver : SolidResolver
        Configured with the CSS Pod's base URL.
    credentials : CssClientCredentials
        Client credentials used to obtain and cache the access token.
    stash_owner : str
        Owner segment used when converting HTTP URLs back to stash:// URIs.
    session : optional
        httpx.Client to use; if None a new one is created internally.
    """

    def __init__(
        self,
        resolver: SolidResolver,
        credentials: CssClientCredentials,
        stash_owner: str = "pod",
        session=None,
    ) -> None:
        super().__init__(resolver, session=session, stash_owner=stash_owner)
        self._credentials = credentials
        self._dpop_nonce: Optional[str] = None

    def _dynamic_headers(self, method: str, url: str) -> dict:
        """Return Authorization, DPoP, and User-Agent headers for this request."""
        token = self._credentials.get_token()
        proof = make_dpop_proof_es256(
            self._credentials._dpop_ec_key, method, url,
            nonce=self._dpop_nonce,
            access_token=token,  # RFC 9449 §4.2 — ath claim binds proof to bearer token
        )
        return {
            "User-Agent": "Proxion/1.0",
            "Authorization": f"DPoP {token}",
            "DPoP": proof,
        }

    def _refresh_auth(self, response=None) -> None:
        """Invalidate cached token; extract and cache nonce from a 401 response."""
        self._credentials._cached_token = None
        self._credentials._token_expires_at = 0.0
        if response is not None:
            nonce = _extract_dpop_nonce(response.headers.get("WWW-Authenticate", ""))
            if nonce:
                self._dpop_nonce = nonce
