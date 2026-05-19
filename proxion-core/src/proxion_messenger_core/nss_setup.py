"""NSS (Node Solid Server) account management and bearer-token authentication."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .solid_client import SolidClient
from .solid import SolidResolver


class NssAuthError(RuntimeError):
    """Raised when NSS authentication fails."""
    pass


def detect_server_type(base_url: str) -> str:
    """Detect whether a Solid server is CSS or NSS.

    GET {base_url}/.account/ — if 200 + JSON with "controls" key → "css", else → "nss".
    Timeout 5s, swallow exceptions → "unknown".

    Parameters
    ----------
    base_url : str
        The base URL of the Solid server (e.g. https://solidweb.org).

    Returns
    -------
    str
        One of: "css", "nss", "unknown".
    """
    import httpx
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/.account/", timeout=5.0)
        if resp.status_code == 200:
            try:
                body = resp.json()
                if "controls" in body:
                    return "css"
                else:
                    return "nss"
            except Exception:
                return "nss"
        else:
            return "nss"
    except Exception:
        return "unknown"


@dataclass
class NssBearerCredentials:
    """OAuth2 bearer token credentials for an NSS Pod, with token caching.

    Attributes
    ----------
    nss_base_url : str
        NSS server base URL, no trailing slash (e.g. https://solidweb.org).
    username : str
        NSS username (not an email; the account name part of the WebID path).
    password : str
        NSS account password.
    identity_key : Ed25519PrivateKey
        Agent identity key — used for security purposes.
    """

    nss_base_url: str
    username: str
    password: str
    identity_key: Ed25519PrivateKey
    _cached_token: Optional[str] = field(default=None, repr=False)
    _token_expiry: float = field(default=0.0, repr=False)
    _client_id: Optional[str] = field(default=None, repr=False)
    _token_issued_at: float = field(default=0.0, repr=False)
    max_cached_token_lifetime_s: int = field(default=3600, repr=False)

    def get_token(self, scope: str = "openid profile") -> str:
        """Return a valid access token, refreshing if expired or stale.

        Parameters
        ----------
        scope : str
            OAuth2 scope to request (default: "openid profile").

        Returns
        -------
        str
            A valid bearer token.

        Raises
        ------
        NssAuthError
            If token fetch fails.
        """
        import os as _os_token
        _max_age = int(_os_token.environ.get("PROXION_MAX_TOKEN_AGE_S", "1800"))
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
        """Fetch a fresh bearer token from NSS.

        Steps:
        1. GET /.well-known/openid-configuration → extract token_endpoint, registration_endpoint
        2. If no client_id, POST to registration_endpoint with client metadata → store client_id
        3. Build WebID from base_url + username
        4. POST to token_endpoint with grant_type=password
        5. Return (access_token, expires_in)

        Returns
        -------
        tuple[str, int]
            (access_token, expires_in_seconds)

        Raises
        ------
        NssAuthError
            If any step fails.
        """
        import httpx
        import json

        base = self.nss_base_url.rstrip("/")

        # Step 1: Fetch OIDC configuration
        try:
            config_resp = httpx.get(
                f"{base}/.well-known/openid-configuration",
                timeout=15.0,
            )
            config_resp.raise_for_status()
            config = config_resp.json()
        except Exception as e:
            raise NssAuthError(
                f"Failed to fetch OIDC configuration from {base}: {e}"
            )

        token_endpoint = config.get("token_endpoint")
        registration_endpoint = config.get("registration_endpoint")

        if not token_endpoint:
            raise NssAuthError(
                f"NSS OIDC configuration missing token_endpoint at {base}"
            )

        # Step 2: Register client if needed
        if self._client_id is None and registration_endpoint:
            try:
                reg_resp = httpx.post(
                    registration_endpoint,
                    json={
                        "application_type": "native",
                        "grant_types": ["password", "refresh_token"],
                        "redirect_uris": ["https://localhost"],
                        "token_endpoint_auth_method": "none",
                    },
                    timeout=15.0,
                )
                if reg_resp.status_code == 201 or reg_resp.status_code == 200:
                    reg_body = reg_resp.json()
                    self._client_id = reg_body.get("client_id")
                else:
                    # Registration endpoint exists but fails — fall back to default
                    self._client_id = "proxion-test"
            except Exception:
                # Registration failed — use default client ID
                self._client_id = "proxion-test"

        if self._client_id is None:
            self._client_id = "proxion-test"

        # Step 3: Build WebID
        webid = f"{base}/{self.username}/profile/card#me"

        # Step 4: Request token with password grant
        try:
            token_resp = httpx.post(
                token_endpoint,
                data={
                    "grant_type": "password",
                    "username": webid,
                    "password": self.password,
                    "client_id": self._client_id,
                    "scope": "openid profile",
                },
                timeout=15.0,
            )

            if token_resp.status_code != 200:
                body_text = token_resp.text
                try:
                    body_json = token_resp.json()
                    error = body_json.get("error", "unknown error")
                    error_desc = body_json.get("error_description", "")
                    raise NssAuthError(
                        f"NSS token fetch failed: {token_resp.status_code} {error}: {error_desc}"
                    )
                except ValueError:
                    raise NssAuthError(
                        f"NSS token fetch failed: {token_resp.status_code} {body_text}"
                    )

            body = token_resp.json()
            return body["access_token"], int(body.get("expires_in", 3600))

        except NssAuthError:
            raise
        except Exception as e:
            raise NssAuthError(f"NSS token fetch failed: {e}")


class NssBearerSolidClient(SolidClient):
    """SolidClient that injects Bearer token Authorization headers per request.

    Parameters
    ----------
    resolver : SolidResolver
        Configured with the NSS Pod's base URL.
    credentials : NssBearerCredentials
        Credentials used to obtain and cache the access token.
    stash_owner : str
        Owner segment used when converting HTTP URLs back to stash:// URIs.
    session : optional
        httpx.Client to use; if None a new one is created internally.
    """

    def __init__(
        self,
        resolver: SolidResolver,
        credentials: NssBearerCredentials,
        stash_owner: str = "pod",
        session=None,
    ) -> None:
        super().__init__(resolver, session=session, stash_owner=stash_owner)
        self._credentials = credentials

    def _dynamic_headers(self, method: str, url: str) -> dict:
        """Return Authorization and User-Agent headers for this request."""
        token = self._credentials.get_token()
        return {
            "User-Agent": "Proxion/1.0",
            "Authorization": f"Bearer {token}",
        }


class NssAccountManager:
    """Manages NSS account access and credential issuance.

    NSS uses OIDC with a password grant type for server-side credential flows.
    Unlike CSS, NSS does not have an account creation API — accounts are created
    out-of-band (e.g. via web UI).
    """

    def __init__(self, base_url: str) -> None:
        """Initialize the NSS account manager.

        Parameters
        ----------
        base_url : str
            The NSS server base URL (e.g. https://solidweb.org).
        """
        self.base_url = base_url.rstrip("/")

    def connect_agent(
        self,
        identity_key: Ed25519PrivateKey,
        username: str,
        password: str,
        label: str = "proxion",
    ) -> Tuple[NssBearerCredentials, str, str]:
        """Connect to an NSS account and return (credentials, pod_url, webid).

        NSS accounts must already exist (created out-of-band).
        This method validates the password and returns credentials for future use.

        Parameters
        ----------
        identity_key : Ed25519PrivateKey
            The agent's identity key.
        username : str
            NSS username (not an email; the account name in the pod URL).
        password : str
            NSS account password.
        label : str
            Label for logging (unused for NSS but kept for API compatibility).

        Returns
        -------
        tuple[NssBearerCredentials, str, str]
            (credentials, pod_url, webid)

        Raises
        ------
        NssAuthError
            If authentication fails.
        """
        credentials = NssBearerCredentials(
            nss_base_url=self.base_url,
            username=username,
            password=password,
            identity_key=identity_key,
        )

        # Validate credentials by fetching a token
        try:
            credentials.get_token()
        except NssAuthError:
            raise

        pod_url = f"{self.base_url}/{username}/"
        webid = f"{self.base_url}/{username}/profile/card#me"

        return credentials, pod_url, webid


def build_nss_client(
    credentials: NssBearerCredentials,
    pod_url: str,
    stash_owner: str = "pod",
) -> NssBearerSolidClient:
    """Build an NSS Solid client from credentials.

    Parameters
    ----------
    credentials : NssBearerCredentials
        The credentials object.
    pod_url : str
        The NSS pod URL (e.g. https://solidweb.org/username/).
    stash_owner : str
        Owner segment for stash:// URI resolution.

    Returns
    -------
    NssBearerSolidClient
        A ready-to-use Solid client.
    """
    resolver = SolidResolver(pod_url if pod_url.endswith("/") else pod_url + "/")
    return NssBearerSolidClient(resolver, credentials, stash_owner=stash_owner)


def make_pod_client(
    base_url: str,
    identity_key: Ed25519PrivateKey,
    username: str,
    password: str,
    stash_owner: str = "pod",
) -> Tuple:
    """Server-agnostic factory to connect to CSS or NSS pods.

    Detects the server type and routes to the appropriate manager.

    Parameters
    ----------
    base_url : str
        Pod server base URL.
    identity_key : Ed25519PrivateKey
        Agent identity key.
    username : str
        Account username (email for CSS, account name for NSS).
    password : str
        Account password.
    stash_owner : str
        Owner segment for stash:// URI resolution.

    Returns
    -------
    tuple
        (credentials, pod_url, webid, client)

    Raises
    ------
    NssAuthError or CssAccountExistsError
        If authentication fails.
    """
    server_type = detect_server_type(base_url)

    if server_type == "nss":
        mgr = NssAccountManager(base_url)
        creds, pod_url, webid = mgr.connect_agent(
            identity_key, username, password
        )
        client = build_nss_client(creds, pod_url, stash_owner=stash_owner)
        return creds, pod_url, webid, client

    elif server_type == "css":
        from .css_setup import CssAccountManager, build_dpop_client
        mgr = CssAccountManager(base_url)
        creds, pod_url, webid = mgr.setup_agent(
            identity_key, username, password
        )
        client = build_dpop_client(creds, pod_url, stash_owner=stash_owner)
        return creds, pod_url, webid, client

    else:
        raise NssAuthError(
            f"Could not detect Solid server type at {base_url}. "
            "Tried CSS (/.account/) and NSS (/.well-known/openid-configuration). "
            "Server may be unreachable or not a Solid server."
        )
