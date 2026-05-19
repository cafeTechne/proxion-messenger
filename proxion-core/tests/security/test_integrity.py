"""Tests for seq_num integrity: gap detection and continuity checking."""
import pytest
from proxion_messenger_core.messaging import Message, check_sequence_continuity


def _make_msg(seq: int) -> Message:
    return Message(
        message_id=f"msg-{seq}",
        cert_id="cert-abc",
        from_pub_hex="a" * 64,
        content=f"hello {seq}",
        timestamp=1_000_000 + seq,
        signature="sig",
        seq_num=seq,
    )


class TestCheckSequenceContinuity:
    def test_no_gaps_returns_empty(self):
        msgs = [_make_msg(i) for i in range(1, 6)]
        assert check_sequence_continuity(msgs) == []

    def test_single_gap(self):
        msgs = [_make_msg(i) for i in [1, 2, 4, 5]]
        assert check_sequence_continuity(msgs) == [3]

    def test_multiple_gaps(self):
        msgs = [_make_msg(i) for i in [1, 3, 6]]
        assert check_sequence_continuity(msgs) == [2, 4, 5]

    def test_legacy_zero_seq_ignored(self):
        # seq_num=0 means "no seq" — should not be included in gap analysis
        msgs = [_make_msg(0), _make_msg(0), _make_msg(0)]
        assert check_sequence_continuity(msgs) == []

    def test_mixed_legacy_and_numbered(self):
        # Legacy (seq=0) mixed with numbered — numbered gap still detected
        numbered = [_make_msg(i) for i in [1, 3]]
        legacy = [_make_msg(0), _make_msg(0)]
        assert check_sequence_continuity(numbered + legacy) == [2]

    def test_empty_list(self):
        assert check_sequence_continuity([]) == []

    def test_single_message_no_gap(self):
        assert check_sequence_continuity([_make_msg(7)]) == []

    def test_out_of_order_still_detected(self):
        # Gap detection should work regardless of ordering
        msgs = [_make_msg(i) for i in [5, 1, 3, 2]]
        assert check_sequence_continuity(msgs) == [4]

    def test_large_jump_reports_all_missing(self):
        msgs = [_make_msg(1), _make_msg(5)]
        assert check_sequence_continuity(msgs) == [2, 3, 4]

    def test_contiguous_from_non_one(self):
        # Starting from an arbitrary seq_num — no gap
        msgs = [_make_msg(i) for i in range(100, 105)]
        assert check_sequence_continuity(msgs) == []


class TestMessageSeqNumField:
    def test_seq_num_default_zero(self):
        msg = Message(
            message_id="x",
            cert_id="cert-abc",
            from_pub_hex="a" * 64,
            content="hi",
            timestamp=1,
            signature="s",
        )
        assert msg.seq_num == 0

    def test_seq_num_in_to_dict_when_nonzero(self):
        msg = _make_msg(3)
        d = msg.to_dict()
        assert d["seq_num"] == 3

    def test_seq_num_absent_in_to_dict_when_zero(self):
        msg = _make_msg(0)
        d = msg.to_dict()
        assert "seq_num" not in d

    def test_from_dict_roundtrip(self):
        msg = _make_msg(42)
        recovered = Message.from_dict(msg.to_dict())
        assert recovered.seq_num == 42

    def test_from_dict_missing_seq_num_defaults_zero(self):
        d = {
            "message_id": "m1",
            "cert_id": "cert-abc",
            "from_pub_hex": "a" * 64,
            "content": "hi",
            "timestamp": 1,
            "signature": "s",
        }
        msg = Message.from_dict(d)
        assert msg.seq_num == 0

    def test_seq_num_not_in_canonical_bytes(self):
        # Changing seq_num must NOT change the canonical bytes (signature stability)
        common = dict(message_id="m-same", cert_id="cert-abc", from_pub_hex="a" * 64, content="hi", timestamp=1, signature="s")
        msg_a = Message(**common, seq_num=1)
        msg_b = Message(**common, seq_num=99)
        assert msg_a.canonical_bytes() == msg_b.canonical_bytes()
