"""Tests for receipt writer hook integration and pod receipt writer helper."""

import datetime
import json
import os
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core import issue_token, sign_challenge, validate_request
from proxion_messenger_core.context import Caveat, RequestContext


def ip_allowlist(allowed: set) -> Caveat:
    return Caveat(id=f"ip:{','.join(sorted(allowed))}", predicate=lambda ctx: ctx.ip in allowed)
from proxion_messenger_core.messaging import make_pod_receipt_writer
from proxion_messenger_core.pop import fingerprint_from_key
from proxion_messenger_core.validator import Decision


def _make_allow_env():
    holder = Ed25519PrivateKey.generate()
    sk = os.urandom(32)
    now = datetime.datetime.now(datetime.timezone.utc)
    token = issue_token(
        permissions=[("read", "stash://messages/thread/t1/")],
        exp=now + datetime.timedelta(minutes=10),
        aud="svc",
        caveats=[],
        holder_key_fingerprint=fingerprint_from_key(holder.public_key()),
        signing_key=sk,
        now=now,
    )
    ctx = RequestContext(
        action="read",
        resource="stash://messages/thread/t1/file.json",
        aud="svc",
        now=now,
        device_nonce="n-1",
    )
    proof = sign_challenge(holder, token.token_id, "n-1")
    return token, ctx, proof, sk


def test_receipt_writer_called_on_allow():
    token, ctx, proof, sk = _make_allow_env()
    seen = []

    def writer(t, c, d):
        seen.append((t, c, d))

    decision = validate_request(token, ctx, proof, sk, receipt_writer=writer)
    assert decision.allowed is True
    assert len(seen) == 1
    assert seen[0][0].token_id == token.token_id
    assert seen[0][1].resource == ctx.resource
    assert seen[0][2].allowed is True


def test_receipt_writer_called_on_deny():
    holder = Ed25519PrivateKey.generate()
    sk = os.urandom(32)
    now = datetime.datetime.now(datetime.timezone.utc)
    token = issue_token(
        permissions=[("read", "stash://messages/")],
        exp=now + datetime.timedelta(minutes=10),
        aud="svc",
        caveats=[ip_allowlist({"10.0.0.1"})],
        holder_key_fingerprint=fingerprint_from_key(holder.public_key()),
        signing_key=sk,
        now=now,
    )
    ctx = RequestContext(
        action="read",
        resource="stash://messages/thread/t1/file.json",
        aud="svc",
        now=now,
        device_nonce="n-2",
        ip="10.0.0.2",
    )
    proof = sign_challenge(holder, token.token_id, "n-2")
    seen = []

    def writer(t, c, d):
        seen.append(d)

    decision = validate_request(token, ctx, proof, sk, receipt_writer=writer)
    assert decision.allowed is False
    assert decision.reason == "caveat_failed"
    assert len(seen) == 1
    assert seen[0].allowed is False


def test_receipt_writer_exception_does_not_deny():
    token, ctx, proof, sk = _make_allow_env()

    def writer(_t, _c, _d):
        raise RuntimeError("boom")

    decision = validate_request(token, ctx, proof, sk, receipt_writer=writer)
    assert decision.allowed is True


def test_receipt_writer_none_is_noop():
    token, ctx, proof, sk = _make_allow_env()
    decision = validate_request(token, ctx, proof, sk)
    assert decision.allowed is True


def test_make_pod_receipt_writer_writes_on_allow():
    pod_client = MagicMock()
    token, ctx, _, _ = _make_allow_env()
    writer = make_pod_receipt_writer(pod_client, "agentpubhex")
    writer(token, ctx, Decision(True, None))

    pod_client.put.assert_called_once()
    call_args = pod_client.put.call_args
    path = call_args[0][0]
    body = call_args[0][1]
    content_type = call_args[1]["content_type"]
    assert path.startswith("stash://receipts/")
    payload = json.loads(body.decode("utf-8"))
    assert payload["token_id"] == token.token_id
    assert payload["agent"] == "agentpubhex"
    assert payload["allowed"] is True
    assert content_type == "application/ld+json"


def test_make_pod_receipt_writer_silent_on_deny():
    pod_client = MagicMock()
    token, ctx, _, _ = _make_allow_env()
    writer = make_pod_receipt_writer(pod_client, "agentpubhex")
    writer(token, ctx, Decision(False, "permission_missing"))
    pod_client.put.assert_not_called()
