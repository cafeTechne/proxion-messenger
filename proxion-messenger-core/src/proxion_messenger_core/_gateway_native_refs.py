"""Version-gated native references over bounded device fanout custody."""
from __future__ import annotations

import json
import re


class NativeReferenceGatewayMixin:
    def _native_ref_capable(self, ws):
        meta = self._session_meta.get(ws) or {}
        return (str(self._client_webids.get(ws, '')).startswith('did:key:')
                and self._fanout_commit_capable(ws) and type(meta.get('native_ref_v')) is int
                and meta['native_ref_v'] == 1 and isinstance(meta.get('native_media_pub'), str)
                and bool(re.fullmatch(r'[A-Za-z0-9_-]{43}', meta['native_media_pub'])))

    @staticmethod
    def _native_ref_payload(payload):
        from .command_validation import SEALED_KEYS, sealed_fields
        return (isinstance(payload, dict) and type(payload.get('native_ref_v')) is int and payload['native_ref_v'] == 1
                and set(payload) == {*SEALED_KEYS, 'native_ref_v', 'recipient_pub', 'from_device_id', 'thread_id', 'thread_peer', 'x25519_pub'}
                and bool(sealed_fields(payload))
                and all(isinstance(payload.get(key), str) and 0 < len(payload[key]) <= 2048
                        for key in ('from_device_id', 'thread_id', 'thread_peer'))
                and all(isinstance(payload.get(key), str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', payload[key])
                        for key in ('recipient_pub', 'x25519_pub')))

    def _native_ref_device(self, owner, device, pub):
        if self._store.is_cert_revoked(f'device:{device}'):
            return False
        # This roster is populated by authenticated primary/delegated
        # registration. The separate WebAuthn device registry is not the DID
        # pairing registry and must not exclude legitimate linked devices.
        return any(row['device_id'] == device and row['pub_b64u'] == pub
                   for row in self._store.list_device_e2e_keys(owner))

    def _native_ref_deliverable(self, ws, payload):
        return (self._native_ref_capable(ws) and self._native_ref_payload(payload)
                and (self._session_meta.get(ws) or {}).get('native_media_pub') == payload['recipient_pub'])

    def _native_ref_parent(self, actor, other, file_id, author):
        if author not in ('actor', 'peer') or not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', file_id):
            return None
        sender, recipient = (actor, other) if author == 'actor' else (other, actor)
        parent = self._store.get_native_media(sender, file_id)
        if (not parent or parent['to_webid'] != recipient or not parent['committed']
                or parent['transport'] not in ('inline', 'chunks') or not parent['header_digest']):
            return None
        return parent

    def _native_ref_source(self, ws, data):
        if (not self._native_ref_capable(ws) or not self._native_storage_writable()
                or type(data.get('native_ref_v')) is not int or data['native_ref_v'] != 1):
            return None
        owner = self._client_webids.get(ws, '')
        device = self._session_device_did.get(ws) or owner
        if self._store.is_cert_revoked(f'device:{device}'):
            return None
        thread = self._native_dm_thread(owner, data.get('cert_id'))
        if not thread or not str(thread['peer_webid']).startswith('did:key:'):
            return None
        peer = thread['peer_webid']
        if (self._is_blocked_for(peer, owner) or self._is_blocked_for(owner, peer)
                or self._resolve_peer_gateway(peer) and not self._sockets_for_exact(peer) and not self._is_owner_account(ws, owner)):
            return None
        return owner, device, peer

    def _native_ref_ready(self, owner, expected_pub):
        ready = (self._store.get_x25519_pub(owner) == expected_pub
                 and self._native_ref_device(owner, owner, expected_pub)
                 and any(self._native_ref_capable(ws) and (self._session_meta.get(ws) or {}).get('native_media_pub') == expected_pub
                         for ws in self._device_sockets(owner, owner)))
        return bool(ready)

    async def _handle_native_ref_capability(self, ws, data):
        source = self._native_ref_source(ws, data)
        if not source:
            return
        owner, device, peer = source
        request, pub = data.get('request_id'), data.get('expected_pub')
        if (not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request)
                or not isinstance(pub, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', pub)):
            return
        parent = self._native_ref_parent(owner, peer, data.get('file_id'), data.get('author'))
        session = (self._session_meta.get(ws) or {}).get('session_id')
        async def respond():
            supported, primary = False, None
            if parent:
                if self._sockets_for_exact(peer):
                    supported = self._native_ref_ready(peer, pub); primary = peer if supported else None
                else:
                    target = self._resolve_peer_gateway(peer)
                    if target:
                        proof = await self._native_exchange(target, peer, {'content_type': 'native_ref', 'operation': 'capability',
                            'native_ref_v': 1, 'request_id': request, 'expected_pub': pub, 'file_id': data['file_id'],
                            'author': data['author'], 'parent_header_digest': parent['header_digest']}, 'native_ref_result')
                        if proof and proof.get('supported') is True and isinstance(proof.get('primary_device_id'), str):
                            supported, primary = True, proof['primary_device_id']
            if (self._native_ref_source(ws, data) != source or (self._session_meta.get(ws) or {}).get('session_id') != session
                    or self._native_ref_parent(owner, peer, data.get('file_id'), data.get('author')) != parent):
                return
            await ws.send(json.dumps({'type': 'native_ref_capability_result', 'native_ref_v': 1, 'request_id': request,
                'file_id': data.get('file_id'), 'author': data.get('author'), 'peer_webid': peer, 'expected_pub': pub,
                'supported': supported, 'primary_device_id': primary, 'peer_pub': pub if supported else None}))
        if not self._schedule_native_task(('ref-capability', id(ws), request), respond):
            await ws.send(json.dumps({'type': 'native_ref_capability_result', 'native_ref_v': 1, 'request_id': request,
                'file_id': data.get('file_id'), 'author': data.get('author'), 'peer_webid': peer, 'expected_pub': pub,
                'supported': False, 'primary_device_id': None, 'peer_pub': None}))

    async def _native_ref_admit(self, owner, device, sender, message, payload):
        if (not self._native_ref_payload(payload) or not self._native_ref_device(owner, device, payload['recipient_pub'])
                or self._is_blocked_for(owner, sender) or self._is_blocked_for(sender, owner)):
            return None
        if not self._store.enqueue_device_fanout(message, sender, owner, device, payload):
            return None
        if self._store.device_fanout_committed(owner, device, sender, message, payload):
            return True
        row = next((row for row in self._store.pending_device_fanout(owner, device)
                    if row['from_webid'] == sender and row['message_id'] == message), None)
        if not row:
            return None
        wire = json.dumps(self._fanout_commit_event(row, device))
        for ws in self._device_sockets(owner, device):
            if (not self._native_storage_writable() or not self._native_ref_deliverable(ws, payload)
                    or not self._store.has_device_fanout(owner, device, row['queue_id']) or self._is_blocked_for(owner, sender)):
                continue
            try:
                await ws.send(wire)
            except Exception:
                pass
        return False  # custody/socket write is not interpreted-message consumption

    async def _handle_native_ref_send(self, ws, data):
        source = self._native_ref_source(ws, data)
        entries, message = data.get('fanout'), data.get('message_id')
        if (not source or not isinstance(message, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message)
                or not isinstance(entries, list) or not 1 <= len(entries) <= 16):
            return
        owner, device, peer = source
        for entry in entries:
            payload = entry.get('payload') if isinstance(entry, dict) else None
            if (not self._native_ref_payload(payload) or entry.get('to_webid') not in (owner, peer)
                    or not isinstance(entry.get('to_device_id'), str) or not 0 < len(entry['to_device_id']) <= 2048
                    or payload['from_device_id'] != device or payload['thread_id'] != data.get('cert_id')
                    or payload['thread_peer'] != peer or payload['x25519_pub'] != (self._session_meta.get(ws) or {}).get('native_media_pub')):
                return
        session = (self._session_meta.get(ws) or {}).get('session_id')
        async def deliver():
            delivered = []
            for entry in entries:
                if (self._native_ref_source(ws, data) != source or (self._session_meta.get(ws) or {}).get('session_id') != session):
                    return
                target, target_device, payload = entry['to_webid'], entry['to_device_id'], entry['payload']
                if target == owner or self._sockets_for_exact(target) or not self._resolve_peer_gateway(target):
                    committed = await self._native_ref_admit(target, target_device, owner, message, payload)
                else:
                    proof = await self._native_exchange(self._resolve_peer_gateway(target), target, {'content_type': 'native_ref',
                        'operation': 'send', 'native_ref_v': 1, 'message_id': message, 'to_device_id': target_device,
                        'payload': payload}, 'native_ref_result')
                    committed = proof.get('committed') is True if proof else False
                if committed is True:
                    delivered.append({'to_webid': target, 'to_device_id': target_device})
            if self._native_ref_source(ws, data) == source and (self._session_meta.get(ws) or {}).get('session_id') == session:
                await ws.send(json.dumps({'type': 'native_ref_fanout_ack', 'native_ref_v': 1, 'message_id': message, 'delivered': delivered}))
        self._schedule_native_task(('ref-send', id(ws), message, tuple(sorted((entry['to_webid'], entry['to_device_id']) for entry in entries))), deliver)

    async def _handle_native_ref_relay(self, data):
        if not self._native_storage_writable() or data.get('to_webid') != self._own_gateway_did():
            return '503 Service Unavailable', '{"error":"native_ref_unavailable"}'
        if type(data.get('native_ref_v')) is not int or data['native_ref_v'] != 1:
            return '400 Bad Request', '{"error":"native_ref_version"}'
        sender = data.get('from_webid', '')
        owner = self._native_chunk_relay_owner(sender)
        if not owner:
            return '200 OK', '{"status":"received"}'
        if data.get('operation') == 'capability':
            pub, request = data.get('expected_pub'), data.get('request_id')
            if (not isinstance(pub, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', pub)
                    or not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request)):
                return '400 Bad Request', '{"error":"native_ref_query"}'
            parent = self._native_ref_parent(sender, owner, data.get('file_id'), data.get('author'))
            ready = bool(parent and parent['header_digest'] == data.get('parent_header_digest') and self._native_ref_ready(owner, pub))
            return self._native_response(data, 'native_ref_result', supported=ready, primary_device_id=owner if ready else None)
        message, device, payload = data.get('message_id'), data.get('to_device_id'), data.get('payload')
        if (data.get('operation') != 'send' or not isinstance(message, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message)
                or not isinstance(device, str) or not 0 < len(device) <= 2048):
            return '400 Bad Request', '{"error":"native_ref_send"}'
        committed = await self._native_ref_admit(owner, device, sender, message, payload)
        if committed is None:
            return '409 Conflict', '{"error":"native_ref_not_accepted"}'
        return self._native_response(data, 'native_ref_result', committed=committed)
