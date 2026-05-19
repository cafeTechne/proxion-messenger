"""Pod reachability tests — run against the local CSS Docker instances.

The css_servers session fixture in conftest.py starts the containers and sets
TEST_POD_A_URL / TEST_POD_B_URL before any test in tests/integration/ runs,
so these tests always see the env vars at runtime.
"""
import os
import httpx
import pytest


@pytest.fixture
def pod_a_url(css_servers):
    url = os.environ.get("TEST_POD_A_URL", "")
    if not url:
        pytest.skip("TEST_POD_A_URL not set (Docker unavailable)")
    return url.rstrip("/")


@pytest.fixture
def pod_b_url(css_servers):
    url = os.environ.get("TEST_POD_B_URL", "")
    if not url:
        pytest.skip("TEST_POD_B_URL not set (Docker unavailable)")
    return url.rstrip("/")


@pytest.mark.integration
def test_pod_urls_are_valid(pod_a_url, pod_b_url):
    for url in (pod_a_url, pod_b_url):
        assert url.startswith(("http://", "https://")), f"Invalid URL: {url}"


@pytest.mark.integration
def test_pod_urls_are_different(pod_a_url, pod_b_url):
    assert pod_a_url != pod_b_url, "Both pod URLs are the same — need two distinct pods"


@pytest.mark.integration
def test_pod_a_is_reachable(pod_a_url):
    r = httpx.get(pod_a_url + "/", timeout=10, follow_redirects=True)
    assert r.status_code < 500, f"Pod A returned {r.status_code}"


@pytest.mark.integration
def test_pod_b_is_reachable(pod_b_url):
    r = httpx.get(pod_b_url + "/", timeout=10, follow_redirects=True)
    assert r.status_code < 500, f"Pod B returned {r.status_code}"
