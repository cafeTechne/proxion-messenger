"""Tests for JSS (JavaScript Solid Server) support — detection, provisioning, bearer auth.

Unit tests mock JSS's HTTP endpoints with respx. A single live-integration test runs the
real flow against a running JSS (skipped unless one is reachable), matching what was
verified by hand against JSS v0.0.220.
"""
from __future__ import annotations

import base64
import json
import os
import time

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.nss_setup import detect_server_type, make_pod_client
from proxion_messenger_core.jss_setup import (
    JssAccountManager, JssBearerCredentials, JssAuthError,
    _jwt_claims, _pod_url_from_webid,
)

BASE = "http://jss.test"


def _fake_jwt(webid: str, exp_in: int = 3600) -> str:
    payload = {"webid": webid, "exp": int(time.time()) + exp_in}
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJSUzI1NiJ9.{b}.signature"


def _key():
    return Ed25519PrivateKey.generate()


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_pod_url_from_webid():
    assert _pod_url_from_webid("http://h:4455/alice/profile/card.jsonld#me") == "http://h:4455/alice/"
    assert _pod_url_from_webid("http://h/bob/profile/card#me") == "http://h/bob/"
    # No /profile/ — fall back to stripping the last segment.
    assert _pod_url_from_webid("http://h/carol/card#me") == "http://h/carol/"


def test_jwt_claims_decodes_and_is_safe():
    tok = _fake_jwt("http://h/alice/profile/card#me")
    assert _jwt_claims(tok)["webid"] == "http://h/alice/profile/card#me"
    assert _jwt_claims("garbage") == {}
    assert _jwt_claims("") == {}


# ── detection ─────────────────────────────────────────────────────────────────

def test_detect_jss():
    with respx.mock:
        respx.get(f"{BASE}/.account/").mock(return_value=httpx.Response(401))
        respx.get(f"{BASE}/idp/credentials").mock(return_value=httpx.Response(200))
        assert detect_server_type(BASE) == "jss"


def test_detect_css_takes_precedence():
    with respx.mock:
        respx.get(f"{BASE}/.account/").mock(
            return_value=httpx.Response(200, json={"controls": {}}))
        assert detect_server_type(BASE) == "css"


def test_detect_nss_when_no_jss_signal():
    with respx.mock:
        respx.get(f"{BASE}/.account/").mock(return_value=httpx.Response(404))
        respx.get(f"{BASE}/idp/credentials").mock(return_value=httpx.Response(404))
        assert detect_server_type(BASE) == "nss"


def test_detect_unknown_when_unreachable():
    with respx.mock:
        respx.get(f"{BASE}/.account/").mock(side_effect=httpx.ConnectError("down"))
        assert detect_server_type(BASE) == "unknown"


# ── bearer credentials ────────────────────────────────────────────────────────

def test_get_token_and_cache():
    webid = f"{BASE}/alice/profile/card.jsonld#me"
    with respx.mock:
        route = respx.post(f"{BASE}/idp/credentials").mock(
            return_value=httpx.Response(200, json={"access_token": _fake_jwt(webid)}))
        creds = JssBearerCredentials(BASE, "alice@example.org", "pw", _key())
        t1 = creds.get_token()
        t2 = creds.get_token()
        assert t1 and t1 == t2
        assert route.call_count == 1   # second call served from cache
        assert creds.webid() == webid


def test_get_token_raises_on_bad_login():
    with respx.mock:
        respx.post(f"{BASE}/idp/credentials").mock(return_value=httpx.Response(401))
        creds = JssBearerCredentials(BASE, "alice@example.org", "wrong", _key())
        with pytest.raises(JssAuthError):
            creds.get_token()


# ── account manager ───────────────────────────────────────────────────────────

def test_connect_agent_existing_account():
    webid = f"{BASE}/alice/profile/card.jsonld#me"
    with respx.mock:
        respx.post(f"{BASE}/idp/credentials").mock(
            return_value=httpx.Response(200, json={"access_token": _fake_jwt(webid)}))
        creds, pod_url, wid = JssAccountManager(BASE).connect_agent(_key(), "alice@example.org", "pw")
        assert wid == webid
        assert pod_url == f"{BASE}/alice/"


def test_setup_agent_creates_pod_when_login_first_fails():
    webid = f"{BASE}/carol/profile/card.jsonld#me"
    with respx.mock:
        # First login attempt fails (account doesn't exist), pod creation succeeds,
        # then the retry login succeeds.
        login = respx.post(f"{BASE}/idp/credentials")
        login.side_effect = [
            httpx.Response(401),
            httpx.Response(200, json={"access_token": _fake_jwt(webid)}),
        ]
        create = respx.post(f"{BASE}/.pods").mock(
            return_value=httpx.Response(201, json={"webId": webid, "podUri": f"{BASE}/carol/"}))
        creds, pod_url, wid = JssAccountManager(BASE).setup_agent(
            _key(), "carol@example.org", "pw", name="carol")
        assert wid == webid and pod_url == f"{BASE}/carol/"
        assert create.called
        assert login.call_count == 2


def test_make_pod_client_routes_jss():
    webid = f"{BASE}/alice/profile/card.jsonld#me"
    with respx.mock:
        respx.get(f"{BASE}/.account/").mock(return_value=httpx.Response(401))
        respx.get(f"{BASE}/idp/credentials").mock(return_value=httpx.Response(200))
        respx.post(f"{BASE}/idp/credentials").mock(
            return_value=httpx.Response(200, json={"access_token": _fake_jwt(webid)}))
        creds, pod_url, wid, client = make_pod_client(BASE, _key(), "alice@example.org", "pw")
        assert type(client).__name__ == "JssBearerSolidClient"
        assert wid == webid and pod_url == f"{BASE}/alice/"


# ── live integration (skipped unless a real JSS is reachable) ──────────────────

@pytest.mark.integration
def test_jss_live_roundtrip():
    base = os.getenv("PROXION_JSS_URL", "http://127.0.0.1:4455")
    try:
        httpx.get(base, timeout=2.0)
    except Exception:
        pytest.skip("no live JSS reachable")
    assert detect_server_type(base) == "jss"
    mgr = JssAccountManager(base)
    creds, pod_url, webid = mgr.setup_agent(
        _key(), os.getenv("PROXION_JSS_EMAIL", "alice@example.org"),
        os.getenv("PROXION_JSS_PASSWORD", "test1234"))
    assert webid.startswith(base) and pod_url.endswith("/")
    assert len(creds.get_token()) > 0
