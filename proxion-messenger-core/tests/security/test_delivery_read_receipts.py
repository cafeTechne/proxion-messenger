"""R17: Delivery and read receipt persistence."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "receipts.db"))


def test_ack_delivered_emits_msg_delivered_to_sender(store):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.save_receipt("msg-001", "did:key:bob", delivered_at=ts)
    receipts = store.get_receipts("msg-001")
    assert len(receipts) == 1
    assert receipts[0]["receiver_webid"] == "did:key:bob"
    assert receipts[0]["delivered_at"] == ts


def test_ack_read_emits_msg_read_to_sender(store):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.save_receipt("msg-002", "did:key:carol", read_at=ts)
    receipts = store.get_receipts("msg-002")
    assert len(receipts) == 1
    assert receipts[0]["read_at"] == ts


def test_receipts_persist_per_message_and_receiver(store):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.save_receipt("msg-003", "did:key:bob", delivered_at=ts)
    store.save_receipt("msg-003", "did:key:carol", delivered_at=ts)
    receipts = store.get_receipts("msg-003")
    assert len(receipts) == 2
    receivers = {r["receiver_webid"] for r in receipts}
    assert "did:key:bob" in receivers
    assert "did:key:carol" in receivers
