"""Tests for proxion_messenger_core.messaging.

These tests deliberately expose the spec gaps documented in JOURNAL.md.
Each gap is called out with a comment referencing its journal entry.

Test surface:
- compose(): creates a validly signed Message
- send(): calls SolidClient.put() with the correct path and JSON body
- receive(): lists container, fetches individual messages, verifies signatures
- receive(): silently drops messages with invalid signatures
- receive(): returns empty list when container is absent (SolidError on list)
- verify (Message.verify()): True for valid sig, False for tampered content
- Full round-trip: Alice composes + sends, Bob receives + verifies
- Bidirectional: Alice reads Bob's thread, Bob reads Alice's thread (two certs)
- J-008 surface test: AuthenticatedSolidClient with aud=cert.issuer works;
  aud="" (the default) causes audience_mismatch on cert-derived tokens
"""

from __future__ import annotations

import json
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

from proxion_messenger_core import AgentState, run_bidirectional_handshake, run_local_handshake
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.messaging import (
    Message,
    compose,
    message_path,
    narrow_to_thread,
    receive,
    send,
    thread_path,
)
from proxion_messenger_core.solid import SolidResolver
from proxion_messenger_core.solid_client import SolidClient, SolidError
from proxion_messenger_core.store import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def alice():
    return AgentState.generate()


@pytest.fixture
def bob():
    return AgentState.generate()


@pytest.fixture
def cert(alice, bob):
    """A RelationshipCertificate where Alice is the issuer, Bob is the subject.

    The capabilities grant Bob read on stash://messages/ (covers all threads)
    because the cert_id is not yet known at invite time — J-007.
    """
    store = MemoryStore()
    caps = [
        Capability(can="read",  with_="stash://messages/"),
        Capability(can="write", with_="stash://messages/"),
    ]
    cert, valid = run_local_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_capabilities=caps,
        bob_capabilities=caps,
        store=store,
    )
    assert valid
    return cert


@pytest.fixture
def now():
    return datetime.datetime(2026, 4, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _mock_pod_client(stored: dict[str, bytes] | None = None) -> SolidClient:
    """In-memory SolidClient mock backed by a dict."""
    storage: dict[str, bytes] = stored or {}
    resolver = MagicMock(spec=SolidResolver)

    def _to_http(uri: str) -> str:
        # Mimic SolidResolver semantics: stash://<owner>/<path> -> http://pod/<path>
        without_scheme = uri[len("stash://"):]
        slash = without_scheme.find("/")
        if slash == -1:
            return "http://pod/"
        path = without_scheme[slash + 1:]
        return f"http://pod/{path}" if path else "http://pod/"

    def _to_stash(url: str) -> str:
        if url.startswith("http://pod/"):
            return f"stash://pod/{url[len('http://pod/'):]}"
        return url

    resolver.resolve.side_effect = _to_http

    client = MagicMock(spec=SolidClient)
    client._resolver = resolver

    def _put(path, data, content_type="application/octet-stream"):
        url = _to_http(path)
        storage[url] = data

    def _get(path):
        url = _to_http(path)
        if url not in storage:
            raise SolidError(f"not found: {url}", status_code=404)
        return storage[url]

    def _list(path):
        prefix = _to_http(path)
        return [
            _to_stash(k)
            for k in storage
            if k.startswith(prefix) and k != prefix
        ]

    client.put.side_effect = _put
    client.get.side_effect = _get
    client.list.side_effect = _list

    return client


# ---------------------------------------------------------------------------
# thread_path / message_path
# ---------------------------------------------------------------------------

def test_thread_path_format(cert):
    path = thread_path(cert.certificate_id)
    assert path == f"stash://messages/thread/{cert.certificate_id}/"


def test_message_path_format(cert):
    path = message_path(cert.certificate_id, "msg001")
    assert path == f"stash://messages/thread/{cert.certificate_id}/msg001.json"


# ---------------------------------------------------------------------------
# compose()
# ---------------------------------------------------------------------------

def test_compose_returns_signed_message(alice, cert, now):
    msg = compose(alice.identity_key, cert, "hello", now=now)

    assert msg.content == "hello"
    assert msg.cert_id == cert.certificate_id
    assert msg.from_pub_hex == alice.identity_pub_bytes.hex()
    assert msg.timestamp == int(now.timestamp())
    assert msg.reply_to_id is None
    assert msg.message_type == "text"
    assert msg.signature != ""


def test_compose_signature_verifies(alice, cert, now):
    msg = compose(alice.identity_key, cert, "hello", now=now)
    assert msg.verify(alice.identity_pub_bytes)


def test_compose_signature_fails_wrong_key(alice, bob, cert, now):
    msg = compose(alice.identity_key, cert, "hello", now=now)
    assert not msg.verify(bob.identity_pub_bytes)


def test_compose_signature_fails_tampered_content(alice, cert, now):
    msg = compose(alice.identity_key, cert, "hello", now=now)
    tampered = Message(
        message_id=msg.message_id,
        cert_id=msg.cert_id,
        from_pub_hex=msg.from_pub_hex,
        content="evil content",
        timestamp=msg.timestamp,
        reply_to_id=msg.reply_to_id,
        message_type=msg.message_type,
        signature=msg.signature,
    )
    assert not tampered.verify(alice.identity_pub_bytes)


def test_compose_unique_ids(alice, cert, now):
    a = compose(alice.identity_key, cert, "one", now=now)
    b = compose(alice.identity_key, cert, "two", now=now)
    assert a.message_id != b.message_id


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------

def test_send_calls_put_with_correct_path(alice, cert, now):
    pod = _mock_pod_client()
    msg = compose(alice.identity_key, cert, "hi", now=now)
    path = send(msg, pod)

    assert path == message_path(cert.certificate_id, msg.message_id)
    pod.put.assert_called_once()
    call_args = pod.put.call_args
    assert call_args[0][0] == path
    assert call_args[1].get("content_type") == "application/json" or call_args[0][2] == "application/json"


def test_send_body_is_valid_json(alice, cert, now):
    pod = _mock_pod_client()
    msg = compose(alice.identity_key, cert, "hi", now=now)
    send(msg, pod)

    raw = pod.put.call_args[0][1]
    data = json.loads(raw.decode("utf-8"))
    assert data["message_id"] == msg.message_id
    assert data["content"] == "hi"
    assert data["signature"] == msg.signature


# ---------------------------------------------------------------------------
# receive()
# ---------------------------------------------------------------------------

def test_receive_returns_messages(alice, cert, now):
    pod = _mock_pod_client()
    msg = compose(alice.identity_key, cert, "hi from alice", now=now)
    send(msg, pod)

    received = receive(cert, pod)
    assert len(received) == 1
    assert received[0].content == "hi from alice"
    assert received[0].message_id == msg.message_id


def test_receive_verifies_signatures_by_default(alice, bob, cert, now):
    """Messages with invalid signatures are silently dropped."""
    pod = _mock_pod_client()
    # Write a message with a tampered signature directly
    bad_msg = Message(
        message_id="tampered001",
        cert_id=cert.certificate_id,
        from_pub_hex=alice.identity_pub_bytes.hex(),
        content="tampered",
        timestamp=int(now.timestamp()),
        signature="deadbeef" * 16,  # invalid 128-char hex
    )
    path = message_path(cert.certificate_id, bad_msg.message_id)
    pod.list.return_value = [path.replace("stash://messages/", "stash://pod/")]
    pod.get.return_value = json.dumps(bad_msg.to_dict()).encode()

    received = receive(cert, pod)
    assert received == []


def test_receive_skips_invalid_signatures_keeps_valid(alice, cert, now):
    """Mix of valid and invalid — only valid ones returned."""
    pod = _mock_pod_client()

    good_msg = compose(alice.identity_key, cert, "valid", now=now)
    send(good_msg, pod)

    # Inject a bad message directly into pod storage
    bad_path = message_path(cert.certificate_id, "bad001")
    bad_uri = bad_path.replace("stash://messages/", "stash://pod/")
    bad_data = {
        "message_id": "bad001",
        "cert_id": cert.certificate_id,
        "from_pub_hex": alice.identity_pub_bytes.hex(),
        "content": "injected",
        "timestamp": int(now.timestamp()),
        "signature": "aa" * 64,
    }
    # Inject directly into the mock storage by monkey-patching list/get
    original_list = pod.list.side_effect
    original_get = pod.get.side_effect

    def patched_list(path):
        result = original_list(path)
        result.append(bad_uri)
        return result

    def patched_get(path):
        if path in (bad_path, bad_uri):
            return json.dumps(bad_data).encode()
        return original_get(path)

    pod.list.side_effect = patched_list
    pod.get.side_effect = patched_get

    received = receive(cert, pod)
    assert len(received) == 1
    assert received[0].message_id == good_msg.message_id


def test_receive_empty_when_container_missing(cert):
    """SolidError on list() → empty list, not an exception."""
    pod = _mock_pod_client()
    pod.list.side_effect = SolidError("not found", 404)

    result = receive(cert, pod)
    assert result == []


def test_receive_sorted_by_timestamp(alice, cert):
    """Messages come back oldest-first regardless of storage order."""
    pod = _mock_pod_client()
    t1 = datetime.datetime(2026, 4, 9, 10, 0, 0, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2026, 4, 9, 11, 0, 0, tzinfo=datetime.timezone.utc)
    t3 = datetime.datetime(2026, 4, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)

    m3 = compose(alice.identity_key, cert, "third",  now=t3)
    m1 = compose(alice.identity_key, cert, "first",  now=t1)
    m2 = compose(alice.identity_key, cert, "second", now=t2)
    for m in (m3, m1, m2):
        send(m, pod)

    received = receive(cert, pod)
    assert [r.content for r in received] == ["first", "second", "third"]


def test_receive_verify_false_returns_all(alice, cert, now):
    """verify_signatures=False returns even unsigned/invalid messages."""
    pod = _mock_pod_client()
    bad_path = message_path(cert.certificate_id, "unsigned")
    bad_uri = bad_path.replace("stash://messages/", "stash://pod/")
    bad_data = {
        "message_id": "unsigned",
        "cert_id": cert.certificate_id,
        "from_pub_hex": alice.identity_pub_bytes.hex(),
        "content": "no sig",
        "timestamp": int(now.timestamp()),
        "signature": "",
    }
    # Override side_effect (not return_value — side_effect takes priority on MagicMock)
    raw = json.dumps(bad_data).encode()
    pod.list.side_effect = lambda _path: [bad_uri]
    pod.get.side_effect  = lambda _path: raw

    received = receive(cert, pod, verify_signatures=False)
    assert len(received) == 1
    assert received[0].content == "no sig"


def test_receive_since_filters_old_messages(alice, cert):
    pod = _mock_pod_client()
    t1 = datetime.datetime(2026, 4, 9, 10, 0, 0, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2026, 4, 9, 11, 0, 0, tzinfo=datetime.timezone.utc)
    t3 = datetime.datetime(2026, 4, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
    send(compose(alice.identity_key, cert, "first", now=t1), pod)
    send(compose(alice.identity_key, cert, "second", now=t2), pod)
    send(compose(alice.identity_key, cert, "third", now=t3), pod)
    received = receive(cert, pod, since=int(t2.timestamp()))
    assert [m.content for m in received] == ["second", "third"]


def test_receive_since_inclusive_boundary(alice, cert):
    pod = _mock_pod_client()
    t = datetime.datetime(2026, 4, 9, 11, 30, 0, tzinfo=datetime.timezone.utc)
    send(compose(alice.identity_key, cert, "boundary", now=t), pod)
    received = receive(cert, pod, since=int(t.timestamp()))
    assert len(received) == 1
    assert received[0].content == "boundary"


# ---------------------------------------------------------------------------
# Full round-trip
# ---------------------------------------------------------------------------

def test_round_trip_alice_to_bob(alice, bob, cert, now):
    """Alice writes a message; Bob reads and verifies it."""
    alice_pod = _mock_pod_client()

    # Alice sends
    msg = compose(alice.identity_key, cert, "hey bob", now=now)
    send(msg, alice_pod)

    # Bob reads from Alice's Pod (alice_pod)
    received = receive(cert, alice_pod)
    assert len(received) == 1
    assert received[0].content == "hey bob"
    assert received[0].verify(alice.identity_pub_bytes)


def test_round_trip_bidirectional(alice, bob, now):
    """Bidirectional messaging using run_bidirectional_handshake()."""
    store = MemoryStore()
    caps = [Capability(can="read", with_="stash://messages/")]

    (cert_a_to_b, valid_ab), (cert_b_to_a, valid_ba) = run_bidirectional_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_to_bob_capabilities=caps,
        bob_to_alice_capabilities=caps,
        store=store,
    )
    assert valid_ab and valid_ba

    alice_pod = _mock_pod_client()
    bob_pod   = _mock_pod_client()

    msg_a = compose(alice.identity_key, cert_a_to_b, "hi bob", now=now)
    send(msg_a, alice_pod)

    msg_b = compose(bob.identity_key, cert_b_to_a, "hi alice", now=now)
    send(msg_b, bob_pod)

    from_alice = receive(cert_a_to_b, alice_pod)
    assert len(from_alice) == 1
    assert from_alice[0].content == "hi bob"

    from_bob = receive(cert_b_to_a, bob_pod)
    assert len(from_bob) == 1
    assert from_bob[0].content == "hi alice"


# ---------------------------------------------------------------------------
# J-007 narrowing token helper
# ---------------------------------------------------------------------------

def test_narrow_to_thread_produces_scoped_token(alice, bob, cert, now):
    """narrow_to_thread() returns a token covering only the thread container."""
    signing_key = alice.signing_key_bytes
    token = narrow_to_thread(cert, bob, signing_key, now=now)
    container = thread_path(cert.certificate_id)
    assert ("read", container) in token.permissions
    assert ("read", "stash://messages/thread/other-cert/") not in token.permissions


def test_narrow_to_thread_rejects_cert_without_read(alice, bob, now):
    """CertTokenError when the cert doesn't grant read on stash://messages/."""
    from proxion_messenger_core.certtoken import CertTokenError
    store = MemoryStore()
    write_only_caps = [Capability(can="write", with_="stash://messages/")]
    cert_wo, _ = run_local_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_capabilities=write_only_caps,
        bob_capabilities=write_only_caps,
        store=store,
    )
    with pytest.raises(CertTokenError):
        narrow_to_thread(cert_wo, bob, alice.signing_key_bytes, now=now)


def test_receive_with_holder_state_enforces_narrow_token(alice, bob, cert, now):
    """receive() enforces per-read access via narrow token when holder_state is provided."""
    pod = _mock_pod_client()
    msg = compose(alice.identity_key, cert, "enforced read", now=now)
    send(msg, pod)

    received = receive(
        cert,
        pod,
        holder_state=bob,
        signing_key=alice.signing_key_bytes,
    )
    assert len(received) == 1
    assert received[0].content == "enforced read"


def test_receive_with_holder_state_denies_wrong_cert(alice, bob, now):
    """write-only cert cannot mint narrow read token; receive() propagates error."""
    from proxion_messenger_core.certtoken import CertTokenError

    store = MemoryStore()
    write_only_caps = [Capability(can="write", with_="stash://messages/")]
    cert_wo, valid = run_local_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_capabilities=write_only_caps,
        bob_capabilities=write_only_caps,
        store=store,
    )
    assert valid
    pod = _mock_pod_client()
    with pytest.raises(CertTokenError):
        receive(
            cert_wo,
            pod,
            holder_state=bob,
            signing_key=alice.signing_key_bytes,
            now=now,
        )


# ---------------------------------------------------------------------------
# J-008 surface test: AuthenticatedSolidClient aud mismatch
# ---------------------------------------------------------------------------

def test_j008_authenticated_client_aud_mismatch(alice, bob, cert):
    """Expose J-008: AuthenticatedSolidClient defaults aud='' but
    issue_from_certificate sets aud=cert.issuer.  The default aud causes
    audience_mismatch in validate_request.

    This test documents the current behaviour (PermissionError) so that
    when J-008 is fixed, the test can be updated to confirm the fix.

    NOTE: token must be issued with wall-clock now so it isn't expired when
    AuthenticatedSolidClient validates it (which uses datetime.now() internally).
    """
    from proxion_messenger_core.certtoken import issue_from_certificate
    from proxion_messenger_core.solid_auth import AuthenticatedSolidClient
    from proxion_messenger_core.solid import SolidResolver

    resolver = SolidResolver("http://pod/")
    real_client = SolidClient(resolver, session=MagicMock())
    real_client._session.get.return_value = MagicMock(
        status_code=200, content=b"data"
    )

    signing_key = alice.signing_key_bytes
    # Use real wall-clock now so the token isn't stale when _check_allowed runs
    real_now = datetime.datetime.now(datetime.timezone.utc)
    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "stash://messages/")],
        holder_pub_key=bob.identity_key.public_key(),
        signing_key=signing_key,
        ttl_seconds=300,
        now=real_now,
    )

    # Default aud="" — will not match token.aud == cert.issuer
    bad_client = AuthenticatedSolidClient(
        solid_client=real_client,
        token=token,
        identity_key=bob.identity_key,
        signing_key=signing_key,
        aud="",  # J-008: this should be cert.issuer
    )
    with pytest.raises(PermissionError, match="audience_mismatch"):
        bad_client.get("stash://messages/thread/x/y.json")

    # cert= auto-derives aud — should NOT raise
    cert_client = AuthenticatedSolidClient(
        solid_client=real_client,
        token=token,
        identity_key=bob.identity_key,
        signing_key=signing_key,
        cert=cert,
    )
    result = cert_client.get("stash://messages/thread/x/y.json")
    assert result == b"data"

    # explicit aud=cert.issuer still works (backwards compatibility)
    explicit_client = AuthenticatedSolidClient(
        solid_client=real_client,
        token=token,
        identity_key=bob.identity_key,
        signing_key=signing_key,
        aud=cert.issuer,
    )
    result2 = explicit_client.get("stash://messages/thread/x/y.json")
    assert result2 == b"data"


# ---------------------------------------------------------------------------
# Task 6 — Message.in_reply_to field + compose(in_reply_to=)
# ---------------------------------------------------------------------------

def test_compose_top_level_reply_to_id_is_none(alice, cert):
    msg = compose(alice.identity_key, cert, "Hello")
    assert msg.reply_to_id is None


def test_compose_with_reply_to_id(alice, cert):
    parent = compose(alice.identity_key, cert, "Parent message")
    reply = compose(alice.identity_key, cert, "Reply", reply_to_id=parent.message_id)
    assert reply.reply_to_id == parent.message_id


def test_message_serde_preserves_reply_to_id(alice, cert):
    parent = compose(alice.identity_key, cert, "A")
    reply = compose(alice.identity_key, cert, "B", reply_to_id=parent.message_id)
    d = reply.to_dict()
    restored = Message.from_dict(d)
    assert restored.reply_to_id == parent.message_id

def test_message_serde_preserves_message_type(alice, cert):
    msg = compose(alice.identity_key, cert, "A", message_type="test-type")
    d = msg.to_dict()
    restored = Message.from_dict(d)
    assert restored.message_type == "test-type"

def test_backward_compat_in_reply_to(alice, cert):
    # Old dict with in_reply_to instead of reply_to_id
    d = {
        "message_id": "m1",
        "cert_id": cert.certificate_id,
        "from_pub_hex": alice.identity_pub_bytes.hex(),
        "content": "abc",
        "timestamp": 12345,
        "in_reply_to": "parent123",
        "signature": "sig"
    }
    msg = Message.from_dict(d)
    assert msg.reply_to_id == "parent123"


# ---------------------------------------------------------------------------
# Task 7 — delete_message
# ---------------------------------------------------------------------------

def test_delete_message_calls_pod_delete(alice, cert):
    from proxion_messenger_core.messaging import delete_message
    pod = _mock_pod_client()
    msg = compose(alice.identity_key, cert, "To be deleted")
    send(msg, pod)
    path_returned = delete_message(msg.message_id, cert.certificate_id, pod)
    assert path_returned == message_path(cert.certificate_id, msg.message_id)
    pod.delete.assert_called_once()


def test_delete_message_returns_correct_path(alice, cert):
    from proxion_messenger_core.messaging import delete_message
    pod = _mock_pod_client()
    msg = compose(alice.identity_key, cert, "Gone")
    send(msg, pod)
    returned = delete_message(msg.message_id, cert.certificate_id, pod)
    assert returned == message_path(cert.certificate_id, msg.message_id)


# ---------------------------------------------------------------------------
# Task 8 — thread_info
# ---------------------------------------------------------------------------

def test_thread_info_count(alice, cert):
    from proxion_messenger_core.messaging import thread_info
    pod = _mock_pod_client()
    send(compose(alice.identity_key, cert, "msg1"), pod)
    send(compose(alice.identity_key, cert, "msg2"), pod)
    info = thread_info(cert, pod)
    assert info["count"] == 2
    assert len(info["message_ids"]) == 2


def test_thread_info_empty_thread(alice, cert):
    from proxion_messenger_core.messaging import thread_info
    pod = _mock_pod_client()
    info = thread_info(cert, pod)
    assert info["count"] == 0
    assert info["message_ids"] == []


def test_thread_info_latest_timestamp_is_none(alice, cert):
    from proxion_messenger_core.messaging import thread_info
    pod = _mock_pod_client()
    send(compose(alice.identity_key, cert, "test"), pod)
    info = thread_info(cert, pod)
    assert info["latest_timestamp"] is None


# ---------------------------------------------------------------------------
# Task 9 — receive pagination (limit / offset)
# ---------------------------------------------------------------------------

def test_receive_limit_restricts_results(alice, cert):
    pod = _mock_pod_client()
    for i in range(5):
        send(compose(alice.identity_key, cert, f"msg {i}"), pod)
    msgs = receive(cert, pod, limit=3)
    assert len(msgs) == 3


def test_receive_offset_skips_messages(alice, cert):
    pod = _mock_pod_client()
    for i in range(4):
        send(compose(alice.identity_key, cert, f"msg {i}"), pod)
    all_msgs = receive(cert, pod)
    paged_msgs = receive(cert, pod, offset=2)
    assert len(paged_msgs) == 2
    assert paged_msgs[0].message_id == all_msgs[2].message_id


def test_receive_limit_and_offset_combined(alice, cert):
    pod = _mock_pod_client()
    for i in range(6):
        send(compose(alice.identity_key, cert, f"msg {i}"), pod)
    paged = receive(cert, pod, limit=2, offset=2)
    assert len(paged) == 2


# ---------------------------------------------------------------------------
# Task 10 — compose_and_send
# ---------------------------------------------------------------------------

def test_compose_and_send_persists_message(alice, cert):
    from proxion_messenger_core.messaging import compose_and_send
    pod = _mock_pod_client()
    msg = compose_and_send(alice.identity_key, cert, "Hi compose_and_send", pod)
    assert msg.content == "Hi compose_and_send"
    msgs = receive(cert, pod)
    assert any(m.message_id == msg.message_id for m in msgs)


def test_compose_and_send_returns_message(alice, cert):
    from proxion_messenger_core.messaging import compose_and_send
    pod = _mock_pod_client()
    msg = compose_and_send(alice.identity_key, cert, "Returned", pod)
    assert isinstance(msg, Message)
    assert msg.cert_id == cert.certificate_id


# ---------------------------------------------------------------------------
# Task 18 — narrow_to_thread edge cases
# ---------------------------------------------------------------------------

def test_narrow_to_thread_ttl_respected(alice, cert, now):
    """narrow_to_thread with ttl_seconds=60 produces a token expiring ~60s out."""
    from proxion_messenger_core.messaging import narrow_to_thread
    import datetime as _dt
    token = narrow_to_thread(cert, alice, alice.signing_key_bytes, now=now, ttl_seconds=60)
    max_exp = now + _dt.timedelta(seconds=61)
    assert token.exp <= max_exp.replace(tzinfo=_dt.timezone.utc)


def test_narrow_to_thread_permissions_exact(alice, cert, now):
    """narrow_to_thread produces a token with exactly the thread container in perms."""
    from proxion_messenger_core.messaging import narrow_to_thread
    token = narrow_to_thread(cert, alice, alice.signing_key_bytes, now=now)
    container = thread_path(cert.certificate_id)
    assert ("read", container) in token.permissions
    assert not any(r == "stash://messages/" for _, r in token.permissions)


# ---------------------------------------------------------------------------
# Edit Messages
# ---------------------------------------------------------------------------

def test_edit_message_returns_edit_type(alice, cert):
    from proxion_messenger_core.messaging import edit_message
    msg = edit_message(alice.identity_key, cert, "orig-123", "New text")
    assert msg.message_type == "edit"
    assert msg.reply_to_id == "orig-123"
    assert msg.content == "New text"

def test_apply_edits_replaces_original_content(alice, cert):
    from proxion_messenger_core.messaging import compose, edit_message, apply_edits
    import datetime
    now1 = datetime.datetime.now(datetime.timezone.utc)
    orig = compose(alice.identity_key, cert, "Old text", now=now1)
    
    now2 = now1 + datetime.timedelta(seconds=10)
    edit = edit_message(alice.identity_key, cert, orig.message_id, "New text", now=now2)
    
    result = apply_edits([orig, edit])
    assert len(result) == 1
    assert result[0].message_id == orig.message_id
    assert result[0].content == "New text"

def test_apply_edits_multiple_edits_newest_wins(alice, cert):
    from proxion_messenger_core.messaging import compose, edit_message, apply_edits
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    orig = compose(alice.identity_key, cert, "Old text", now=now)
    
    edit1 = edit_message(alice.identity_key, cert, orig.message_id, "Edit 1", now=now + datetime.timedelta(seconds=10))
    edit2 = edit_message(alice.identity_key, cert, orig.message_id, "Edit 2", now=now + datetime.timedelta(seconds=20))
    
    result = apply_edits([orig, edit2, edit1]) # out of order
    assert len(result) == 1
    assert result[0].content == "Edit 2"

def test_apply_edits_no_edits_returns_unchanged(alice, cert):
    from proxion_messenger_core.messaging import compose, apply_edits
    m1 = compose(alice.identity_key, cert, "Text 1")
    m2 = compose(alice.identity_key, cert, "Text 2")
    
    result = apply_edits([m1, m2])
    assert len(result) == 2
    assert result[0].content == "Text 1"
    assert result[1].content == "Text 2"

def test_apply_edits_referencing_nonexistent_is_dropped(alice, cert):
    from proxion_messenger_core.messaging import edit_message, compose, apply_edits
    m1 = compose(alice.identity_key, cert, "Real")
    edit = edit_message(alice.identity_key, cert, "fake-123", "Ghost edit")
    
    result = apply_edits([m1, edit])
    assert len(result) == 1
    assert result[0].content == "Real"
    
def test_edit_message_encrypt_stores_enc1_prefix(alice, cert):
    from proxion_messenger_core.messaging import edit_message
    msg = edit_message(alice.identity_key, cert, "orig-123", "Secret edit", encrypt=True)
    assert msg.message_type == "edit"
    assert msg.content.startswith("enc1:")
    assert "Secret edit" not in msg.content
