"""Unit tests for CssAccountManager and build_dpop_client — CSS API v0.5 (cookie-based)."""
import pytest
import respx
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.css_setup import CssAccountManager, build_dpop_client
from proxion_messenger_core.css_auth import CssClientCredentials, DpopSolidClient
from proxion_messenger_core.errors import CssAccountExistsError


BASE = "http://localhost:3001"
ACCOUNT_ID = "aaaa-bbbb-cccc"
PW_URL = f"{BASE}/.account/account/{ACCOUNT_ID}/login/password/"
POD_URL = f"{BASE}/.account/account/{ACCOUNT_ID}/pod/"
CREDS_URL = f"{BASE}/.account/account/{ACCOUNT_ID}/client-credentials/"

UNAUTH_CONTROLS = {
    "controls": {
        "password": {"login": f"{BASE}/.account/login/password/"},
        "account": {"create": f"{BASE}/.account/account/"},
    },
    "version": "0.5",
}

AUTH_CONTROLS = {
    "controls": {
        "password": {"create": PW_URL, "login": f"{BASE}/.account/login/password/"},
        "account": {
            "pod": POD_URL,
            "clientCredentials": CREDS_URL,
            "create": f"{BASE}/.account/account/",
        },
    },
    "version": "0.5",
}


@pytest.fixture
def mgr():
    return CssAccountManager(BASE)


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


def _mock_register_flow(router, *, pw_status=200, pw_body=None):
    """Set up respx mocks for the 3-step CSS v0.5 registration flow."""
    router.post(f"{BASE}/.account/account/").mock(
        return_value=httpx.Response(
            200,
            json=UNAUTH_CONTROLS,
            headers={"Set-Cookie": f"css-account=test-session; Path=/"},
        )
    )
    router.get(f"{BASE}/.account/").mock(
        return_value=httpx.Response(200, json=AUTH_CONTROLS)
    )
    if pw_body is None:
        pw_body = {"resource": PW_URL + "pw-id/"}
    router.post(PW_URL).mock(
        return_value=httpx.Response(pw_status, json=pw_body)
    )


def test_register_returns_session_cookie(mgr):
    """register() returns the css-account cookie value."""
    with respx.mock:
        _mock_register_flow(respx)
        result = mgr.register("alice@test.com", "pass123")
    assert result == "test-session"


def test_register_raises_on_400_already_exists(mgr):
    """register() raises CssAccountExistsError on any 400 from the password endpoint."""
    with respx.mock:
        _mock_register_flow(
            respx,
            pw_status=400,
            pw_body={"message": "There already is a login for this e-mail address."},
        )
        with pytest.raises(CssAccountExistsError):
            mgr.register("alice@test.com", "pass123")


def test_register_raises_on_400_without_already_keyword(mgr):
    """register() raises CssAccountExistsError even when 400 body doesn't contain 'already'."""
    with respx.mock:
        _mock_register_flow(
            respx,
            pw_status=400,
            pw_body={"message": "This email is already in use."},
        )
        with pytest.raises(CssAccountExistsError):
            mgr.register("alice@test.com", "pass123")


def test_register_raises_on_400_bare_account_url(mgr):
    """register() raises CssAccountExistsError when CSS uses bare account URL as password.create."""
    # Some CSS deployments return the bare account URL for password.create.
    # A 400 at that URL must still be treated as account-exists.
    bare_pw_url = f"{BASE}/.account/account/{ACCOUNT_ID}"
    custom_auth = {
        "controls": {
            "password": {"create": bare_pw_url, "login": f"{BASE}/.account/login/password/"},
            "account": {"pod": POD_URL, "clientCredentials": CREDS_URL},
        }
    }
    with respx.mock:
        respx.post(f"{BASE}/.account/account/").mock(
            return_value=httpx.Response(200, json={}, headers={"Set-Cookie": "css-account=s; Path=/"})
        )
        respx.get(f"{BASE}/.account/").mock(return_value=httpx.Response(200, json=custom_auth))
        respx.post(bare_pw_url).mock(return_value=httpx.Response(400, json={"message": "Bad Request"}))
        with pytest.raises(CssAccountExistsError):
            mgr.register("alice@test.com", "pass123")


def test_register_raises_http_error_on_500(mgr):
    """register() propagates unexpected HTTP errors from account creation."""
    with respx.mock:
        respx.post(f"{BASE}/.account/account/").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )
        # _create_account_session calls raise_for_status()
        with pytest.raises(httpx.HTTPStatusError):
            mgr.register("alice@test.com", "pass123")


def test_connect_agent_new_account(mgr, key):
    """connect_agent() registers, creates pod, issues credentials, returns tuple."""
    with respx.mock:
        _mock_register_flow(respx)
        respx.post(POD_URL).mock(
            return_value=httpx.Response(200, json={
                "pod": f"{BASE}/alice/",
                "webId": f"{BASE}/alice/profile/card#me",
            })
        )
        respx.post(CREDS_URL).mock(
            return_value=httpx.Response(200, json={"id": "cid-1", "secret": "sec-1"})
        )

        creds, pod_url, webid = mgr.connect_agent(key, "alice@test.com", "pass123")

    assert pod_url == f"{BASE}/alice/"
    assert webid == f"{BASE}/alice/profile/card#me"
    assert isinstance(creds, CssClientCredentials)
    assert creds.client_id == "cid-1"
    assert creds.client_secret == "sec-1"


def test_connect_agent_existing_account_falls_back_to_login(mgr, key):
    """connect_agent() falls back to login when email already exists."""
    LOGIN_URL = f"{BASE}/.account/login/password/"

    with respx.mock:
        # Account creation succeeds, but password setting says already exists
        _mock_register_flow(
            respx,
            pw_status=400,
            pw_body={"message": "There already is a login for this e-mail address."},
        )
        # Login flow
        respx.post(LOGIN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"authorization": "login-token"},
                headers={"Set-Cookie": f"css-account=login-session; Path=/"},
            )
        )
        # After login, GET /.account/ returns auth controls (already mocked via GET above)
        respx.get(POD_URL).mock(
            return_value=httpx.Response(200, json={
                "pods": {f"{BASE}/alice/": POD_URL + "pod-id/"},
            })
        )
        respx.post(CREDS_URL).mock(
            return_value=httpx.Response(200, json={"id": "cid-2", "secret": "sec-2"})
        )

        creds, pod_url, webid = mgr.connect_agent(key, "alice@test.com", "pass123")

    assert pod_url == f"{BASE}/alice/"
    assert webid == f"{BASE}/alice/profile/card#me"
    assert creds.client_id == "cid-2"


def test_connect_agent_revokes_duplicate_credential(mgr, key):
    """connect_agent() revokes an existing credential with the same name and re-issues."""
    with respx.mock:
        _mock_register_flow(respx)
        respx.post(POD_URL).mock(
            return_value=httpx.Response(200, json={
                "pod": f"{BASE}/alice/",
                "webId": f"{BASE}/alice/profile/card#me",
            })
        )
        # First POST returns 409 (duplicate name)
        cred_id = "existing-cred-id"
        respx.post(CREDS_URL).mock(side_effect=[
            httpx.Response(409, json={"message": "Credential name already in use"}),
            httpx.Response(200, json={"id": "new-cid", "secret": "new-sec"}),
        ])
        # GET to list credentials
        respx.get(CREDS_URL).mock(
            return_value=httpx.Response(200, json={
                "clientCredentials": {cred_id: {"name": "proxion", "webId": f"{BASE}/alice/profile/card#me"}}
            })
        )
        # DELETE the existing credential
        respx.delete(f"{CREDS_URL}{cred_id}").mock(return_value=httpx.Response(204))

        creds, pod_url, webid = mgr.connect_agent(key, "alice@test.com", "pass123")

    assert creds.client_id == "new-cid"
    assert creds.client_secret == "new-sec"


def test_build_dpop_client_returns_dpop_client(key):
    """build_dpop_client() returns a DpopSolidClient with correct credentials."""
    creds = CssClientCredentials(
        css_base_url=BASE,
        client_id="x",
        client_secret="y",
        identity_key=key,
    )

    result = build_dpop_client(creds, f"{BASE}/alice/", stash_owner="alice")

    assert isinstance(result, DpopSolidClient)
    assert result._credentials is creds


def test_validate_pod_url_same_origin_accepted(mgr):
    """Pod on the exact same origin as CSS base URL is accepted."""
    mgr._validate_pod_url(f"{BASE}/alice/")


def test_validate_pod_url_subdomain_accepted():
    """Pod hosted on a subdomain of the CSS base URL is accepted.

    solidcommunity.net hosts pods at {username}.solidcommunity.net, so
    subdomain URLs must be allowed. Both base and subdomain must use the
    same scheme and port (here: no explicit port → default 80/443).
    """
    mgr_noport = CssAccountManager("http://localhost")
    mgr_noport._validate_pod_url("http://alice.localhost/")


def test_validate_pod_url_different_origin_rejected(mgr):
    """Pod on a completely different domain is rejected."""
    with pytest.raises(ValueError, match="different server"):
        mgr._validate_pod_url("http://evil.com/alice/")


def test_validate_pod_url_different_scheme_rejected(mgr):
    """Pod using a different scheme (http vs https) is rejected."""
    https_mgr = CssAccountManager("https://localhost:3001")
    with pytest.raises(ValueError, match="different server"):
        https_mgr._validate_pod_url("http://localhost:3001/alice/")


def test_connect_agent_accepts_subdomain_pod(key):
    """connect_agent() accepts pods hosted on subdomains of the CSS base URL.

    Models solidcommunity.net: base URL has no explicit port; subdomain pod URL
    also has no explicit port — ports are equal (both None → default).
    """
    SC_BASE = "http://localhost"
    SC_ACCOUNT_ID = "sc-acct-id"
    SC_PW_URL = f"{SC_BASE}/.account/account/{SC_ACCOUNT_ID}/login/password/"
    SC_POD_URL = f"{SC_BASE}/.account/account/{SC_ACCOUNT_ID}/pod/"
    SC_CREDS_URL = f"{SC_BASE}/.account/account/{SC_ACCOUNT_ID}/client-credentials/"
    subdomain_pod = "http://alice.localhost/alice/"

    sc_unauth = {
        "controls": {
            "password": {"login": f"{SC_BASE}/.account/login/password/"},
            "account": {"create": f"{SC_BASE}/.account/account/"},
        }
    }
    sc_auth = {
        "controls": {
            "password": {"create": SC_PW_URL, "login": f"{SC_BASE}/.account/login/password/"},
            "account": {"pod": SC_POD_URL, "clientCredentials": SC_CREDS_URL},
        }
    }

    sc_mgr = CssAccountManager(SC_BASE)
    with respx.mock:
        # Account creation → already exists (400)
        respx.post(f"{SC_BASE}/.account/account/").mock(
            return_value=httpx.Response(200, json=sc_unauth, headers={"Set-Cookie": "css-account=sc1; Path=/"})
        )
        respx.get(f"{SC_BASE}/.account/").mock(return_value=httpx.Response(200, json=sc_auth))
        respx.post(SC_PW_URL).mock(
            return_value=httpx.Response(400, json={"message": "already"})
        )
        # Login flow
        respx.post(f"{SC_BASE}/.account/login/password/").mock(
            return_value=httpx.Response(200, json={}, headers={"Set-Cookie": "css-account=sc2; Path=/"})
        )
        respx.get(SC_POD_URL).mock(
            return_value=httpx.Response(200, json={"pods": {subdomain_pod: SC_POD_URL + "pod-id/"}})
        )
        respx.post(SC_CREDS_URL).mock(
            return_value=httpx.Response(200, json={"id": "cid-s", "secret": "sec-s"})
        )

        creds, pod_url, webid = sc_mgr.connect_agent(key, "alice@test.com", "pass123")

    assert pod_url == subdomain_pod
    assert webid == "http://alice.localhost/alice/profile/card#me"


def test_parse_jwt_exp_returns_zero_on_garbage():
    from proxion_messenger_core.css_setup import _parse_jwt_exp
    assert _parse_jwt_exp("not.a.jwt") == 0.0
    assert _parse_jwt_exp("") == 0.0
    assert _parse_jwt_exp("a.b.c") == 0.0


def test_parse_jwt_exp_returns_correct_value():
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 9999999999}).encode()).decode().rstrip("=")
    token = f"header.{payload}.sig"
    from proxion_messenger_core.css_setup import _parse_jwt_exp
    assert _parse_jwt_exp(token) == 9999999999.0
