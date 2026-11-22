"""Native partial custody must never look like a complete accepted file."""
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from proxion_messenger_core.local_store import LocalStore


CHUNK = 64 * 1024


def manifest(raw=b'opaque ciphertext bytes'):
    return {'e2e': True, 'e2e_v': 1, 'content': 'Y2lwaGVy', 'nonce': 'A' * 16,
            'ratchet_pub': 'A' * 43, 'msg_num': 0, 'pn': 0,
            'size': len(raw), 'cipher_digest': hashlib.sha256(raw).hexdigest(),
            'chunk_count': (len(raw) + CHUNK - 1) // CHUNK, 'recipient_pub': 'A' * 43}


def begin(store, raw=b'opaque ciphertext bytes', *, message='file', sender='alice', recipient='bob', incoming=False, meta=None):
    return store.begin_native_media_chunks(sender, message, recipient, 'thread', 'A' * 43,
                                           meta if meta is not None else manifest(raw), incoming=incoming)


def put(store, row, seq, raw):
    return store.put_native_media_part(row['from_webid'], row['message_id'], row['manifest_digest'], seq, raw)


@pytest.fixture
def store(tmp_path):
    db = LocalStore(str(tmp_path / 'parts.db'))
    yield db
    db.close()


def test_manifest_is_durable_but_not_full_custody_or_commit(tmp_path):
    path = str(tmp_path / 'restart.db')
    db = LocalStore(path)
    first = begin(db, b'X' * (CHUNK * 2 + 17))
    assert first['transport'] == 'chunks' and not first['custody_complete']
    assert first['event']['type'] == 'native_media_offer'
    assert first['event']['chunk_count'] == 3
    args = ('bob', first['queue_id'], 'alice', 'file', first['envelope_digest'])
    assert not db.commit_native_media(*args)
    assert not db.commit_native_media(*args, forwarded=True)
    assert not db.commit_native_media_header(*args)
    with sqlite3.connect(path) as external:
        complete, reserved = external.execute('SELECT custody_complete,byte_count FROM native_media').fetchone()
        assert complete == 0 and reserved > CHUNK * 2 + 17
        assert external.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0] == 0
    assert put(db, first, 1, b'X' * CHUNK)
    db.close(); db = LocalStore(path)
    try:
        assert begin(db, b'X' * (CHUNK * 2 + 17)) == first
        assert db.native_media_missing_parts('alice', 'file', first['manifest_digest']) == [0, 2]
        assert db.get_native_media_part('alice', 'file', first['manifest_digest'], 1)['data'] == b'X' * CHUNK
    finally:
        db.close()


def test_exact_parts_are_idempotent_and_full_ordered_hash_precedes_custody(store):
    raw = b'A' * CHUNK + b'B' * CHUNK + b'C' * 17
    row = begin(store, raw)
    assert put(store, row, 2, raw[CHUNK * 2:])
    assert put(store, row, 1, raw[CHUNK:CHUNK * 2])
    assert put(store, row, 1, raw[CHUNK:CHUNK * 2])
    assert not put(store, row, 1, b'Z' * CHUNK)
    assert not put(store, row, 0, b'Z' * CHUNK)  # would complete the wrong digest
    assert store.native_media_missing_parts('alice', 'file', row['manifest_digest']) == [0]
    assert store.get_native_media_part('alice', 'file', row['manifest_digest'], 1)['data'] == b'B' * CHUNK
    complete = put(store, row, 0, raw[:CHUNK])
    assert complete['custody_complete'] and not complete['header_committed'] and not complete['committed']
    args = ('bob', row['queue_id'], 'alice', 'file', row['envelope_digest'])
    assert store.commit_native_media_header(*args)
    assert store.get_native_media_part('alice', 'file', row['manifest_digest'], 0)
    assert store.commit_native_media(*args)
    with store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0] == 0
        assert conn.execute('SELECT byte_count FROM native_media').fetchone()[0] == 0
    assert not put(store, row, 0, raw[:CHUNK])
    retried = begin(store, raw)
    assert retried['custody_complete'] and retried['committed'] and retried['event'] is None
    assert not begin(store, raw, meta={**manifest(raw), 'msg_num': 1})


@pytest.mark.parametrize('size', [2 * 1024 * 1024, 25 * 1024 * 1024])
def test_full_sized_ciphertext_survives_mid_transfer_restart(tmp_path, size):
    raw = b''.join(bytes([seq % 251]) * min(CHUNK, size - seq * CHUNK) for seq in range((size + CHUNK - 1) // CHUNK))
    path = str(tmp_path / 'large.db'); db = LocalStore(path)
    try:
        row = begin(db, raw)
        assert row and not row['custody_complete']
        for seq in range(row['chunk_count']):
            if seq == row['chunk_count'] // 2:
                db.close(); db = LocalStore(path)
                assert db.native_media_missing_parts('alice', 'file', row['manifest_digest'])[0] == seq
            accepted = put(db, row, seq, raw[seq * CHUNK:(seq + 1) * CHUNK])
            assert accepted['custody_complete'] == (seq == row['chunk_count'] - 1)
        db.close(); db = LocalStore(path)
        digest = hashlib.sha256()
        for seq in range(row['chunk_count']):
            digest.update(db.get_native_media_part('alice', 'file', row['manifest_digest'], seq)['data'])
        assert digest.hexdigest() == manifest(raw)['cipher_digest']
        assert begin(db, raw)['custody_complete']
        additional = begin(db, b'X' * (25 * 1024 * 1024), message='additional')
        assert bool(additional) == (size == 2 * 1024 * 1024)
        if additional:
            assert not begin(db, b'X' * (25 * 1024 * 1024), message='over-budget')
    finally:
        db.close()


def test_no_foreign_manifest_or_sequence_can_replace_partial_bytes(store):
    raw = b'X' * (CHUNK + 17); row = begin(store, raw)
    assert not store.put_native_media_part('foreign', 'file', row['manifest_digest'], 0, raw[:CHUNK])
    assert not store.put_native_media_part('alice', 'file', 'f' * 64, 0, raw[:CHUNK])
    for seq, data in [(True, raw[:CHUNK]), (-1, raw[:CHUNK]), (2, b'X' * 17), (0, b'X' * 17), (1, b'X' * CHUNK)]:
        assert not put(store, row, seq, data)
    assert store.native_media_missing_parts('alice', 'file', row['manifest_digest']) == [0, 1]
    assert not begin(store, raw, recipient='other')
    assert not begin(store, raw, incoming=True)
    assert not begin(store, raw, meta={**manifest(raw), 'cipher_digest': 'f' * 64})


def test_parts_reserve_the_existing_global_budget_and_share_it_with_inline(store):
    raw = b'X' * (2 * CHUNK + 17)
    row = begin(store, raw)
    with store._conn() as conn:
        reserved = conn.execute('SELECT byte_count FROM native_media').fetchone()[0]
    assert reserved > len(raw)
    store.MAX_NATIVE_MEDIA_BYTES = reserved + 100
    assert not begin(store, message='other')
    from test_native_media_custody import accept
    assert not accept(store, message='inline')
    # Capacity changes never prevent exact accepted retries or reserved parts.
    store.MAX_NATIVE_MEDIA_BYTES = 1
    assert begin(store, raw) == row
    assert put(store, row, 0, raw[:CHUNK])
    with store._conn() as conn:
        assert conn.execute('SELECT byte_count FROM native_media').fetchone()[0] == reserved


def test_concurrent_manifest_admission_never_exceeds_source_limit(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda index: begin(store, message=str(index)), range(15)))
    assert sum(row is not None for row in rows) == 10
    assert len(store.pending_native_media('bob')) == 10
    with store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0] == 0


@pytest.mark.parametrize('action', ['recipient block', 'sender block', 'sender revoke', 'recipient revoke', 'account deletion'])
def test_policy_cleanup_purges_parts_and_keeps_no_fake_delivery(store, action):
    raw = b'X' * (CHUNK + 17); row = begin(store, raw)
    assert put(store, row, 0, raw[:CHUNK])
    if action == 'recipient block': store.set_block('bob', 'alice', True)
    elif action == 'sender block': store.set_block('alice', 'bob', True)
    elif action == 'sender revoke': store.mark_revoked('device:alice', 'alice')
    elif action == 'recipient revoke': store.mark_revoked('device:bob', 'bob')
    else: store.delete_account_data('alice')
    with store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0] == 0
        assert conn.execute('SELECT COALESCE(SUM(byte_count),0) FROM native_media').fetchone()[0] == 0
    assert not put(store, row, 1, raw[CHUNK:])
    remaining = store.get_native_media('alice', 'file')
    if action != 'account deletion':
        assert remaining['status'] == 'blocked' and not remaining['committed'] and not remaining['header_committed']
    else:
        assert remaining is None


def test_forwarding_keeps_only_full_custody_receipt_not_header_consumption(store):
    raw = b'X' * 17; row = begin(store, raw); assert put(store, row, 0, raw)
    assert store.commit_native_media('bob', row['queue_id'], 'alice', 'file', row['envelope_digest'], forwarded=True)
    forwarded = begin(store, raw)
    assert forwarded['forwarded'] and forwarded['custody_complete'] and not forwarded['header_committed']
    with store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0] == 0


def test_missing_part_batches_are_bounded_and_reject_invalid_arguments(store):
    row = begin(store, b'X' * (CHUNK * 20))
    assert store.native_media_missing_parts('alice', 'file', row['manifest_digest']) == list(range(8))
    assert store.native_media_missing_parts('alice', 'file', row['manifest_digest'], after=7, limit=8) == list(range(8, 16))
    for opts in [{'limit': 17}, {'limit': True}, {'after': -2}, {'after': False}]:
        with pytest.raises(ValueError): store.native_media_missing_parts('alice', 'file', row['manifest_digest'], **opts)


@pytest.mark.parametrize('field,value', [('size', True), ('size', 0), ('size', 25 * 1024 * 1024 + 1),
    ('chunk_count', True), ('chunk_count', 2), ('cipher_digest', 'A' * 64), ('e2e_v', True), ('extra', 'bad')])
def test_invalid_manifests_never_reserve_storage(store, field, value):
    assert not begin(store, meta={**manifest(), field: value})
    assert store.pending_native_media('bob') == []


def test_schema_69_upgrade_preserves_inline_bytes_without_inventing_chunk_evidence(tmp_path):
    from test_native_media_custody import accept
    path = str(tmp_path / 'upgrade.db'); old = LocalStore(path); original = accept(old); old.close()
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TRIGGER native_media_clear_parts'); conn.execute('DROP TRIGGER native_media_delete_parts')
        conn.execute('DROP TABLE native_media_parts')
        for field in ['transport', 'cipher_digest', 'total_bytes', 'chunk_count', 'custody_complete', 'manifest_digest', 'recipient_pub', 'chunk_identity_digest']:
            conn.execute('ALTER TABLE native_media DROP COLUMN ' + field)
        conn.execute('UPDATE schema_version SET version=69')
    upgraded = LocalStore(path)
    try:
        restored = upgraded.get_native_media('alice', 'm')
        assert restored['event'] == original['event'] and restored['transport'] == 'inline'
        assert restored['custody_complete'] and restored['manifest_digest'] is None and restored['cipher_digest'] is None
        adapted = begin(upgraded, message='m')  # Explicit exact bytes, never invented by migration.
        assert adapted['envelope_digest'] == original['envelope_digest']
        assert upgraded.get_native_media_part('alice', 'm', adapted['manifest_digest'], 0)['data'] == b'opaque ciphertext bytes'
        assert begin(upgraded, message='new')
    finally:
        upgraded.close()


def test_inline_adaptation_preserves_original_identity_header_progress_and_ciphertext(tmp_path):
    from test_native_media_custody import accept, payload
    path = str(tmp_path / 'adapt.db'); db = LocalStore(path)
    raw = b'A' * CHUNK + b'B' * 17
    original = accept(db, body=payload(raw))
    args = ('bob', original['queue_id'], 'alice', 'm', original['envelope_digest'])
    assert db.commit_native_media_header(*args)
    adapted = begin(db, raw, message='m')
    assert adapted['adapted_inline'] and adapted['custody_complete'] and adapted['header_committed']
    assert not adapted['committed'] and not adapted['forwarded']
    assert adapted['queue_id'] == original['queue_id'] and adapted['envelope_digest'] == original['envelope_digest']
    assert adapted['event']['timestamp'] == original['event']['timestamp']
    assert adapted['header_digest'] == original['header_digest']
    assert b''.join(db.get_native_media_part('alice', 'm', adapted['manifest_digest'], seq)['data'] for seq in range(2)) == raw
    assert accept(db, body=payload(raw)) == adapted  # Exact old API retry does not downgrade transport.
    db.close(); db = LocalStore(path)
    try:
        assert begin(db, raw, message='m') == adapted
        assert db.commit_native_media(*args)  # Original receipt token remains valid.
        assert db.get_native_media_part('alice', 'm', adapted['manifest_digest'], 0) is None
        assert begin(db, raw, message='m')['committed']
    finally:
        db.close()


@pytest.mark.parametrize('change', ['recipient', 'thread', 'digest', 'header', 'bytes', 'direction'])
def test_inline_adaptation_rejects_conflicts_without_mutating_original_custody(store, change):
    from test_native_media_custody import accept, payload
    raw = b'A' * CHUNK + b'B' * 17
    original = accept(store, body=payload(raw)); meta = manifest(raw)
    kwargs = {'sender': 'alice', 'message_id': 'm', 'recipient': 'bob', 'thread_id': 'thread',
              'media_digest': 'A' * 43, 'manifest': meta}
    if change == 'recipient': kwargs['recipient'] = 'foreign'
    if change == 'thread': kwargs['thread_id'] = 'other'
    if change == 'digest': kwargs['media_digest'] = 'B' * 43
    if change == 'header': meta['msg_num'] += 1
    if change == 'bytes': kwargs['manifest'] = manifest(b'Z' * len(raw))
    if change == 'direction': kwargs['incoming'] = True
    assert not store.begin_native_media_chunks(**kwargs)
    assert store.get_native_media('alice', 'm') == original
    with store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0] == 0


def test_completed_inline_cannot_acquire_invented_manifest_or_recipient_key(store):
    from test_native_media_custody import accept
    original = accept(store)
    assert store.commit_native_media('bob', original['queue_id'], 'alice', 'm', original['envelope_digest'])
    assert not begin(store, message='m')
    assert store.get_native_media('alice', 'm')['transport'] == 'inline'


def test_adaptation_pins_manifest_key_and_respects_reduced_shared_budget(store):
    from test_native_media_custody import accept, payload
    raw = b'A' * CHUNK + b'B' * 17
    original = accept(store, body=payload(raw)); store.MAX_NATIVE_MEDIA_BYTES = 1
    assert not begin(store, raw, message='m')
    assert store.get_native_media('alice', 'm') == original
    store.MAX_NATIVE_MEDIA_BYTES = 32 * 1024 * 1024
    adapted = begin(store, raw, message='m'); assert adapted
    changed = manifest(raw); changed['recipient_pub'] = 'B' * 43
    assert not begin(store, raw, message='m', meta=changed)
    assert store.get_native_media('alice', 'm') == adapted


def test_failed_part_insertion_rolls_back_the_entire_inline_adaptation(store):
    from test_native_media_custody import accept, payload
    raw = b'A' * CHUNK + b'B' * 17
    original = accept(store, body=payload(raw))
    with store._conn() as conn:
        conn.execute("""CREATE TRIGGER fail_adaptation BEFORE INSERT ON native_media_parts
            WHEN NEW.seq=1 BEGIN SELECT RAISE(ABORT, 'injected part failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='injected part failure'):
        begin(store, raw, message='m')
    assert store.get_native_media('alice', 'm') == original
    with store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0] == 0
        conn.execute('DROP TRIGGER fail_adaptation')
    assert begin(store, raw, message='m')['adapted_inline']


def test_schema_70_chunk_upgrade_preserves_exact_manifest_parts_and_receipt(tmp_path):
    path = str(tmp_path / 'v70.db'); old = LocalStore(path)
    raw = b'A' * CHUNK + b'B' * 17
    original = begin(old, raw)
    for seq in range(2): assert put(old, original, seq, raw[seq * CHUNK:(seq + 1) * CHUNK])
    original = old.get_native_media('alice', 'file'); old.close()
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE native_media DROP COLUMN chunk_identity_digest')
        conn.execute('UPDATE schema_version SET version=70')
    upgraded = LocalStore(path)
    try:
        assert upgraded.get_native_media('alice', 'file') == original
        assert begin(upgraded, raw) == original
        assert upgraded.get_native_media_part('alice', 'file', original['manifest_digest'], 1)['data'] == raw[CHUNK:]
        assert upgraded.commit_native_media('bob', original['queue_id'], 'alice', 'file', original['envelope_digest'])
    finally:
        upgraded.close()


def test_decline_is_exact_durable_header_consumption_without_a_full_delivery_receipt(tmp_path):
    path = str(tmp_path / 'decline.db'); db = LocalStore(path)
    raw = b'A' * CHUNK + b'B' * 17
    row = begin(db, raw)
    args = ('bob', row['queue_id'], 'alice', 'file', row['envelope_digest'], 'A' * 43)
    assert not db.decline_native_media(*args)  # A guessed partial upload is not an offered file.
    for seq in range(2): assert put(db, row, seq, raw[seq * CHUNK:(seq + 1) * CHUNK])
    for changed in [('foreign', *args[1:]), (args[0], True, *args[2:]), (*args[:-2], 'f' * 64, args[-1]), (*args[:-1], 'B' * 43)]:
        assert not db.decline_native_media(*changed)
    assert db.decline_native_media(*args); db.close(); db = LocalStore(path)
    try:
        declined = db.get_native_media('alice', 'file')
        assert declined['declined'] and declined['header_committed'] and not declined['committed']
        assert declined['event'] is None and db.pending_native_media('bob') == []
        assert db.get_native_media_part('alice', 'file', row['manifest_digest'], 0) is None
        assert db.decline_native_media(*args)
        assert not db.commit_native_media(*args[:-1])  # A stale full commit cannot change the chosen outcome.
        assert begin(db, raw)['declined']
    finally:
        db.close()


def test_decline_and_full_commit_have_one_atomic_terminal_winner(store):
    raw = b'opaque ciphertext bytes'
    for index in range(12):
        row = begin(store, raw, message='race-' + str(index)); assert put(store, row, 0, raw)
        commit = ('bob', row['queue_id'], 'alice', row['message_id'], row['envelope_digest'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            declined, delivered = list(pool.map(lambda decline: store.decline_native_media(*commit, 'A' * 43)
                if decline else store.commit_native_media(*commit), [True, False]))
        assert declined != delivered
        result = store.get_native_media('alice', row['message_id'])
        assert result['header_committed'] and result['declined'] != result['committed']
        assert result['event'] is None


def test_decline_does_not_bypass_existing_block_and_revocation_policy(store):
    row = begin(store); assert put(store, row, 0, b'opaque ciphertext bytes')
    store.set_block('bob', 'alice', True)
    assert not store.decline_native_media('bob', row['queue_id'], 'alice', 'file', row['envelope_digest'], 'A' * 43)
    assert store.get_native_media('alice', 'file')['status'] == 'blocked'
    assert not store.get_native_media('alice', 'file')['declined']


@pytest.mark.parametrize('action', ['sender block', 'recipient block', 'sender revoke', 'recipient revoke'])
def test_policy_stops_forwarded_pending_receipts_without_changing_existing_terminal_evidence(store, action):
    raw = b'X' * 17
    forwarded = begin(store, raw, message='forwarded'); assert put(store, forwarded, 0, raw)
    committed = begin(store, raw, message='committed'); assert put(store, committed, 0, raw)
    declined = begin(store, raw, message='declined'); assert put(store, declined, 0, raw)
    for row in [forwarded, committed]:
        assert store.commit_native_media('bob', row['queue_id'], 'alice', row['message_id'], row['envelope_digest'], forwarded=row is forwarded)
    assert store.decline_native_media('bob', declined['queue_id'], 'alice', 'declined', declined['envelope_digest'], 'A' * 43)
    if action == 'sender block': store.set_block('alice', 'bob', True)
    elif action == 'recipient block': store.set_block('bob', 'alice', True)
    elif action == 'sender revoke': store.mark_revoked('device:alice', 'alice')
    else: store.mark_revoked('device:bob', 'bob')
    result = store.get_native_media('alice', 'forwarded')
    assert result['status'] == 'blocked' and not result['committed'] and not result['header_committed']
    assert not store.record_native_peer_progress('alice', 'forwarded', result['media_digest'], result['header_digest'], header_committed=True, committed=True)
    assert store.get_native_media('alice', 'committed')['committed']
    assert store.get_native_media('alice', 'declined')['declined']
