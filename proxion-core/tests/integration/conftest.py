"""Shared fixtures for integration tests that require live Solid instances (CSS or NSS)."""
import os
import socket
import subprocess
import time
import pytest
import httpx
import uuid
from pathlib import Path
from proxion_messenger_core.persist import AgentState

# Root of the repo (two levels up from tests/integration/)
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_COMPOSE_FILE = str(_REPO_ROOT / "docker-compose.test.yml")

_CSS_ALICE_DEFAULT = "http://localhost:3001"
_CSS_BOB_DEFAULT   = "http://localhost:3002"


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("localhost", port)) == 0


def _css_reachable(url: str) -> bool:
    try:
        r = httpx.get(url + "/", timeout=2)
        return r.status_code < 500
    except Exception:
        return False


def _wait_for_css(urls: list, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(_css_reachable(u) for u in urls):
            return True
        time.sleep(2)
    return False


@pytest.fixture(scope="session", autouse=True)
def css_servers():
    """Start two CSS containers for the test session if not already running.

    Also handles external CSS/NSS instances via CSS_ALICE_URL env var.
    """
    # If env vars already set (CI, manual, or external instance), pass through
    if os.environ.get("CSS_ALICE_URL"):
        yield
        return

    # If ports already occupied assume CSS is running externally
    if _port_in_use(3001) or _port_in_use(3002):
        os.environ.setdefault("CSS_ALICE_URL", _CSS_ALICE_DEFAULT)
        os.environ.setdefault("CSS_BOB_URL",   _CSS_BOB_DEFAULT)
        os.environ.setdefault("TEST_POD_A_URL", _CSS_ALICE_DEFAULT)
        os.environ.setdefault("TEST_POD_B_URL", _CSS_BOB_DEFAULT)
        yield
        return

    if not _docker_available():
        pytest.skip(
            "Docker is not available and CSS_ALICE_URL is not set. "
            "Run: docker compose -f docker-compose.test.yml up -d  "
            "then set CSS_ALICE_URL=http://localhost:3001"
        )
        return

    # Start containers
    subprocess.run(
        ["docker", "compose", "-f", _COMPOSE_FILE, "up", "-d", "--quiet-pull"],
        check=True, cwd=str(_REPO_ROOT),
    )

    urls = [_CSS_ALICE_DEFAULT, _CSS_BOB_DEFAULT]
    if not _wait_for_css(urls, timeout=90):
        subprocess.run(
            ["docker", "compose", "-f", _COMPOSE_FILE, "down", "-v"],
            cwd=str(_REPO_ROOT),
        )
        pytest.fail("CSS containers did not become healthy within 90s")

    os.environ["CSS_ALICE_URL"]   = _CSS_ALICE_DEFAULT
    os.environ["CSS_BOB_URL"]     = _CSS_BOB_DEFAULT
    os.environ["TEST_POD_A_URL"]  = _CSS_ALICE_DEFAULT
    os.environ["TEST_POD_B_URL"]  = _CSS_BOB_DEFAULT

    yield

    # Teardown: stop and remove volumes (clean state for next run)
    subprocess.run(
        ["docker", "compose", "-f", _COMPOSE_FILE, "down", "-v"],
        cwd=str(_REPO_ROOT),
    )
    for var in ("CSS_ALICE_URL", "CSS_BOB_URL", "TEST_POD_A_URL", "TEST_POD_B_URL"):
        os.environ.pop(var, None)


def pytest_collection_modifyitems(config, items):
    """No longer auto-skip @integration tests — the fixture handles startup."""
    pass


@pytest.fixture
def css_alice_url(css_servers):
    """Alice's Solid pod server URL (CSS or NSS)."""
    return os.environ["CSS_ALICE_URL"].rstrip("/")


@pytest.fixture
def css_bob_url(css_servers):
    """Bob's Solid pod server URL (CSS or NSS)."""
    return os.environ["CSS_BOB_URL"].rstrip("/")


@pytest.fixture
def alice_email(css_servers):
    """Alice's account identifier (email for CSS, username for NSS)."""
    return os.environ.get("CSS_ALICE_EMAIL", f"alice-{uuid.uuid4().hex[:8]}@test.example")


@pytest.fixture
def bob_email(css_servers):
    """Bob's account identifier (email for CSS, username for NSS)."""
    return os.environ.get("CSS_BOB_EMAIL", f"bob-{uuid.uuid4().hex[:8]}@test.example")


@pytest.fixture
def alice_password(css_servers):
    """Alice's account password."""
    return os.environ.get("CSS_ALICE_PASSWORD", "password123")


@pytest.fixture
def bob_password(css_servers):
    """Bob's account password."""
    return os.environ.get("CSS_BOB_PASSWORD", "password123")


@pytest.fixture
def alice_agent():
    return AgentState.generate()


@pytest.fixture
def bob_agent():
    return AgentState.generate()


@pytest.fixture(scope="session")
def alice_pod_setup(css_servers, alice_agent):
    """Setup Alice's pod (CSS or NSS) and return credentials, pod_url, webid, client.

    Cached at session scope so all tests use the same pod.
    """
    from proxion_messenger_core.nss_setup import make_pod_client

    base = os.environ.get("CSS_ALICE_URL", "http://localhost:3001").rstrip("/")
    username = os.environ.get("CSS_ALICE_EMAIL", f"alice-{uuid.uuid4().hex[:8]}@test.example")
    password = os.environ.get("CSS_ALICE_PASSWORD", "password123")

    try:
        credentials, pod_url, webid, client = make_pod_client(
            base, alice_agent.identity_key, username, password, stash_owner="pod"
        )
        return {
            "credentials": credentials,
            "pod_url": pod_url,
            "webid": webid,
            "client": client,
        }
    except Exception as e:
        pytest.skip(f"Could not set up Alice pod: {e}")


@pytest.fixture(scope="session")
def bob_pod_setup(css_servers, bob_agent):
    """Setup Bob's pod (CSS or NSS) and return credentials, pod_url, webid, client.

    Cached at session scope so all tests use the same pod.
    """
    from proxion_messenger_core.nss_setup import make_pod_client

    base = os.environ.get("CSS_BOB_URL", "http://localhost:3002").rstrip("/")
    username = os.environ.get("CSS_BOB_EMAIL", f"bob-{uuid.uuid4().hex[:8]}@test.example")
    password = os.environ.get("CSS_BOB_PASSWORD", "password123")

    try:
        credentials, pod_url, webid, client = make_pod_client(
            base, bob_agent.identity_key, username, password, stash_owner="pod"
        )
        return {
            "credentials": credentials,
            "pod_url": pod_url,
            "webid": webid,
            "client": client,
        }
    except Exception as e:
        pytest.skip(f"Could not set up Bob pod: {e}")


@pytest.fixture
def alice_client(alice_pod_setup):
    """Alice's pre-built Solid client."""
    return alice_pod_setup["client"]


@pytest.fixture
def bob_client(bob_pod_setup):
    """Bob's pre-built Solid client."""
    return bob_pod_setup["client"]
