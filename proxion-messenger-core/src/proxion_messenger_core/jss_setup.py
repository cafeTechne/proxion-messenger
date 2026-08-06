"""JSS (JavaScript Solid Server) account management and bearer-token authentication.

JSS speaks standard Solid-OIDC, but its simplest headless path (verified against a live
JSS v0.0.220) is bearer-token, like NSS and unlike CSS's DPoP client-credentials dance:

  - POST {base}/idp/credentials {"email","password"}  -> {"access_token": <Solid-OIDC JWT>}
  - Authorization: Bearer <access_token>              -> pod read/write (no DPoP)
  - POST {base}/.pods {"name","email","password"}     -> 201 {"webId","podUri",...} (create)

So this module mirrors nss_setup.py's bearer approach rather than css_setup.py's DPoP one.
The protocol-level pod I/O (solid_client.py) is unchanged.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .solid_client import SolidClient
from .solid import SolidResolver


class JssAuthError(RuntimeError):
    """Raised when JSS authentication or provisioning fails."""
    pass


def _jwt_claims(token: str) -> dict:
    """Best-effort decode of a JWT payload (no signature check; we only read claims like
    webid/exp that the server already vouched for by issuing the token to us)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # pad base64url
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _pod_url_from_webid(webid: str) -> str:
    """Derive the pod root from a JSS WebID.

    JSS WebIDs look like {base}/{account}/profile/card.jsonld#me and the pod root is
    {base}/{account}/, so cut at /profile/.
    """
    no_frag = webid.split("#", 1)[0]
    if "/profile/" in no_frag:
        return no_frag.split("/profile/", 1)[0].rstrip("/") + "/"
    # Fallback: strip the last path segment (the profile doc).
    return no_frag.rsplit("/", 1)[0].rstrip("/") + "/"


@dataclass
class JssBearerCredentials:
    """Bearer-token credentials for a JSS Pod, with token caching.

    Attributes
    ----------
    jss_base_url : str
        JSS server base URL, no trailing slash.
    email : str
        Account email (JSS authenticates headless clients by email + password).
    password : str
        Account password.
    identity_key : Ed25519PrivateKey
        Agent identity key (kept for parity with the other credential types).
    """

    jss_base_url: str
    email: str
    password: str
    identity_key: Ed25519PrivateKey
    _cached_token: Optional[str] = field(default=None, repr=False)
    _token_expiry: float = field(default=0.0, repr=False)
    _token_issued_at: float = field(default=0.0, repr=False)
    max_cached_token_lifetime_s: int = field(default=3600, repr=False)

    def get_token(self, scope: str = "openid webid") -> str:
        """Return a valid access token, refreshing via /idp/credentials if expired/stale."""
        import os as _os
        _max_age = int(_os.environ.get("PROXION_MAX_TOKEN_AGE_S", "1800"))
        now = time.time()
        age = now - self._token_issued_at if self._token_issued_at else _max_age + 1
        if (
            self._cached_token is not None
            and now < self._token_expiry - 30
            and age < _max_age
        ):
            return self._cached_token
        token, expires_in = self._fetch_token()
        self._cached_token = token
        self._token_issued_at = now
        self._token_expiry = now + min(expires_in, self.max_cached_token_lifetime_s)
        return self._cached_token

    def _fetch_token(self) -> Tuple[str, int]:
        """POST /idp/credentials with email/password -> (access_token, expires_in)."""
        import httpx
        base = self.jss_base_url.rstrip("/")
        try:
            resp = httpx.post(
                f"{base}/idp/credentials",
                json={"email": self.email, "password": self.password},
                headers={"User-Agent": "Proxion/1.0"},
                timeout=15.0,
            )
        except Exception as e:
            raise JssAuthError(f"JSS token request to {base}/idp/credentials failed: {e}")
        if resp.status_code != 200:
            raise JssAuthError(
                f"JSS credential login failed ({resp.status_code}) at {base}/idp/credentials"
            )
        try:
            body = resp.json()
        except Exception as e:
            raise JssAuthError(f"JSS credential response was not JSON: {e}")
        token = body.get("access_token") or body.get("token")
        if not token:
            raise JssAuthError("JSS credential response missing access_token")
        # Prefer the server's expiry; else the JWT exp claim; else a conservative default.
        expires_in = body.get("expires_in")
        if not expires_in:
            claims = _jwt_claims(token)
            exp = claims.get("exp")
            expires_in = int(exp - time.time()) if exp else 3600
        return token, max(int(expires_in), 60)

    def webid(self) -> str:
        """The WebID this account authenticates as (from the token's `webid` claim)."""
        return _jwt_claims(self.get_token()).get("webid", "")


class JssBearerSolidClient(SolidClient):
    """SolidClient that injects a Bearer Authorization header per request (no DPoP)."""

    def __init__(
        self,
        resolver: SolidResolver,
        credentials: JssBearerCredentials,
        stash_owner: str = "pod",
        session=None,
    ) -> None:
        super().__init__(resolver, session=session, stash_owner=stash_owner)
        self._credentials = credentials

    def _dynamic_headers(self, method: str, url: str) -> dict:
        token = self._credentials.get_token()
        return {"User-Agent": "Proxion/1.0", "Authorization": f"Bearer {token}"}


class JssAccountManager:
    """Manages JSS account/pod provisioning and bearer-token access."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def connect_agent(
        self,
        identity_key: Ed25519PrivateKey,
        email: str,
        password: str,
        label: str = "proxion",
    ) -> Tuple[JssBearerCredentials, str, str]:
        """Log in to an EXISTING JSS account and return (credentials, pod_url, webid)."""
        creds = JssBearerCredentials(
            jss_base_url=self.base_url, email=email, password=password,
            identity_key=identity_key,
        )
        webid = creds.webid()   # also validates the login by fetching a token
        if not webid:
            raise JssAuthError("JSS login succeeded but no webid claim was returned")
        return creds, _pod_url_from_webid(webid), webid

    def setup_agent(
        self,
        identity_key: Ed25519PrivateKey,
        email: str,
        password: str,
        label: str = "proxion",
        name: Optional[str] = None,
    ) -> Tuple[JssBearerCredentials, str, str]:
        """Create the pod if needed, then connect. Mirrors CssAccountManager.setup_agent.

        `name` is the pod path segment; defaults to the email local-part.
        """
        try:
            return self.connect_agent(identity_key, email, password, label=label)
        except JssAuthError:
            # Account may not exist yet — create it, then connect.
            self._create_pod(name or email.split("@", 1)[0], email, password)
            return self.connect_agent(identity_key, email, password, label=label)

    def _create_pod(self, name: str, email: str, password: str) -> dict:
        """POST /.pods to create an account + pod. Returns the JSS response body."""
        import httpx
        try:
            resp = httpx.post(
                f"{self.base_url}/.pods",
                json={"name": name, "email": email, "password": password},
                headers={"User-Agent": "Proxion/1.0"},
                timeout=20.0,
            )
        except Exception as e:
            raise JssAuthError(f"JSS pod creation request failed: {e}")
        if resp.status_code not in (200, 201):
            raise JssAuthError(
                f"JSS pod creation failed ({resp.status_code}): {resp.text[:200]}"
            )
        try:
            return resp.json()
        except Exception:
            return {}


def build_jss_client(
    credentials: JssBearerCredentials,
    pod_url: str,
    stash_owner: str = "pod",
) -> JssBearerSolidClient:
    """Build a JSS Solid client from credentials."""
    resolver = SolidResolver(pod_url if pod_url.endswith("/") else pod_url + "/")
    return JssBearerSolidClient(resolver, credentials, stash_owner=stash_owner)
