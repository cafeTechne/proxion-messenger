"""Integration tests for Proxion messaging over real Solid Pods (CSS or NSS).

These tests require live Solid server instances.
Set CSS_ALICE_URL and CSS_BOB_URL env vars to enable.
"""

import pytest
import uuid

from proxion_messenger_core import (
    AgentState, compose, send, receive,
    run_bidirectional_handshake,
    MemoryStore,
)
from proxion_messenger_core.nss_setup import make_pod_client
from proxion_messenger_core.solid_auth import set_thread_read_acl
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.solid_client import SolidError


@pytest.mark.integration
def test_css_messaging_send_and_receive(css_alice_url, css_bob_url, alice_agent, bob_agent):
    """Test Alice sends a message to her Pod; Bob reads it."""
    # Alice registers on her Solid pod
    alice_creds, alice_pod_url, alice_webid, alice_client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    # Bob registers on his Solid pod
    bob_creds, bob_pod_url, bob_webid, bob_client = make_pod_client(
        css_bob_url,
        bob_agent.identity_key,
        f"bob-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    # Run bidirectional handshake with MemoryStore
    store = MemoryStore()
    alice_to_bob_capabilities = [Capability(can="read", with_="stash://messages/")]
    bob_to_alice_capabilities = [Capability(can="read", with_="stash://messages/")]

    (cert_ab, valid_ab), (cert_ba, valid_ba) = run_bidirectional_handshake(
        alice_identity_priv=alice_agent.identity_key,
        alice_store_priv=alice_agent.store_key,
        bob_identity_priv=bob_agent.identity_key,
        bob_store_priv=bob_agent.store_key,
        alice_to_bob_capabilities=alice_to_bob_capabilities,
        bob_to_alice_capabilities=bob_to_alice_capabilities,
        store=store,
    )

    assert valid_ab
    assert valid_ba

    # Alice sets thread ACL on her pod
    acl_path = set_thread_read_acl(alice_client, cert_ab, alice_webid, bob_webid)
    assert acl_path.endswith(".acl")

    # Alice composes + sends a message
    msg = compose(alice_agent.identity_key, cert_ab, "Hello Solid!")
    send(msg, alice_client)

    # Bob reads Alice's pod
    received = receive(cert_ab, alice_client, holder_state=bob_agent, signing_key=alice_agent.signing_key_bytes)

    assert len(received) >= 1
    assert received[0].content == "Hello Solid!"


@pytest.mark.integration
def test_css_messaging_bidirectional(css_alice_url, css_bob_url, alice_agent, bob_agent):
    """Test bidirectional messaging: Alice sends to her pod, Bob sends to his pod."""
    # Alice registers on her Solid pod
    alice_creds, alice_pod_url, alice_webid, alice_client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    # Bob registers on his Solid pod
    bob_creds, bob_pod_url, bob_webid, bob_client = make_pod_client(
        css_bob_url,
        bob_agent.identity_key,
        f"bob-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    # Run bidirectional handshake
    store = MemoryStore()
    alice_to_bob_capabilities = [Capability(can="read", with_="stash://messages/")]
    bob_to_alice_capabilities = [Capability(can="read", with_="stash://messages/")]

    (cert_ab, valid_ab), (cert_ba, valid_ba) = run_bidirectional_handshake(
        alice_identity_priv=alice_agent.identity_key,
        alice_store_priv=alice_agent.store_key,
        bob_identity_priv=bob_agent.identity_key,
        bob_store_priv=bob_agent.store_key,
        alice_to_bob_capabilities=alice_to_bob_capabilities,
        bob_to_alice_capabilities=bob_to_alice_capabilities,
        store=store,
    )

    assert valid_ab
    assert valid_ba

    # Set ACLs on both pods
    set_thread_read_acl(alice_client, cert_ab, alice_webid, bob_webid)
    set_thread_read_acl(bob_client, cert_ba, bob_webid, alice_webid)

    # Alice sends message to her pod
    alice_msg = compose(alice_agent.identity_key, cert_ab, "Alice to Bob")
    send(alice_msg, alice_client)

    # Bob sends message to his pod
    bob_msg = compose(bob_agent.identity_key, cert_ba, "Bob to Alice")
    send(bob_msg, bob_client)

    # Bob reads Alice's messages
    alice_messages = receive(cert_ab, alice_client, holder_state=bob_agent, signing_key=alice_agent.signing_key_bytes)
    assert len(alice_messages) >= 1
    assert alice_messages[0].content == "Alice to Bob"

    # Alice reads Bob's messages
    bob_messages = receive(cert_ba, bob_client, holder_state=alice_agent, signing_key=bob_agent.signing_key_bytes)
    assert len(bob_messages) >= 1
    assert bob_messages[0].content == "Bob to Alice"


@pytest.mark.integration
def test_css_messaging_acl_written(css_alice_url, css_bob_url, alice_agent, bob_agent):
    """Test that set_thread_read_acl writes and can be read back."""
    # Alice registers on her Solid pod
    alice_creds, alice_pod_url, alice_webid, alice_client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    # Bob registers on his Solid pod
    bob_creds, bob_pod_url, bob_webid, bob_client = make_pod_client(
        css_bob_url,
        bob_agent.identity_key,
        f"bob-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )

    # Run bidirectional handshake
    store = MemoryStore()
    alice_to_bob_capabilities = [Capability(can="read", with_="stash://messages/")]
    bob_to_alice_capabilities = [Capability(can="read", with_="stash://messages/")]

    (cert_ab, valid_ab), (cert_ba, valid_ba) = run_bidirectional_handshake(
        alice_identity_priv=alice_agent.identity_key,
        alice_store_priv=alice_agent.store_key,
        bob_identity_priv=bob_agent.identity_key,
        bob_store_priv=bob_agent.store_key,
        alice_to_bob_capabilities=alice_to_bob_capabilities,
        bob_to_alice_capabilities=bob_to_alice_capabilities,
        store=store,
    )

    # Write the ACL on Alice's pod
    acl_path = set_thread_read_acl(alice_client, cert_ab, alice_webid, bob_webid)

    # Verify the path ends with .acl
    assert acl_path.endswith(".acl")

    # Read back the ACL content
    acl_content = alice_client.get(acl_path)
    acl_text = acl_content.decode("utf-8")

    # Assert the ACL contains expected Turtle predicates and WebIDs
    assert "#owner" in acl_text or "owner" in acl_text
    assert "#subject" in acl_text or "subject" in acl_text
    assert alice_webid in acl_text
    assert bob_webid in acl_text
