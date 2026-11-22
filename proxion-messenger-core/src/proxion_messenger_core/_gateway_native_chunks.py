"""Opt-in native part upload and signed gateway custody (browser not enabled yet)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re


class NativeChunkGatewayMixin:
    def _native_chunk_relay_owner(self, sender):
        owner = self._store.get_relationship_owner(sender) if self._store else None
        if (not str(owner or '').startswith('did:key:') or sender in self._revoked_dids
                or self._store.is_revoked(sender) or self._is_blocked_for(owner, sender)
                or self._is_blocked_for(sender, owner)):
            return None
        return owner

    def _native_chunks_capable(self, ws):
        meta = self._session_meta.get(ws) or {}
        return self._native_capable(ws) and type(meta.get('native_chunks_v')) is int and meta['native_chunks_v'] == 1

    @staticmethod
    def _native_part_bytes(value):
        if not isinstance(value, str) or not 0 < len(value) <= 87384:
            return None
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            return None
        return raw if 1 <= len(raw) <= 65536 and base64.b64encode(raw).decode() == value else None

    def _native_chunk_result(self, row, *, after=-1):
        missing = self._store.native_media_missing_parts(row['from_webid'], row['message_id'], row['manifest_digest'], after=after)
        return {'custody_complete': row['custody_complete'], 'header_committed': row['header_committed'],
                'committed': row['committed'], 'missing': missing or [], 'recipient_pub': row['recipient_pub']}

    async def _send_native_part_status(self, ws, row, *, seq=None):
        response = {'type': 'native_media_parts', 'v': 1, 'message_id': row['message_id'],
            'media_digest': row['media_digest'], 'manifest_digest': row['manifest_digest'],
            'header_digest': row['header_digest'], **self._native_chunk_result(row)}
        if seq is not None:
            response['seq'] = seq
        await ws.send(json.dumps(response))

    async def _handle_native_media_begin(self, ws, data):
        if (not self._native_chunks_capable(ws) or not self._native_storage_writable()
                or type(data.get('v')) is not int or data['v'] != 1):
            return
        owner = self._client_webids[ws]
        if self._store.is_cert_revoked(f'device:{owner}'):
            return
        thread = next((thread for thread in self._store.get_dm_threads(owner_webid=owner)
                       if thread['thread_id'] == data.get('cert_id')), None)
        if (not thread or not str(thread['peer_webid']).startswith('did:key:')
                or data.get('cert_id') in self.dm_clients or data.get('room_id')):
            return
        peer = thread['peer_webid']
        if self._resolve_peer_gateway(peer) and not self._sockets_for_exact(peer) and not self._is_owner_account(ws, owner):
            return
        row = self._store.begin_native_media_chunks(owner, data.get('message_id'), peer, thread['thread_id'],
            data.get('media_digest'), data.get('manifest'), self._name_for(ws, owner))
        if not row:
            await ws.send(json.dumps({'type': 'error', 'message': 'native_media_not_accepted', 'message_id': data.get('message_id')}))
            return
        await self._send_native_part_status(ws, row)
        if row['custody_complete']:
            self._schedule_native_media(row)

    async def _handle_native_media_part(self, ws, data):
        if (not self._native_chunks_capable(ws) or not self._native_storage_writable()
                or type(data.get('v')) is not int or data['v'] != 1):
            return
        owner = self._client_webids[ws]
        if self._store.is_cert_revoked(f'device:{owner}'):
            return
        raw = self._native_part_bytes(data.get('data_b64'))
        row = self._store.put_native_media_part(owner, data.get('message_id'), data.get('manifest_digest'), data.get('seq'), raw) if raw else None
        if not row:
            await ws.send(json.dumps({'type': 'error', 'message': 'native_media_part_not_accepted',
                'message_id': data.get('message_id'), 'seq': data.get('seq')}))
            return
        await self._send_native_part_status(ws, row, seq=data['seq'])
        if row['custody_complete']:
            self._schedule_native_media(row)

    async def _handle_native_chunks_relay(self, data):
        if not self._native_storage_writable() or data.get('to_webid') != self._own_gateway_did():
            return '503 Service Unavailable', '{"error":"native_media_unavailable"}'
        after = data.get('after', -1)
        if (type(data.get('native_chunks_v')) is not int or data['native_chunks_v'] != 1
                or type(after) is not int or not -1 <= after < 400):
            return '400 Bad Request', '{"error":"invalid_native_batch"}'
        sender = data.get('from_webid', '')
        owner = self._native_chunk_relay_owner(sender)
        if not owner:
            return '200 OK', '{"status":"received"}'
        operation = data.get('operation')
        if operation == 'begin':
            manifest = data.get('manifest')
            canonical = self._store.validate_native_manifest(manifest)
            if (canonical is None or data.get('manifest_digest') != hashlib.sha256(canonical.encode()).hexdigest()
                    or data.get('header_digest') != self._store._native_header_digest(manifest)):
                return '409 Conflict', '{"error":"native_manifest_mismatch"}'
            row = self._store.begin_native_media_chunks(sender, data.get('message_id'), owner, sender,
                data.get('media_digest'), manifest, incoming=True)
        elif operation in ('part', 'status'):
            row = self._store.get_native_media(sender, data.get('message_id'))
            if (not row or row['transport'] != 'chunks' or not row['incoming'] or row['to_webid'] != owner
                    or row['status'] == 'blocked' or row['manifest_digest'] != data.get('manifest_digest')
                    or row['header_digest'] != data.get('header_digest') or row['media_digest'] != data.get('media_digest')):
                return '200 OK', '{"status":"received"}'
            if operation == 'part':
                raw = self._native_part_bytes(data.get('data_b64'))
                row = self._store.put_native_media_part(sender, data.get('message_id'), data.get('manifest_digest'), data.get('seq'), raw) if raw else None
        else:
            return '400 Bad Request', '{"error":"native_chunk_operation"}'
        if not row:
            return '409 Conflict', '{"error":"native_part_not_accepted"}'
        return self._native_response(data, 'native_media_chunk_receipt', **self._native_chunk_result(row, after=after))

    @staticmethod
    def _native_chunk_proof_valid(proof, row):
        missing = proof.get('missing') if isinstance(proof, dict) else None
        return (isinstance(proof, dict) and type(proof.get('custody_complete')) is bool
                and type(proof.get('header_committed')) is bool and type(proof.get('committed')) is bool
                and (not proof['committed'] or proof['header_committed'])
                and (not proof['header_committed'] or proof['custody_complete'])
                and proof.get('recipient_pub') == row['recipient_pub'] and isinstance(missing, list)
                and len(missing) <= 8 and all(type(seq) is int and 0 <= seq < row['chunk_count'] for seq in missing)
                and len(set(missing)) == len(missing)
                and (not proof['custody_complete'] or not missing))

    async def _relay_native_chunks(self, target, row):
        if row['transport'] != 'chunks' or not row['custody_complete'] or not row['event']:
            return None
        from .command_validation import SEALED_KEYS
        manifest = {key: row['event'][key] for key in (*SEALED_KEYS, 'size', 'cipher_digest', 'chunk_count', 'recipient_pub')}
        base = {'content_type': 'native_media_chunks', 'native_chunks_v': 1, 'message_id': row['message_id'],
            'media_digest': row['media_digest'], 'header_digest': row['header_digest'], 'manifest_digest': row['manifest_digest']}
        proof = await self._native_exchange(target, row['to_webid'], {**base, 'operation': 'begin', 'manifest': manifest}, 'native_media_chunk_receipt')
        if not self._native_chunk_proof_valid(proof, row):
            return None
        # One attempt is finite (at most 400 original parts), reads one bounded
        # part at a time and stops on refusal/shutdown/policy change. No custody
        # bytes are released until the peer's complete signed proof arrives.
        sent = set()
        while not proof['custody_complete']:
            if not proof['missing'] or not self._native_storage_writable():
                return None
            for seq in proof['missing']:
                if seq in sent:
                    return None
                part = self._store.get_native_media_part(row['from_webid'], row['message_id'], row['manifest_digest'], seq)
                if not part:
                    return None
                sent.add(seq)
                result = await self._native_exchange(target, row['to_webid'], {**base, 'operation': 'part',
                    'seq': seq, 'data_b64': base64.b64encode(part['data']).decode()}, 'native_media_chunk_receipt')
                if not self._native_chunk_proof_valid(result, row):
                    return None
                proof = result
                if proof['custody_complete']:
                    break
        return proof
