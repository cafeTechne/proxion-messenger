"""CSS account and credential management helpers — CSS API v0.5 (cookie-based)."""
from __future__ import annotations

import httpx
import base64
import json
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _normalize_origin(scheme: str, netloc: str) -> str:
    """Return a canonical ``scheme://host[:port]`` string for safe comparison.

    Normalises:
    - hostname to lowercase
    - trailing dots stripped (``localhost.`` → ``localhost``)
    - default ports stripped (``http:80``, ``https:443``)

    This prevents bypass via mixed-case hostnames, trailing-dot equivalents,
    or explicit default ports (all are equivalent under RFC 3986).
    """
    host, _, port = netloc.partition(":")
    host = host.lower().rstrip(".")
    _default_ports = {"http": "80", "https": "443"}
    if port and port == _default_ports.get(scheme, ""):
        port = ""  # strip redundant default port
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .css_auth import CssClientCredentials, DpopSolidClient
from .solid import SolidResolver
from .errors import CssAccountExistsError


def _parse_jwt_exp(token_str: str) -> float:
    """Best-effort JWT exp extraction without signature verification."""
    try:
        parts = token_str.split(".")
        if len(parts) != 3:
            return 0.0
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims.get("exp", 0) or 0)
    except Exception:
        return 0.0


@dataclass
class CssAccountManager:
    """Manages CSS account creation and credential issuance for CSS API v0.5.

    CSS v0.5 uses a cookie-based session. All account-specific endpoint URLs
    are discovered dynamically via the controls object returned from GET /.account/.
    """

    css_base_url: str

    # ── internal helpers ────────────────────────────────────────────────────

    def _unauthenticated_controls(self) -> dict:
        resp = httpx.get(f"{self.css_base_url}/.account/")
        resp.raise_for_status()
        return resp.json().get("controls", {})

    def _authenticated_controls(self, client: httpx.Client) -> dict:
        """Re-fetch controls with the session cookie to get account-specific URLs."""
        resp = client.get(f"{self.css_base_url}/.account/")
        resp.raise_for_status()
        return resp.json().get("controls", {})

    def _create_account_session(self, client: httpx.Client) -> dict:
        """POST /.account/account/ — creates empty account, sets session cookie.

        Returns authenticated controls (account-specific URLs).
        """
        resp = client.post(f"{self.css_base_url}/.account/account/")
        resp.raise_for_status()
        return self._authenticated_controls(client)

    def _login_session(self, client: httpx.Client, email: str, password: str) -> dict:
        """Login with email/password — sets session cookie.

        Returns authenticated controls (account-specific URLs).
        """
        unauth = self._unauthenticated_controls()
        login_url = (
            unauth.get("password", {}).get("login")
            or f"{self.css_base_url}/.account/login/password/"
        )
        logger.debug("css_setup: POST login → %s", login_url)
        resp = client.post(login_url, json={"email": email, "password": password})
        logger.debug("css_setup: login status=%d cookies=%s body=%s",
                     resp.status_code, dict(client.cookies), resp.text[:400])
        resp.raise_for_status()
        controls = self._authenticated_controls(client)
        logger.debug("css_setup: authenticated controls keys=%s", list(controls.keys()))
        return controls

    def _set_password(
        self, client: httpx.Client, controls: dict, email: str, password: str
    ) -> None:
        url = controls.get("password", {}).get("create")
        if not url:
            # No password.create control — account was already set up
            raise CssAccountExistsError(f"Account already exists: {email}")
        resp = client.post(url, json={"email": email, "password": password})
        if resp.status_code in (400, 409):
            # The password.create endpoint only returns 4xx when the email is
            # already registered — treat any 4xx here as account-exists regardless
            # of the exact message wording (which varies across CSS versions).
            raise CssAccountExistsError(f"Account already exists: {email}")
        resp.raise_for_status()

    def _validate_pod_url(self, pod_url: str) -> None:
        """Raise ValueError if pod_url is not on the same origin or a subdomain of css_base_url.

        solidcommunity.net and similar deployments host each pod at its own
        subdomain ({username}.solidcommunity.net) rather than a path under the
        base origin, so an exact-origin check is too strict. We allow any
        subdomain of the CSS base hostname while still rejecting pods on a
        completely unrelated domain (audit finding #6).

        Applies RFC 3986 normalisation before comparison so that mixed-case
        hostnames, trailing dots, and explicit default ports cannot bypass the
        check.
        """
        base = urlparse(self.css_base_url)
        pod = urlparse(pod_url)
        base_host = (base.hostname or "").lower().rstrip(".")
        pod_host = (pod.hostname or "").lower().rstrip(".")

        # Normalize default ports so http://h:80 equals http://h (RFC 3986).
        _default_port = {"http": 80, "https": 443}
        def _norm_port(p):
            raw = p.port
            return None if raw == _default_port.get(p.scheme) else raw

        same_host = pod_host == base_host
        # Allow subdomain pods only when the CSS base is an apex domain (≤1 dot).
        # This covers solidcommunity.net (pods at username.solidcommunity.net) and
        # bare hostnames like localhost, while blocking CSS deployments that are
        # themselves on a subdomain (e.g. css.example.com) from auto-trusting
        # sibling subdomains that could be registered by an attacker.
        _apex_like = base_host.count(".") <= 1
        is_subdomain = _apex_like and pod_host.endswith("." + base_host)

        scheme_ok = pod.scheme == base.scheme
        port_ok = _norm_port(pod) == _norm_port(base)

        if not (same_host or is_subdomain) or not scheme_ok or not port_ok:
            raise ValueError(
                f"Pod URL {pod_url!r} is not on the same origin or a trusted subdomain of "
                f"CSS base URL {self.css_base_url!r}. "
                "Refusing to use a pod on a different server."
            )

    def _create_pod(
        self, client: httpx.Client, controls: dict, pod_name: str
    ) -> tuple[str, str]:
        """POST to create a pod. Returns (pod_url, webid)."""
        url = controls["account"]["pod"]
        resp = client.post(url, json={"name": pod_name})
        resp.raise_for_status()
        body = resp.json()
        pod_url, webid = body["pod"], body["webId"]
        self._validate_pod_url(pod_url)
        return pod_url, webid

    def _get_pod_url_and_webid(
        self, client: httpx.Client, controls: dict
    ) -> tuple[str, str]:
        """GET pod list for this account. Returns (pod_url, webid)."""
        url = controls["account"]["pod"]
        logger.debug("css_setup: GET pod list → %s", url)
        resp = client.get(url)
        logger.debug("css_setup: pod list status=%d body=%s", resp.status_code, resp.text[:400])
        resp.raise_for_status()
        body = resp.json()
        pods: dict = body.get("pods", {})
        if not pods:
            raise ValueError("No pods found for this account")
        pod_url = next(iter(pods))
        self._validate_pod_url(pod_url)
        # CSS standard: webId is at {podUrl}profile/card#me
        webid = pod_url.rstrip("/") + "/profile/card#me"
        return pod_url, webid

    def _issue_credentials(
        self, client: httpx.Client, controls: dict, webid: str, label: str
    ) -> tuple[str, str]:
        """POST to client-credentials endpoint. Returns (client_id, client_secret).

        If a credential with *label* already exists (CSS returns 409 or 400),
        the old credential is revoked and a fresh one issued so the secret is
        always known to the caller.
        """
        url = controls["account"]["clientCredentials"]
        resp = client.post(url, json={"name": label, "webId": webid})
        if resp.status_code in (400, 409):
            # Credential name already in use — find and revoke it, then recreate.
            listing = client.get(url)
            if listing.is_success:
                for cred_id, cred in listing.json().get("clientCredentials", {}).items():
                    if cred.get("name") == label:
                        client.delete(url.rstrip("/") + "/" + cred_id)
                        break
            resp = client.post(url, json={"name": label, "webId": webid})
        resp.raise_for_status()
        body = resp.json()
        return body["id"], body["secret"]

    # ── public API ──────────────────────────────────────────────────────────

    def register(self, email: str, password: str) -> str:
        """Create a new CSS account with email/password. Returns session cookie value.

        Raises CssAccountExistsError if email already registered.
        """
        with httpx.Client() as client:
            controls = self._create_account_session(client)
            self._set_password(client, controls, email, password)
            return client.cookies.get("css-account", "")

    def login(self, email: str, password: str) -> str:
        """Log in to an existing CSS account. Returns session cookie value."""
        with httpx.Client() as client:
            self._login_session(client, email, password)
            return client.cookies.get("css-account", "")

    def connect_agent(
        self,
        identity_key: Ed25519PrivateKey,
        email: str,
        password: str,
        label: str = "proxion",
    ) -> tuple["CssClientCredentials", str, str]:
        """Connect (register or login) and return (credentials, pod_url, webid).

        Tries to register a new account + create a pod; if the email already
        exists, logs in and fetches the existing pod. Does NOT publish an
        identity card — use setup_agent for first-time onboarding with profile
        publishing.
        """
        try:
            with httpx.Client() as client:
                controls = self._create_account_session(client)
                self._set_password(client, controls, email, password)
                controls = self._authenticated_controls(client)
                pod_name = email.split("@")[0]
                pod_url, webid = self._create_pod(client, controls, pod_name)
                client_id, client_secret = self._issue_credentials(
                    client, controls, webid, label
                )
        except CssAccountExistsError:
            # Fresh client so the anonymous-session cookie from _create_account_session
            # does not contaminate the login session and cause CSS to reject /pod/ with 400.
            with httpx.Client() as client:
                controls = self._login_session(client, email, password)
                try:
                    pod_url, webid = self._get_pod_url_and_webid(client, controls)
                except ValueError:
                    # Account exists but pod was never created (partial prior registration).
                    pod_name = email.split("@")[0]
                    pod_url, webid = self._create_pod(client, controls, pod_name)
                client_id, client_secret = self._issue_credentials(
                    client, controls, webid, label
                )

        credentials = CssClientCredentials(
            css_base_url=self.css_base_url,
            client_id=client_id,
            client_secret=client_secret,
            identity_key=identity_key,
        )
        return credentials, pod_url, webid

    def setup_agent(
        self,
        identity_key: Ed25519PrivateKey,
        email: str,
        password: str,
        label: str = "proxion",
        display_name: Optional[str] = None,
        store_url: Optional[str] = None,
    ) -> tuple[CssClientCredentials, str, str]:
        """Full setup: register account, create pod, issue credentials.

        Returns (CssClientCredentials, pod_url, webid).
        """
        with httpx.Client() as client:
            controls = self._create_account_session(client)
            self._set_password(client, controls, email, password)
            controls = self._authenticated_controls(client)
            pod_name = email.split("@")[0]
            pod_url, webid = self._create_pod(client, controls, pod_name)
            client_id, client_secret = self._issue_credentials(
                client, controls, webid, label
            )

        credentials = CssClientCredentials(
            css_base_url=self.css_base_url,
            client_id=client_id,
            client_secret=client_secret,
            identity_key=identity_key,
        )

        if display_name or store_url:
            from .identity import IdentityCard, publish_identity
            client_conn = build_dpop_client(credentials, pod_url)

            if display_name:
                card = IdentityCard(display_name=display_name)
                publish_identity(client_conn, card)

            if store_url:
                from cryptography.hazmat.primitives import serialization
                pub_hex = identity_key.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                ).hex()
                publish_proxion_discovery(client_conn, pod_url, store_url, pub_hex)

        return credentials, pod_url, webid

    # ── legacy compat ───────────────────────────────────────────────────────

    def get_credentials(self, account_token: str, label: str = "proxion") -> tuple[str, str]:
        """Legacy compatibility helper for API v0.5.

        CSS API v0.5 requires an authenticated cookie session to issue client
        credentials. This legacy signature cannot supply that session context.
        """
        raise ValueError(
            "get_credentials(account_token=...) is unsupported for CSS API v0.5; "
            "use connect_agent(email, password, ...) to acquire fresh credentials."
        )

    def get_pod_info(self, account_token: str) -> tuple[str, str]:
        """Legacy compatibility helper for API v0.5."""
        raise ValueError(
            "get_pod_info(account_token=...) is unsupported for CSS API v0.5; "
            "use connect_agent(email, password, ...) to resolve pod URL + WebID."
        )


def build_dpop_client(
    credentials: CssClientCredentials,
    pod_url: str,
    stash_owner: str = "pod",
) -> DpopSolidClient:
    resolver = SolidResolver(pod_url if pod_url.endswith("/") else pod_url + "/")
    return DpopSolidClient(resolver, credentials, stash_owner=stash_owner)


def publish_proxion_discovery(
    pod_client,
    pod_url: str,
    store_url: str,
    identity_pub_hex: str,
) -> None:
    import json
    data = {
        "pod_url": pod_url,
        "store_url": store_url,
        "identity_pub_hex": identity_pub_hex,
        "proxion_version": "0.1.0",
    }
    pod_client.put("stash://profile/proxion-discovery.json", json.dumps(data).encode("utf-8"))
