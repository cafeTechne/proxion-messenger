"""Tests for Layer 3 — Solid Pod federation at the gateway level."""
import pytest
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch, call

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


def _make_gateway(tmp_db=None, **cfg_kwargs):
    key = Ed25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    config = GatewayConfig(db_path=tmp_db, **cfg_kwargs)
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=config,
        read_state=ReadState(),
    )


# ── _proxion_address ──────────────────────────────────────────────────────────

def test_proxion_address_includes_did_and_http_url(tmp_path):
    gw = _make_gateway(public_url="wss://chat.example.com")
    addr = gw._proxion_address()
    assert "@" in addr
    did, url = addr.split("@", 1)
    assert did.startswith("did:key:")
    assert url == "https://chat.example.com"


def test_proxion_address_ws_converted_to_http(tmp_path):
    gw = _make_gateway(host="0.0.0.0", port=7474)
    addr = gw._proxion_address()
    assert "@http://" in addr


def test_proxion_address_did_matches_identity_key():
    key = Ed25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(public_url="wss://example.com"),
        read_state=ReadState(),
    )
    expected_did = pub_key_to_did(pub_bytes)
    assert gw._proxion_address().startswith(expected_did + "@")


# ── get_my_address command ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_my_address_command_returns_address():
    gw = _make_gateway(public_url="wss://chat.example.com")
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = "did:key:podstarttest1"
    await gw.process_command(ws, {"cmd": "get_my_address"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "my_address"
    assert sent["proxion_address"].startswith("did:key:")
    assert "@https://chat.example.com" in sent["proxion_address"]
    assert sent["did"].startswith("did:key:")
    assert sent["gateway_url"] == "wss://chat.example.com"


# ── connect_css command ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connect_css_missing_fields_returns_error():
    gw = _make_gateway()
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = pub_key_to_did(gw.agent.identity_pub_bytes)
    await gw.process_command(ws, {"cmd": "connect_css", "css_url": "https://pod.example.com"})
    sent = json.loads(ws.send.call_args[0][0])
    # Schema validation (E_SCHEMA) or handler-level css_error both indicate a bad request
    assert sent["type"] in ("css_error", "error")
    assert sent.get("code") == "E_SCHEMA" or "required" in sent.get("message", "")


@pytest.mark.asyncio
async def test_connect_css_calls_connect_agent_and_returns_event():
    gw = _make_gateway(public_url="wss://chat.example.com")
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = pub_key_to_did(gw.agent.identity_pub_bytes)

    fake_creds = MagicMock()
    fake_client = MagicMock()

    def fake_connect_css_sync(css_url, email, password):
        gw.dm_clients["did:key:alice-pod"] = (fake_creds, fake_client)
        return fake_creds, "https://pod.example.com/alice/", "did:key:alice-pod"

    _fake = [(None, None, None, None, ("93.184.216.34", 0))]
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_fake), \
         patch.object(gw, "_connect_css_sync", side_effect=fake_connect_css_sync):
        await gw.process_command(ws, {
            "cmd": "connect_css",
            "css_url": "https://pod.example.com",
            "email": "alice@example.com",
            "password": "secret",
        })

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "css_connected"
    assert sent["pod_url"] == "https://pod.example.com/alice/"
    assert sent["webid"] == "did:key:alice-pod"
    assert "proxion_address" in sent
    # Pod client should have been registered in dm_clients
    assert "did:key:alice-pod" in gw.dm_clients


@pytest.mark.asyncio
async def test_connect_css_exception_returns_css_error():
    gw = _make_gateway()
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = pub_key_to_did(gw.agent.identity_pub_bytes)

    _fake = [(None, None, None, None, ("93.184.216.34", 0))]
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_fake), \
         patch.object(gw, "_connect_css_sync", side_effect=RuntimeError("auth failed")):
        await gw.process_command(ws, {
            "cmd": "connect_css",
            "css_url": "https://pod.example.com",
            "email": "alice@example.com",
            "password": "wrong",
        })

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "css_error"
    assert "auth failed" in sent["message"]


@pytest.mark.asyncio
async def test_connect_css_rejects_private_url():
    """connect_css must reject css_url that resolves to a private address."""
    gw = _make_gateway()
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = pub_key_to_did(gw.agent.identity_pub_bytes)

    await gw.process_command(ws, {
        "cmd": "connect_css",
        "css_url": "http://127.0.0.1:4000",
        "email": "alice@example.com",
        "password": "secret",
    })

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "css_error"
    assert "private" in sent["message"].lower() or "disallowed" in sent["message"].lower()


@pytest.mark.asyncio
async def test_connect_css_allows_private_url_with_env_override(monkeypatch):
    """With PROXION_ALLOW_PRIVATE_RELAY=1, a local CSS URL should pass SSRF check."""
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    gw = _make_gateway()
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = pub_key_to_did(gw.agent.identity_pub_bytes)

    fake_creds = MagicMock()
    fake_client = MagicMock()

    def _fake_sync(css_url, email, password):
        gw.dm_clients["did:key:local"] = (fake_creds, fake_client)
        return fake_creds, "http://127.0.0.1:4000/alice/", "did:key:local"

    with patch.object(gw, "_connect_css_sync", side_effect=_fake_sync):
        await gw.process_command(ws, {
            "cmd": "connect_css",
            "css_url": "http://127.0.0.1:4000",
            "email": "alice@example.com",
            "password": "secret",
        })

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "css_connected"


# ── _setup_pod_connection ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_pod_connection_calls_connect_when_configured():
    gw = _make_gateway(
        css_url="https://pod.example.com",
        css_email="alice@example.com",
        css_password="secret",
    )
    called_with = []

    def fake_connect(css_url, email, password):
        called_with.append((css_url, email, password))
        return MagicMock(), "https://pod.example.com/alice/", "did:key:alice"

    with patch.object(gw, "_connect_css_sync", side_effect=fake_connect):
        await gw._setup_pod_connection()

    assert len(called_with) == 1
    assert called_with[0] == ("https://pod.example.com", "alice@example.com", "secret")


@pytest.mark.asyncio
async def test_setup_pod_connection_noop_without_config():
    gw = _make_gateway()  # no css_url/email/password
    called = []

    with patch.object(gw, "_connect_css_sync", side_effect=lambda *a: called.append(a)):
        await gw._setup_pod_connection()

    assert len(called) == 0


@pytest.mark.asyncio
async def test_setup_pod_connection_warns_on_failure():
    gw = _make_gateway(
        css_url="https://pod.example.com",
        css_email="alice@example.com",
        css_password="bad",
    )
    with patch.object(gw, "_connect_css_sync", side_effect=Exception("connection refused")):
        # Should not raise — just log a warning
        await gw._setup_pod_connection()


# ── CssAccountManager.login and connect_agent ─────────────────────────────────

def test_css_login_returns_cookie_value():
    import respx
    import httpx
    from proxion_messenger_core.css_setup import CssAccountManager

    BASE = "https://pod.example.com"
    ACCOUNT_ID = "test-acct"
    mgr = CssAccountManager(BASE)

    unauth_controls = {"controls": {"password": {"login": f"{BASE}/.account/login/password/"}}, "version": "0.5"}
    auth_controls = {"controls": {"password": {}, "account": {}}, "version": "0.5"}

    with respx.mock:
        respx.get(f"{BASE}/.account/").mock(
            return_value=httpx.Response(200, json=unauth_controls)
        )
        respx.post(f"{BASE}/.account/login/password/").mock(
            return_value=httpx.Response(
                200,
                json={"authorization": "sess-tok"},
                headers={"Set-Cookie": "css-account=sess-tok; Path=/"},
            )
        )
        # Second GET after login
        respx.get(f"{BASE}/.account/").mock(
            return_value=httpx.Response(200, json=auth_controls)
        )
        tok = mgr.login("alice@example.com", "secret")
    assert tok == "sess-tok"


def test_css_connect_agent_falls_back_to_login_on_existing_email():
    import respx
    import httpx
    from proxion_messenger_core.css_setup import CssAccountManager

    BASE = "https://pod.example.com"
    ACCOUNT_ID = "test-acct"
    PW_URL = f"{BASE}/.account/account/{ACCOUNT_ID}/login/password/"
    POD_URL_CTRL = f"{BASE}/.account/account/{ACCOUNT_ID}/pod/"
    CREDS_URL = f"{BASE}/.account/account/{ACCOUNT_ID}/client-credentials/"
    LOGIN_URL = f"{BASE}/.account/login/password/"

    mgr = CssAccountManager(BASE)
    key = Ed25519PrivateKey.generate()

    unauth_ctrl = {"controls": {"password": {"login": LOGIN_URL}, "account": {"create": f"{BASE}/.account/account/"}}, "version": "0.5"}
    auth_ctrl_new = {"controls": {"password": {"create": PW_URL}, "account": {"pod": POD_URL_CTRL, "clientCredentials": CREDS_URL}}, "version": "0.5"}

    with respx.mock:
        # Step 1: create account
        respx.post(f"{BASE}/.account/account/").mock(
            return_value=httpx.Response(200, json=unauth_ctrl, headers={"Set-Cookie": "css-account=new-sess; Path=/"})
        )
        # Step 2: GET with new session cookie
        respx.get(f"{BASE}/.account/").mock(
            return_value=httpx.Response(200, json=auth_ctrl_new)
        )
        # Step 3: set password → already exists
        respx.post(PW_URL).mock(
            return_value=httpx.Response(400, json={"message": "There already is a login for this e-mail address."})
        )
        # Login fallback
        respx.post(LOGIN_URL).mock(
            return_value=httpx.Response(200, json={}, headers={"Set-Cookie": "css-account=login-sess; Path=/"})
        )
        # Pod listing
        respx.get(POD_URL_CTRL).mock(
            return_value=httpx.Response(200, json={"pods": {f"{BASE}/alice/": POD_URL_CTRL + "pod-id/"}})
        )
        # Credentials
        respx.post(CREDS_URL).mock(
            return_value=httpx.Response(200, json={"id": "cid", "secret": "csecret"})
        )

        creds, pod_url, webid = mgr.connect_agent(key, "alice@example.com", "secret")

    assert pod_url == f"{BASE}/alice/"
    assert webid == f"{BASE}/alice/profile/card#me"


def test_css_connect_agent_registers_new_account():
    import respx
    import httpx
    from proxion_messenger_core.css_setup import CssAccountManager

    BASE = "https://pod.example.com"
    ACCOUNT_ID = "test-acct"
    PW_URL = f"{BASE}/.account/account/{ACCOUNT_ID}/login/password/"
    POD_URL_CTRL = f"{BASE}/.account/account/{ACCOUNT_ID}/pod/"
    CREDS_URL = f"{BASE}/.account/account/{ACCOUNT_ID}/client-credentials/"

    mgr = CssAccountManager(BASE)
    key = Ed25519PrivateKey.generate()

    unauth_ctrl = {"controls": {"password": {}, "account": {"create": f"{BASE}/.account/account/"}}, "version": "0.5"}
    auth_ctrl = {"controls": {"password": {"create": PW_URL}, "account": {"pod": POD_URL_CTRL, "clientCredentials": CREDS_URL}}, "version": "0.5"}

    with respx.mock:
        respx.post(f"{BASE}/.account/account/").mock(
            return_value=httpx.Response(200, json=unauth_ctrl, headers={"Set-Cookie": "css-account=new-sess; Path=/"})
        )
        respx.get(f"{BASE}/.account/").mock(
            return_value=httpx.Response(200, json=auth_ctrl)
        )
        respx.post(PW_URL).mock(
            return_value=httpx.Response(200, json={"resource": PW_URL + "pw-id/"})
        )
        respx.post(POD_URL_CTRL).mock(
            return_value=httpx.Response(200, json={"pod": f"{BASE}/alice/", "webId": f"{BASE}/alice/profile/card#me"})
        )
        respx.post(CREDS_URL).mock(
            return_value=httpx.Response(200, json={"id": "cid2", "secret": "sec2"})
        )

        creds, pod_url, webid = mgr.connect_agent(key, "alice@example.com", "secret")

    assert pod_url == f"{BASE}/alice/"
    assert webid == f"{BASE}/alice/profile/card#me"
    assert creds.client_id == "cid2"
