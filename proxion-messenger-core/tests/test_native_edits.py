"""Caption custody cannot rewrite the file envelope or bypass original authorship."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from proxion_messenger_core.local_store import LocalStore
from test_native_media_custody import payload
from test_native_media_parts import begin, put


def file_parent(db, *, sender='alice', recipient='bob', incoming=False):
    raw = b'opaque ciphertext bytes'
    row = begin(db, raw, sender=sender, recipient=recipient, incoming=incoming)
    assert put(db, row, 0, raw)
    assert db.commit_native_media(recipient, row['queue_id'], sender, 'file', row['envelope_digest'])
    if not incoming: db.save_dm_thread('thread', recipient, owner_webid=sender)
    return db.get_native_media(sender, 'file')


def edit(db, parent, action='action', sequence=1, **patch):
    body = payload(); body.pop('file')
    data = dict(sender=parent['from_webid'], action_id=action, recipient=parent['to_webid'],
        thread_id=parent['from_webid'] if parent['incoming'] else 'thread', media_digest='B' * 43,
        file_id=parent['message_id'], sequence=sequence, parent_header_digest=parent['header_digest'],
        header=body, recipient_pub='A' * 43, incoming=parent['incoming'])
    data.update(patch)
    return db.accept_native_edit(**data)


@pytest.fixture
def store(tmp_path):
    db = LocalStore(str(tmp_path / 'edits.db'))
    yield db
    db.close()


def test_caption_custody_is_exact_durable_separate_from_file_receipt_and_not_full_delivery(tmp_path):
    path = str(tmp_path / 'restart.db'); db = LocalStore(path)
    parent = file_parent(db); row = edit(db, parent)
    assert row['transport'] == 'edit' and row['native_parent_id'] == 'file' and row['native_sequence'] == 1
    assert row['custody_complete'] and not row['committed'] and not row['header_committed']
    assert 'file' not in row['event'] and row['event']['type'] == 'native_edit'
    assert edit(db, parent, display_name='changed') == row
    assert db.get_native_media('alice', 'file') == parent
    db.close(); db = LocalStore(path)
    try:
        assert edit(db, parent) == row
        args = ('bob', row['queue_id'], 'alice', 'action', row['envelope_digest'])
        assert db.commit_native_media_header(*args)
        assert db.get_native_media('alice', 'action')['event'] == row['event']
        assert db.commit_native_media(*args)
        complete = edit(db, parent)
        assert complete['committed'] and complete['event'] is None and complete['native_parent_id'] == 'file'
        assert db.get_native_media('alice', 'file') == parent
    finally:
        db.close()


@pytest.mark.parametrize('patch', [
    {'sender': 'foreign'}, {'recipient': 'foreign'}, {'thread_id': 'foreign'},
    {'file_id': 'missing'}, {'parent_header_digest': 'f' * 64}, {'action_id': 'file'},
    {'action_id': '../action'}, {'sequence': True}, {'sequence': 0}, {'sequence': 101},
    {'recipient_pub': 'not-a-key'}, {'recipient_pub': 'B' * 43}, {'media_digest': 'bad'}, {'incoming': True},
])
def test_invalid_foreign_or_unowned_references_cannot_admit_an_edit(store, patch):
    parent = file_parent(store)
    assert not edit(store, parent, **patch)
    assert store.get_native_media('alice', 'file') == parent
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM native_media WHERE transport='edit'").fetchone()[0] == 0


def test_owned_wrong_peer_thread_is_not_original_file_authority(store):
    parent = file_parent(store)
    store.save_dm_thread('other', 'carol', owner_webid='alice')
    assert not edit(store, parent, thread_id='other')
    assert not store.native_edit_next_sequence('alice', 'file', 'bob', 'other')


def test_inline_parent_and_incoming_relationship_identity_are_supported_without_replacing_file(store):
    original = store.accept_native_media('gateway', 'inline-file', 'owner', 'gateway', 'A' * 43, payload(), incoming=True)
    assert store.commit_native_media('owner', original['queue_id'], 'gateway', 'inline-file', original['envelope_digest'])
    parent = store.get_native_media('gateway', 'inline-file')
    assert store.native_edit_next_sequence('gateway', 'inline-file', 'owner', 'gateway', incoming=True) == 1
    assert not edit(store, parent, thread_id='different-gateway')
    assert edit(store, parent)['incoming']
    assert store.get_native_media('gateway', 'inline-file') == parent


def test_one_sequence_has_one_winner_and_conflicting_retry_cannot_replace_opaque_header(store):
    parent = file_parent(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda action: edit(store, parent, action=action), ['first', 'second']))
    assert sum(bool(row) for row in results) == 1
    accepted = next(row for row in results if row)
    hdr = {key: accepted['event'][key] for key in payload() if key != 'file'}
    changed = {**hdr, 'content': 'bmV3IGNpcGhlcnRleHQ'}
    assert not edit(store, parent, action=accepted['message_id'], header=changed)
    assert not edit(store, parent, action=accepted['message_id'], recipient_pub='B' * 43)
    assert store.get_native_media('alice', accepted['message_id']) == accepted
    assert store.native_edit_next_sequence('alice', 'file', 'bob', 'thread') == 2
    assert not edit(store, parent, action='skip', sequence=3)
    assert edit(store, parent, action='next', sequence=2)


@pytest.mark.parametrize('policy', ['sender block', 'recipient block', 'sender revoke', 'recipient revoke', 'delete'])
def test_policy_cleanup_covers_pending_caption_without_rewriting_completed_file_fact(store, policy):
    parent = file_parent(store); row = edit(store, parent)
    if policy == 'sender block': store.set_block('alice', 'bob', True)
    elif policy == 'recipient block': store.set_block('bob', 'alice', True)
    elif policy == 'sender revoke': store.mark_revoked('device:alice', 'alice')
    elif policy == 'recipient revoke': store.mark_revoked('device:bob', 'bob')
    else: store.delete_account_data('alice')
    assert not edit(store, parent, action='replacement')
    result = store.get_native_media('alice', row['message_id'])
    if policy == 'delete': assert result is None
    else:
        assert result['status'] == 'blocked' and result['event'] is None and not result['committed']
        assert store.get_native_media('alice', 'file')['committed']


def test_shared_custody_capacity_and_existing_edit_ceiling_never_evict_or_reauthor(store):
    parent = file_parent(store); store.MAX_NATIVE_MEDIA_BYTES = 1
    assert not edit(store, parent)
    store.MAX_NATIVE_MEDIA_BYTES = 32 * 1024 * 1024; store.MAX_NATIVE_EDITS = 2
    first = edit(store, parent); assert first
    assert edit(store, parent, 'second', 2)
    assert not edit(store, parent, 'third', 3)
    assert store.native_edit_next_sequence('alice', 'file', 'bob', 'thread') is None
    assert edit(store, parent) == first
    store.MAX_NATIVE_EDITS = 3; store.MAX_NATIVE_MEDIA_PENDING = 1
    assert not edit(store, parent, 'new-file-action', 3)
    store.MAX_NATIVE_MEDIA_PENDING = 10; store.MAX_NATIVE_MEDIA_RECEIPTS = 3
    assert not edit(store, parent, 'receipt-cap', 3)
    assert store.get_native_media('alice', 'file') == parent


def test_noncommitted_parent_and_an_edit_parent_are_not_editable_file_evidence(store):
    row = begin(store); assert put(store, row, 0, b'opaque ciphertext bytes')
    store.save_dm_thread('thread', 'bob', owner_webid='alice')
    assert not edit(store, row)
    assert not store.native_edit_next_sequence('alice', 'file', 'bob', 'thread')
    assert store.commit_native_media('bob', row['queue_id'], 'alice', 'file', row['envelope_digest'])
    parent = store.get_native_media('alice', 'file'); action = edit(store, parent)
    assert store.commit_native_media('bob', action['queue_id'], 'alice', 'action', action['envelope_digest'])
    assert not edit(store, store.get_native_media('alice', 'action'), action='nested')


def test_schema_71_upgrade_preserves_existing_opaque_file_and_receipt(tmp_path):
    path = str(tmp_path / 'v71.db'); db = LocalStore(path)
    parent = file_parent(db); pending = begin(db, message='pending'); db.close()
    with sqlite3.connect(path) as conn:
        conn.execute('DROP INDEX native_edit_order')
        conn.execute('ALTER TABLE native_media DROP COLUMN native_parent_id')
        conn.execute('ALTER TABLE native_media DROP COLUMN native_sequence')
        conn.execute('UPDATE schema_version SET version=71')
    upgraded = LocalStore(path)
    try:
        assert upgraded.get_native_media('alice', 'file') == parent
        assert upgraded.get_native_media('alice', 'pending') == pending
        assert edit(upgraded, parent)
    finally:
        upgraded.close()


@pytest.mark.asyncio
async def test_staged_caption_rows_never_reach_an_unnegotiated_file_browser(tmp_path):
    from test_native_media_custody import gateway, socket
    gw = gateway(tmp_path, 'staged-caption')
    ws = socket(gw, 'did:key:bob')
    parent = file_parent(gw._store, sender='did:key:alice', recipient='did:key:bob')
    action = edit(gw._store, parent)
    await gw._deliver_native_media(action)
    ws.send.assert_not_called()
    assert gw._store.get_native_media('did:key:alice', action['message_id']) == action
    gw._store.close()
