"""OpenID Connect (OIDC) support for Solid Pods.

Enables discovery of OIDC configuration from pod issuer URLs and dynamic
client registration for native applications.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class OidcConfig:
    """OpenID Connect discovery configuration.
    
    Parameters
    ----------
    issuer : str
        The issuer URL (from .well-known/openid-configuration).
    authorization_endpoint : str
        The authorization server's endpoint for authorization requests.
    token_endpoint : str
        The authorization server's token endpoint.
    jwks_uri : str
        The URL to the authorization server's JSON Web Key Set.
    registration_endpoint : str, optional
        The client registration endpoint (for dynamic registration).
    """
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    registration_endpoint: Optional[str] = None


async def fetch_oidc_config(issuer: str) -> OidcConfig:
    """Fetch and parse the OIDC configuration from an issuer.
    
    Makes a GET request to {issuer}/.well-known/openid-configuration
    and parses the JSON response.
    
    Parameters
    ----------
    issuer : str
        The issuer URL (e.g., https://accounts.example.com).
    
    Returns
    -------
    OidcConfig
        Parsed configuration.
    
    Raises
    ------
    httpx.HTTPStatusError
        If the request returns a non-2xx status.
    """
    from .network import async_safe_get, NetworkError
    url = f"{issuer}/.well-known/openid-configuration"
    raw = await async_safe_get(url, timeout=10)
    data = json.loads(raw)

    return OidcConfig(
        issuer=data["issuer"],
        authorization_endpoint=data["authorization_endpoint"],
        token_endpoint=data["token_endpoint"],
        jwks_uri=data["jwks_uri"],
        registration_endpoint=data.get("registration_endpoint"),
    )


async def webid_to_issuer(webid: str) -> Optional[str]:
    """Extract the OIDC issuer URL from a WebID.
    
    Fetches the WebID document (as Turtle RDF) and looks for
    the oidcIssuer predicate to find the issuer URL.
    
    Parameters
    ----------
    webid : str
        The WebID URL (e.g., https://alice.pod/profile/card#me).
    
    Returns
    -------
    str or None
        The issuer URL if found, None otherwise.
    """
    try:
        from .network import async_safe_get, NetworkError
        raw = await async_safe_get(webid, headers={"Accept": "text/turtle"}, timeout=10)
        body = raw.decode("utf-8", errors="replace")
    except Exception:
        return None
    
    # Look for oidcIssuer predicate in Turtle format
    # Expect lines like: <#me> <http://...solid.../oidcIssuer> <https://issuer.example> .
    for line in body.split("\n"):
        if "oidcIssuer" in line:
            # Try to extract URL between < >
            start = line.find("<https://")
            if start >= 0:
                end = line.find(">", start)
                if end > start:
                    return line[start + 1 : end]
            start = line.find("<http://")
            if start >= 0:
                end = line.find(">", start)
                if end > start:
                    return line[start + 1 : end]
    
    return None


async def dynamic_register(
    registration_endpoint: str,
    redirect_uris: list[str],
) -> dict:
    """Dynamically register a client with an OIDC provider.
    
    POSTs a registration request to the provider's registration endpoint.
    
    Parameters
    ----------
    registration_endpoint : str
        The provider's client registration endpoint.
    redirect_uris : list[str]
        List of redirect URIs for the client (e.g., ["http://127.0.0.1:8080/callback"]).
    
    Returns
    -------
    dict
        The registration response (includes client_id, etc.).
    
    Raises
    ------
    httpx.HTTPStatusError
        If the request returns a non-2xx status.
    """
    payload = {
        "application_type": "native",
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
    }
    
    from .network import async_safe_post_content, NetworkError
    raw = await async_safe_post_content(registration_endpoint, payload, timeout=10)
    return json.loads(raw)
