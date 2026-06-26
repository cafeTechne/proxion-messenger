"""Integration tests for CSS DPoP client and credential caching.

These tests require a live Solid server instance (CSS or NSS).
Set CSS_ALICE_URL env var to enable (e.g., http://localhost:3001 or https://solidweb.org).
"""

import pytest
import uuid

from proxion_messenger_core.nss_setup import make_pod_client
from proxion_messenger_core.solid_client import SolidClient, SolidError


@pytest.mark.integration
def test_css_dpop_put_get(css_alice_url, alice_agent):
    """Test DPoP/Bearer client PUT and GET operations."""
    credentials, pod_url, webid, client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    data = b"hello dpop world"
    client.put("stash://pod/test-file.txt", data)
    result = client.get("stash://pod/test-file.txt")

    assert result == data


@pytest.mark.integration
def test_css_dpop_delete(css_alice_url, alice_agent):
    """Test DPoP/Bearer client DELETE operation."""
    credentials, pod_url, webid, client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    client.put("stash://pod/to-delete.txt", b"bye")
    client.delete("stash://pod/to-delete.txt")

    with pytest.raises(SolidError):
        client.get("stash://pod/to-delete.txt")


@pytest.mark.integration
def test_css_dpop_token_caches(css_alice_url, alice_agent):
    """Test that credentials cache access tokens."""
    credentials, pod_url, webid, client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    t1 = credentials.get_token()
    t2 = credentials.get_token()

    assert t1 == t2

    # Verify the cached token works for actual requests
    data = b"cached token test"
    client.put("stash://pod/cache-test.txt", data)
    result = client.get("stash://pod/cache-test.txt")

    assert result == data


@pytest.mark.integration
def test_css_acl_denies_unauthorized(css_alice_url, alice_agent):
    """Test that WAC ACL enforcement denies unauthenticated access.

    Alice creates a private resource on her Solid Pod, then an unauthenticated
    client (plain SolidClient, no DPoP/Bearer) attempts to GET it. Server should return
    401/403, raising SolidError.
    """
    credentials, pod_url, webid, client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    # Alice creates a private resource
    private_path = "stash://pod/private-data.txt"
    client.put(private_path, b"secret content")

    # Unauthenticated client tries to read it
    unauthenticated_client = SolidClient(client._resolver)

    # Should raise SolidError (401 or 403)
    with pytest.raises(SolidError):
        unauthenticated_client.get(private_path)
