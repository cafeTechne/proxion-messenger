import pytest
import uuid
import time
from datetime import datetime

from proxion_messenger_core import (
    AgentState, MemoryStore,
)
from proxion_messenger_core.nss_setup import make_pod_client
from proxion_messenger_core.room import (
    create_room, invite_to_room, join_room,
    send_to_room, read_room, set_room_acl
)
from proxion_messenger_core.room_store import RoomStore
from proxion_messenger_core.solid_client import SolidError

@pytest.mark.integration
def test_css_room_lifecycle(css_alice_url, css_bob_url, alice_agent, bob_agent):
    """Test full room lifecycle: create, invite, join, send, read with pagination."""
    # 1. Setup Alice
    alice_creds, alice_pod_url, alice_webid, alice_client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )
    alice_client.identity_key = alice_agent.identity_key # Needed for signing

    # 2. Setup Bob
    bob_creds, bob_pod_url, bob_webid, bob_client = make_pod_client(
        css_bob_url,
        bob_agent.identity_key,
        f"bob-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )
    bob_client.identity_key = bob_agent.identity_key

    # 3. Alice creates a room
    room = create_room(alice_client, alice_webid, "Secret Garden")
    assert room.name == "Secret Garden"

    # 4. Alice generates invite
    # In real scenario, Alice's handshake mailbox is her Pod store.
    # For this test, we'll use a shared MemoryStore to simulate the coordination.
    shared_store = MemoryStore()
    invite_json = invite_to_room(room, alice_agent)

    # 5. Alice sets ACL for Bob (pre-emptive or after join)
    # The room lifecycle in Proxion usually sets ACL for known members.
    set_room_acl(room, alice_client, alice_webid, [bob_webid])

    # 6. Bob joins
    # First, Alice's agent must "be online" to accept Bob's accept-invite request?
    # No, Bob posts to Alice's Pod, then Alice polls and issues cert.
    # In this integration test, we simulate the handshake steps.

    # Actually, join_room handles the Bob side of pushing to Alice's store.
    # We need to make sure join_room uses the correct remote store (Alice's).
    from proxion_messenger_core.store_client import LocalStoreAdapter
    alice_remote = LocalStoreAdapter(shared_store) # Simulate remote access to Alice's store

    membership = join_room(invite_json, bob_agent, bob_webid, alice_remote)

    # 7. Alice processes join request (polls her store)
    from proxion_messenger_core.handshake import process_join_requests
    results = process_join_requests(alice_agent.identity_key, alice_agent.store_key, alice_remote)
    assert results
    cert, valid = results[0]
    assert valid

    # 8. Bob finishes join (already done by poll in join_room OR we manually update membership)
    # In join_room, it already calls receive_certificates.
    # Since Alice just finalized, Bob should have the cert in his next poll if we retry.
    if not membership.cert:
        from proxion_messenger_core.handshake import receive_certificates
        certs = receive_certificates(bob_agent.store_key, alice_remote)
        assert certs
        membership.cert, _ = certs[0]

    # 9. Messaging: Alice sends 3 messages
    send_to_room(alice_client, room, "Message 1")
    time.sleep(1)
    send_to_room(alice_client, room, "Message 2")
    time.sleep(1)
    send_to_room(alice_client, room, "Message 3")

    # 10. Bob reads with limit
    msgs = read_room(membership, bob_client, bob_agent, limit=2)
    assert len(msgs) == 2
    assert msgs[0].content == "Message 2"
    assert msgs[1].content == "Message 3"

    # 11. Bob reads with before
    before_id = msgs[1].message_id # Message 3
    msgs_before = read_room(membership, bob_client, bob_agent, before=before_id)
    # Should exclude Message 3 and newer. So should have Message 1 and 2.
    assert len(msgs_before) == 2
    assert msgs_before[0].content == "Message 1"
    assert msgs_before[1].content == "Message 2"
