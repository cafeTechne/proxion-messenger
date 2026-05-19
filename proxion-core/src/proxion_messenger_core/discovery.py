"""Federation and store discovery client."""
from __future__ import annotations

import json

from .errors import ProxionError
from .network import safe_get, NetworkError


def fetch_peer_discovery(store_url: str) -> dict:
    """Fetch machine-readable discovery document from a coordination store.

    Tries /.well-known/proxion-identity then /info.
    Returns a dict containing at least:
        - identity_pubkey (hex)
        - pod_url

    Raises ProxionError on failure or invalid JSON.
    """
    # 1. Try .well-known
    url = store_url.rstrip("/") + "/.well-known/proxion-identity"
    try:
        data = safe_get(url, max_bytes=65_536)
        doc = json.loads(data)
        if "identity_pubkey" in doc and "pod_url" in doc:
            return doc
    except (NetworkError, Exception):
        pass

    # 2. Try /info
    url = store_url.rstrip("/") + "/info"
    try:
        data = safe_get(url, max_bytes=65_536)
        doc = json.loads(data)
        if "identity_pubkey" in doc and "pod_url" in doc:
            return doc
        raise ValueError("Discovery JSON missing required fields")
    except NetworkError as exc:
        raise ProxionError(f"Failed to fetch discovery from {store_url}: {exc}")
    except Exception as exc:
        raise ProxionError(f"Failed to fetch discovery from {store_url}: {exc}")


def fetch_oidc_token_endpoint(issuer_url: str) -> str:
    """Fetch the OAuth2 token endpoint from the OIDC discovery document.

    Requests ``{issuer_url}/.well-known/openid-configuration`` (RFC 8414) and
    returns the ``token_endpoint`` value.  Use this instead of hard-coding
    ``/oidc/token`` to support Inrupt ESS and other non-CSS providers.

    Parameters
    ----------
    issuer_url : str
        The OIDC issuer base URL (no trailing slash needed).

    Returns
    -------
    str
        The token endpoint URL.

    Raises
    ------
    ProxionError
        On network failure, invalid JSON, or missing ``token_endpoint``.
    """
    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        data = safe_get(url, max_bytes=65_536)
        doc = json.loads(data)
    except (NetworkError, Exception) as exc:
        raise ProxionError(f"Failed to fetch OIDC discovery from {issuer_url}: {exc}")

    endpoint = doc.get("token_endpoint")
    if not endpoint:
        raise ProxionError(f"OIDC discovery at {issuer_url} missing token_endpoint")
    return endpoint
