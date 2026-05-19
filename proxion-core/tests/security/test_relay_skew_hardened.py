"""Tests for strict timestamp validation in verify_relay_message() (R17)."""
import pytest
from datetime import datetime, timezone, timedelta

from proxion_messenger_core.relay import verify_relay_message, sign_relay_message
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from proxion_messenger_core.didkey import pub_key_to_did


def _make_identity():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    did = pub_key_to_did(pub)
    return priv, did


def _sign(priv, did, to_did="did:key:z6MkTarget", ts=None, nonce=""):
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    sig = sign_relay_message(
        priv, did, to_did, "msg-001", "hello", ts, relay_nonce=nonce
    )
    return sig, ts


class TestRelayTimestampHardened:
    def test_valid_timestamp_accepts(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        sig, ts = _sign(priv, did, ts=ts)
        assert verify_relay_message(
            did, "did:key:z6MkTarget", "msg-001", "hello", ts, sig
        ) is True

    def test_malformed_timestamp_rejected(self):
        priv, did = _make_identity()
        ts = "not-a-date"
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m1", "hello", ts)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m1", "hello", ts, sig
        ) is False

    def test_empty_timestamp_rejected(self):
        priv, did = _make_identity()
        ts = ""
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m1", "hello", ts)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m1", "hello", ts, sig
        ) is False

    def test_integer_timestamp_rejected(self):
        priv, did = _make_identity()
        ts = "1234567890"  # Unix epoch integer as string — not ISO 8601
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m1", "hello", ts)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m1", "hello", ts, sig
        ) is False

    def test_future_timestamp_beyond_window_rejected(self):
        priv, did = _make_identity()
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m2", "hello", future)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m2", "hello", future, sig
        ) is False

    def test_past_timestamp_beyond_window_rejected(self):
        priv, did = _make_identity()
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m3", "hello", past)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m3", "hello", past, sig
        ) is False

    def test_timestamp_within_window_accepted(self):
        priv, did = _make_identity()
        ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m4", "hello", ts)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m4", "hello", ts, sig
        ) is True

    def test_z_suffix_timestamp_accepted(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m5", "hello", ts)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m5", "hello", ts, sig
        ) is True

    def test_timedelta_max_still_fails_on_malformed(self):
        """Even with clock_skew_window=max, malformed timestamps must fail closed."""
        priv, did = _make_identity()
        ts = "garbage-date"
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m6", "hello", ts)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m6", "hello", ts, sig,
            clock_skew_window=timedelta.max,
        ) is False

    def test_timedelta_max_capped_at_15_minutes(self):
        """timedelta.max is capped to ±15 min; arbitrarily old timestamps are still rejected."""
        priv, did = _make_identity()
        ts = "2020-01-01T00:00:00+00:00"
        sig = sign_relay_message(priv, did, "did:key:z6MkT", "m7", "hello", ts)
        assert verify_relay_message(
            did, "did:key:z6MkT", "m7", "hello", ts, sig,
            clock_skew_window=timedelta.max,
        ) is False
