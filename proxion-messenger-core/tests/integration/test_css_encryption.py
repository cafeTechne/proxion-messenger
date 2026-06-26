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
from proxion_messenger_core.federation import Capability

@pytest.mark.integration
def test_css_room_encryption(css_alice_url, css_bob_url, alice_agent, bob_agent):
    """Test authenticated encryption in CSS/NSS rooms."""
    # 1. Setup Alice & Bob
    alice_creds, alice_pod_url, alice_webid, alice_client = make_pod_client(
        css_alice_url,
        alice_agent.identity_key,
        f"alice-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )
    alice_client.identity_key = alice_agent.identity_key

    bob_creds, bob_pod_url, bob_webid, bob_client = make_pod_client(
        css_bob_url,
        bob_agent.identity_key,
        f"bob-{uuid.uuid4().hex[:8]}@test.example",
        "password123",
        stash_owner="pod",
    )
    bob_client.identity_key = bob_agent.identity_key

    # 2. Setup Room & Handshake
    room = create_room(alice_client, alice_webid, "Encrypted Room")
    set_room_acl(room, alice_client, alice_webid, [bob_webid])

    shared_store = MemoryStore()
    from proxion_messenger_core.store_client import LocalStoreAdapter
    remote = LocalStoreAdapter(shared_store)

    invite_json = invite_to_room(room, alice_agent)
    membership = join_room(invite_json, bob_agent, bob_webid, remote)

    from proxion_messenger_core.handshake import process_join_requests, receive_certificates
    process_join_requests(alice_agent.identity_key, alice_agent.store_key, remote)

    certs = receive_certificates(bob_agent.store_key, remote)
    membership.cert, _ = certs[0]

    # 3. Alice sends one plain, one encrypted
    send_to_room(alice_client, room, "Plain Message", encrypt=False)
    send_to_room(alice_client, room, "Secret Message", encrypt=True)

    # 4. Bob reads with decrypt=True (default)
    msgs = read_room(membership, bob_client, bob_agent, decrypt=True)
    assert len(msgs) == 2
    assert msgs[0].content == "Plain Message"
    assert msgs[1].content == "Secret Message"

    # 5. Bob reads with decrypt=False
    msgs_raw = read_room(membership, bob_client, bob_agent, decrypt=False)
    assert len(msgs_raw) == 2
    assert msgs_raw[0].content == "Plain Message"
    # Encrypted message content should be binary/encrypted string (often looks like base64 or garbage if printed)
    assert msgs_raw[1].content != "Secret Message"
    assert len(msgs_raw[1].content) > len("Secret Message") # Usually longer due to poly1305 + nonce
