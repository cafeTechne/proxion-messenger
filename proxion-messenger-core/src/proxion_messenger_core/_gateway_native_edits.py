"""Primary-only caption actions; opaque custody is not receive consumption."""
from __future__ import annotations

import json
import re


class NativeEditGatewayMixin:
    def _native_edits_capable(self, ws):
        meta = self._session_meta.get(ws) or {}
        return (self._native_capable(ws) and type(meta.get('native_edit_v')) is int
                and meta['native_edit_v'] == 1)

    def _native_edit_ready(self, owner, sender, file_id, expected_pub, parent_digest, sequence):
        context = self._store.native_edit_context(sender, file_id, owner, sender, incoming=True)
        pub = self._store.get_x25519_pub(owner)
        supported = (context and context['parent_header_digest'] == parent_digest and context['sequence'] == sequence
                     and (not context['recipient_pub'] or context['recipient_pub'] == expected_pub)
                     and pub == expected_pub and isinstance(pub, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', pub)
                     and any(self._native_edits_capable(ws) and (self._session_meta.get(ws) or {}).get('native_media_pub') == pub
                             for ws in self._device_sockets(owner, owner)))
        return bool(supported), pub if supported else None

    def _native_edit_source(self, ws, data):
        if (not self._native_edits_capable(ws) or not self._native_storage_writable()
                or type(data.get('v')) is not int or data['v'] != 1):
            return None
        owner = self._client_webids.get(ws, '')
        if self._store.is_cert_revoked(f'device:{owner}'):
            return None
        thread = self._native_dm_thread(owner, data.get('cert_id'))
        if (not thread or not str(thread['peer_webid']).startswith('did:key:')
                or data.get('cert_id') in self.dm_clients or data.get('room_id')):
            return None
        peer = thread['peer_webid']
        if (self._resolve_peer_gateway(peer) and not self._sockets_for_exact(peer)
                and not self._is_owner_account(ws, owner)):
            return None
        return owner, thread

    async def _handle_native_edit_capability(self, ws, data):
        source = self._native_edit_source(ws, data)
        if not source:
            return
        owner, thread = source
        peer, file_id, request = thread['peer_webid'], data.get('file_id'), data.get('request_id')
        expected = data.get('expected_pub')
        if (not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request)
                or not isinstance(expected, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', expected)):
            return
        context = self._store.native_edit_context(owner, file_id, peer, thread['thread_id'])
        session = (self._session_meta.get(ws) or {}).get('session_id')
        async def respond():
            supported, pub = False, None
            if context and (not context['recipient_pub'] or context['recipient_pub'] == expected):
                if self._sockets_for_exact(peer):
                    # Same-gateway peers share the outgoing original receipt.
                    supported, pub = self._native_ready(peer, expected, edits=True)
                else:
                    target = self._resolve_peer_gateway(peer)
                    if target:
                        proof = await self._native_exchange(target, peer, {'content_type': 'native_edit', 'operation': 'capability',
                            'native_edit_v': 1, 'file_id': file_id, 'request_id': request, 'expected_pub': expected,
                            'parent_header_digest': context['parent_header_digest'], 'native_sequence': context['sequence']}, 'native_edit_result')
                        if proof and type(proof.get('supported')) is bool and proof.get('peer_pub') == expected:
                            supported, pub = proof['supported'], expected
            live = self._native_edit_source(ws, data)
            if (not live or live != source or (self._session_meta.get(ws) or {}).get('session_id') != session
                    or self._store.native_edit_context(owner, file_id, peer, thread['thread_id']) != context):
                return
            await ws.send(json.dumps({'type': 'native_edit_capability_result', 'v': 1, 'native_edit_v': 1,
                'request_id': request, 'file_id': file_id, 'peer_webid': peer, 'expected_pub': expected,
                'supported': bool(supported), 'peer_pub': pub,
                'parent_header_digest': context['parent_header_digest'] if context else None,
                'native_sequence': context['sequence'] if context else None}))
        if not self._schedule_native_task(('edit-capability', id(ws), request), respond):
            await ws.send(json.dumps({'type': 'native_edit_capability_result', 'v': 1, 'native_edit_v': 1,
                'request_id': request, 'file_id': file_id, 'peer_webid': peer, 'expected_pub': expected,
                'supported': False, 'peer_pub': None}))

    async def _handle_native_edit_send(self, ws, data):
        source = self._native_edit_source(ws, data)
        if not source:
            return
        owner, thread = source
        row = self._store.accept_native_edit(owner, data.get('message_id'), thread['peer_webid'], thread['thread_id'],
            data.get('media_digest'), data.get('file_id'), data.get('native_sequence'), data.get('parent_header_digest'),
            data.get('payload'), data.get('recipient_pub'), self._name_for(ws, owner))
        if not row:
            await ws.send(json.dumps({'type': 'error', 'message': 'native_edit_not_accepted', 'cmd': 'native_edit_send',
                'message_id': data.get('message_id')}))
            return
        await ws.send(json.dumps(self._native_status(row)))
        if row['event'] or row['forwarded']:
            # A retry after custody handoff must query the peer's durable
            # receive receipt, even though the opaque action was released.
            self._schedule_native_media(row)

    async def _deliver_native_edit(self, row):
        sender, peer = row['from_webid'], row['to_webid']
        sockets = [ws for ws in self._device_sockets(peer, peer) if self._native_edits_capable(ws)
                   and (self._session_meta.get(ws) or {}).get('native_media_pub') == row['recipient_pub']]
        if sockets:
            if ((row['incoming'] or self._auth_enforced()) and self._store.get_relationship_owner(sender) != peer):
                return
            if not self._file_delivery_allowed(peer, sender):
                return
            event = {**row['event'], 'native_commit': {'v': 1, 'queue_id': row['queue_id'], 'envelope_digest': row['envelope_digest']}}
            for ws in sockets:
                live = self._store.get_native_media(sender, row['message_id'])
                if (not live or not live['event'] or self._is_blocked_for(peer, sender) or self._is_blocked_for(sender, peer)
                        or not self._native_edits_capable(ws) or (self._session_meta.get(ws) or {}).get('native_media_pub') != row['recipient_pub']):
                    continue
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass  # Retain exact action until journal/history commit.
            return
        if row['incoming']:
            return
        target = self._resolve_peer_gateway(peer)
        if not target:
            return
        from .command_validation import SEALED_KEYS
        event = row['event']
        proof = await self._native_exchange(target, peer, {'content_type': 'native_edit', 'operation': 'send', 'native_edit_v': 1,
            'message_id': row['message_id'], 'media_digest': row['media_digest'], 'header_digest': row['header_digest'],
            'file_id': row['native_parent_id'], 'native_sequence': row['native_sequence'],
            'parent_header_digest': event['parent_header_digest'], 'recipient_pub': row['recipient_pub'],
            'payload': {key: event[key] for key in SEALED_KEYS}}, 'native_edit_result')
        if self._native_progress_valid(proof) and not proof.get('declined', False) and self._native_storage_writable():
            if self._store.commit_native_media(peer, row['queue_id'], sender, row['message_id'], row['envelope_digest'], forwarded=True):
                self._store.record_native_peer_progress(sender, row['message_id'], row['media_digest'], row['header_digest'],
                    header_committed=proof['header_committed'], committed=proof['committed'])

    async def _handle_native_edit_relay(self, data):
        if not self._native_storage_writable() or data.get('to_webid') != self._own_gateway_did():
            return '503 Service Unavailable', '{"error":"native_edit_unavailable"}'
        if type(data.get('native_edit_v')) is not int or data['native_edit_v'] != 1:
            return '400 Bad Request', '{"error":"native_edit_version"}'
        sender = data.get('from_webid', '')
        owner = self._native_chunk_relay_owner(sender)
        if not owner:
            return '200 OK', '{"status":"received"}'
        if data.get('operation') == 'capability':
            request, expected = data.get('request_id'), data.get('expected_pub')
            if (not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request)
                    or not isinstance(expected, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', expected)
                    or type(data.get('native_sequence')) is not int):
                return '400 Bad Request', '{"error":"native_edit_query"}'
            ready, pub = self._native_edit_ready(owner, sender, data.get('file_id'), expected,
                data.get('parent_header_digest'), data['native_sequence'])
            return self._native_response(data, 'native_edit_result', supported=ready, peer_pub=pub)
        if data.get('operation') != 'send':
            return '400 Bad Request', '{"error":"native_edit_operation"}'
        try:
            digest = self._store._native_header_digest(data.get('payload'))
        except (KeyError, TypeError, ValueError):
            return '409 Conflict', '{"error":"native_edit_header"}'
        if digest != data.get('header_digest'):
            return '409 Conflict', '{"error":"native_edit_header"}'
        row = self._store.accept_native_edit(sender, data.get('message_id'), owner, sender, data.get('media_digest'),
            data.get('file_id'), data.get('native_sequence'), data.get('parent_header_digest'), data.get('payload'),
            data.get('recipient_pub'), incoming=True)
        if not row:
            return '409 Conflict', '{"error":"native_edit_not_accepted"}'
        if row['event']:
            self._schedule_native_media(row)
        return self._native_response(data, 'native_edit_result', header_committed=row['header_committed'], committed=row['committed'])
