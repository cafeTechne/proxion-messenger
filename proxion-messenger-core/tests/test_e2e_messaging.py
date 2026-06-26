"""Task 19 — End-to-end messaging lifecycle integration tests.

Each test is self-contained and exercises a complete flow using real
protocol objects (no mocks except the Pod client).
"""

from __future__ import annotations

import datetime
import json
import os
from unittest.mock import MagicMock

import pytest

from proxion_messenger_core import (
    AgentState,
    RevocationList,
    MemoryStore,
    run_bidirectional_handshake,
    run_local_handshake,
)
from proxion_messenger_core.certtoken import (
    issue_from_certificate,
    revoke_cert_and_tokens,
)
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.messaging import (
    compose,
    compose_and_send,
    receive,
    send,
    thread_path,
)
from proxion_messenger_core.solid_client import SolidClient, SolidError


# ---------------------------------------------------------------------------
# Pod client helper (duplicated from test_messaging.py for isolation)
# ---------------------------------------------------------------------------

def _mock_pod(stored=None):
    """In-memory Pod client backed by a plain dict."""
    from proxion_messenger_core.solid import SolidResolver
    storage = {} if stored is None else stored

    def _to_http(uri):
        without_scheme = uri[len("stash://"):]
        slash = without_scheme.find("/")
        if slash == -1:
            return "http://pod/"
        path = without_scheme[slash + 1:]
        return f"http://pod/{path}" if path else "http://pod/"

    def _to_stash(url):
        if url.startswith("http://pod/"):
            return f"stash://pod/{url[len('http://pod/'):]}"
        return url

    resolver = MagicMock(spec=SolidResolver)
    resolver.resolve.side_effect = _to_http

    client = MagicMock(spec=SolidClient)
    client._resolver = resolver

    client.put.side_effect = lambda path, data, content_type=None: storage.update({_to_http(path): data})
    client.get.side_effect = lambda path: (
        storage[_to_http(path)] if _to_http(path) in storage
        else (_ for _ in ()).throw(SolidError(f"not found: {path}", status_code=404))
    )
    client.list.side_effect = lambda path: [
        _to_stash(k) for k in storage if k.startswith(_to_http(path)) and k != _to_http(path)
    ]
    client.delete.side_effect = lambda path: storage.pop(_to_http(path), None)
    return client


NOW = datetime.datetime(2026, 4, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Test 1 — bidirectional with pre-cert-ID
# ---------------------------------------------------------------------------

def test_e2e_bidirectional_with_precert_id():
    """Pre-cert-ID bidirectional handshake; Alice and Bob exchange messages."""
    import uuid
    alice = AgentState.generate()
    bob = AgentState.generate()

    cert_id_ab = str(uuid.uuid4())
    cert_id_ba = str(uuid.uuid4())
    caps_ab = [Capability(can="read", with_=f"stash://messages/thread/{cert_id_ab}/")]
    caps_ba = [Capability(can="read", with_=f"stash://messages/thread/{cert_id_ba}/")]

    store = MemoryStore()
    (cert_ab, valid_ab), (cert_ba, valid_ba) = run_bidirectional_handshake(
        alice.identity_key, alice.store_key,
        bob.identity_key, bob.store_key,
        caps_ab, caps_ba, store,
        certificate_id_a_to_b=cert_id_ab,
        certificate_id_b_to_a=cert_id_ba,
    )
    assert valid_ab and valid_ba
    assert cert_ab.certificate_id == cert_id_ab
    assert cert_ba.certificate_id == cert_id_ba

    alice_pod = _mock_pod()
    bob_pod = _mock_pod()

    send(compose(alice.identity_key, cert_ab, "hi bob", now=NOW), alice_pod)
    send(compose(bob.identity_key, cert_ba, "hi alice", now=NOW), bob_pod)

    msgs_ab = receive(cert_ab, alice_pod)
    msgs_ba = receive(cert_ba, bob_pod)

    assert len(msgs_ab) == 1 and msgs_ab[0].content == "hi bob"
    assert len(msgs_ba) == 1 and msgs_ba[0].content == "hi alice"


# ---------------------------------------------------------------------------
# Test 2 — narrow token enforcement
# ---------------------------------------------------------------------------

def test_e2e_narrow_token_enforcement():
    """receive() with holder_state enforces narrow token; wrong cert raises."""
    alice = AgentState.generate()
    bob = AgentState.generate()

    caps = [Capability(can="read", with_="stash://messages/")]
    store = MemoryStore()
    cert, valid = run_local_handshake(
        alice.identity_key, alice.store_key,
        bob.identity_key, bob.store_key,
        caps, caps, store,
    )
    assert valid

    pod = _mock_pod()
    send(compose(alice.identity_key, cert, "enforced read", now=NOW), pod)

    # Enforced mode: should succeed
    msgs = receive(cert, pod, holder_state=bob, signing_key=alice.signing_key_bytes, now=NOW)
    assert len(msgs) == 1
    assert msgs[0].content == "enforced read"

    # Wrong cert (no read permission on this thread): should raise
    write_only_caps = [Capability(can="write", with_="stash://messages/")]
    wrong_cert, _ = run_local_handshake(
        alice.identity_key, alice.store_key,
        bob.identity_key, bob.store_key,
        write_only_caps, write_only_caps, MemoryStore(),
    )
    from proxion_messenger_core.certtoken import CertTokenError
    with pytest.raises((CertTokenError, PermissionError)):
        receive(wrong_cert, pod, holder_state=bob, signing_key=alice.signing_key_bytes, now=NOW)


# ---------------------------------------------------------------------------
# Test 3 — ledger revocation
# ---------------------------------------------------------------------------

def test_e2e_ledger_revocation():
    """Issue tokens with ledger; revoke cert+tokens; all tokens denied."""
    alice = AgentState.generate()
    bob = AgentState.generate()

    caps = [Capability(can="read", with_="stash://messages/")]
    store = MemoryStore()
    cert, valid = run_local_handshake(
        alice.identity_key, alice.store_key,
        bob.identity_key, bob.store_key,
        caps, caps, store,
    )
    assert valid

    sk = os.urandom(32)
    ledger = MemoryStore()

    tokens = [
        issue_from_certificate(
            cert=cert,
            requested_permissions=[("read", "stash://messages/")],
            holder_pub_key=bob.identity_key.public_key(),
            signing_key=sk,
            now=NOW,
            store=ledger,
        )
        for _ in range(3)
    ]

    rl = RevocationList()
    cert_rev_id, tokens_revoked = revoke_cert_and_tokens(cert, rl, store=ledger)
    assert tokens_revoked == 3

    for tok in tokens:
        assert rl.is_revoked(tok, NOW)


# ---------------------------------------------------------------------------
# Test 4 — since + limit pagination
# ---------------------------------------------------------------------------

def test_e2e_since_and_limit_pagination():
    """Send 10 messages; retrieve with since + limit to confirm correct slice."""
    alice = AgentState.generate()
    bob = AgentState.generate()

    caps = [Capability(can="read", with_="stash://messages/")]
    cert, _ = run_local_handshake(
        alice.identity_key, alice.store_key,
        bob.identity_key, bob.store_key,
        caps, caps, MemoryStore(),
    )

    pod = _mock_pod()
    base = NOW
    for i in range(10):
        ts = base + datetime.timedelta(seconds=i)
        send(compose(alice.identity_key, cert, f"msg {i}", now=ts), pod)

    # All 10
    all_msgs = receive(cert, pod)
    assert len(all_msgs) == 10

    # since=t5 → msgs 5..9 (5 messages)
    t5 = int((base + datetime.timedelta(seconds=5)).timestamp())
    since_msgs = receive(cert, pod, since=t5)
    assert len(since_msgs) == 5
    assert since_msgs[0].content == "msg 5"

    # since=t5, limit=2 → msgs 5 and 6
    limited = receive(cert, pod, since=t5, limit=2)
    assert len(limited) == 2
    assert limited[0].content == "msg 5"
    assert limited[1].content == "msg 6"


# ---------------------------------------------------------------------------
# Test 5 — reply thread
# ---------------------------------------------------------------------------

def test_e2e_reply_thread():
    """compose_and_send with in_reply_to; confirmed in received messages."""
    alice = AgentState.generate()
    bob = AgentState.generate()

    caps = [Capability(can="read", with_="stash://messages/")]
    cert, _ = run_local_handshake(
        alice.identity_key, alice.store_key,
        bob.identity_key, bob.store_key,
        caps, caps, MemoryStore(),
    )

    pod = _mock_pod()

    root_msg = compose_and_send(
        alice.identity_key, cert, "root message", pod, now=NOW
    )
    reply_msg = compose_and_send(
        alice.identity_key, cert, "reply to root", pod, now=NOW,
        reply_to_id=root_msg.message_id,
    )

    msgs = receive(cert, pod)
    assert len(msgs) == 2
    root = next(m for m in msgs if m.message_id == root_msg.message_id)
    reply = next(m for m in msgs if m.message_id == reply_msg.message_id)
    assert root.reply_to_id is None
    assert reply.reply_to_id == root_msg.message_id
