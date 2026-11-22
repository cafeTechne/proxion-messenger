import { describe, it, expect, vi } from 'vitest';
import { createNativeChunkUploader } from './native-media-chunks-send.js';
import { nativeMediaSendIdentity } from './native-media-send.js';
import { dmEnvelopeFingerprint } from './dm-receipts.js';

async function fixture(size = 150000) {
    let scope = 'account', generation = 0, pub = 'A'.repeat(43), clock = 0;
    const row = { scope, id: 'file', peer: 'did:key:bob', peerPub: pub, stage: 'sealed', receiveHold: true,
        view: { id: 'thread' }, prepared: { ct: new Uint8Array(size).fill(42) },
        hdr: { e2e: true, e2e_v: 1, content: 'sealed', nonce: 'nonce', ratchet_pub: pub, msg_num: 0, pn: 0 } };
    const identity = await nativeMediaSendIdentity(row), commands = [], progress = [], states = [];
    const options = { send: vi.fn(async command => { commands.push(command); return true; }),
        getScope: () => scope, getGeneration: () => generation, isActive: () => true, getPeerPub: () => pub,
        onProgress: vi.fn(async event => { progress.push(event); }), onState: (...args) => states.push(args), now: () => clock };
    const engine = () => createNativeChunkUploader(options), uploader = engine();
    async function ack(missing, extra = {}) {
        const manifest = commands.find(command => command.cmd === 'native_media_begin').manifest;
        const last = commands.at(-1);
        return { type: 'native_media_parts', v: 1, message_id: row.id, media_digest: identity.mediaDigest,
            header_digest: identity.headerDigest, manifest_digest: await dmEnvelopeFingerprint(manifest),
            recipient_pub: row.peerPub, custody_complete: !missing.length, header_committed: false, committed: false,
            missing, ...(last.cmd === 'native_media_part' ? { seq: last.seq } : {}), ...extra };
    }
    return { row, identity, uploader, engine, commands, options, progress, states, ack,
        advance: value => { clock += value; }, scope: value => { scope = value; },
        generation: () => { generation++; }, pub: value => { pub = value; } };
}

describe('durable native chunk upload', () => {
    it('sends only gateway-requested bounded parts and distinguishes custody from full receive commit', async () => {
        const h = await fixture();
        await h.uploader.start(h.row, h.identity);
        expect(h.commands).toHaveLength(1);
        const manifest = h.commands[0].manifest;
        expect(manifest.size).toBe(h.row.prepared.ct.length);
        expect(manifest.recipient_pub).toBe(h.row.peerPub);
        for (const missing of [[0, 1, 2], [1, 2], [2], []]) expect(await h.uploader.status(await h.ack(missing))).toBe(true);
        const parts = h.commands.filter(command => command.cmd === 'native_media_part');
        expect(parts.map(command => command.seq)).toEqual([0, 1, 2]);
        const reconstructed = parts.map(command => Buffer.from(command.data_b64, 'base64'));
        expect(new Uint8Array(Buffer.concat(reconstructed))).toEqual(h.row.prepared.ct);
        expect(h.progress.every(event => event.status === 'pending' && !event.header_committed)).toBe(true);
        expect(h.row.stage).toBe('sealed');
    });
    it('restarts with the exact manifest and uploads only still-missing gateway parts', async () => {
        const h = await fixture(); await h.uploader.start(h.row, h.identity);
        await h.uploader.status(await h.ack([0, 1, 2]));
        const original = structuredClone(h.commands[0]); h.uploader.stop();
        const restarted = h.engine(); await restarted.start(h.row, h.identity);
        expect(h.commands.at(-1)).toEqual(original);
        await restarted.status(await h.ack([1, 2]));
        expect(h.commands.at(-1).seq).toBe(1);
    });
    it('retries lost begin and part acknowledgements after a deadline using the same identity', async () => {
        const h = await fixture(); await h.uploader.start(h.row, h.identity);
        await h.uploader.start(h.row, h.identity); expect(h.commands).toHaveLength(1);
        h.advance(15001); await h.uploader.start(h.row, h.identity);
        expect(h.commands[1]).toEqual(h.commands[0]);
        await h.uploader.status(await h.ack([0, 1, 2]));
        h.advance(15001); await h.uploader.start(h.row, h.identity);
        expect(h.commands.at(-1)).toEqual(h.commands[0]);
        await h.uploader.status(await h.ack([1, 2])); expect(h.commands.at(-1).seq).toBe(1);
    });
    it('ignores foreign, malformed, uncorrelated and impossible acknowledgements', async () => {
        const h = await fixture(); await h.uploader.start(h.row, h.identity);
        for (const patch of [{ message_id: 'other' }, { media_digest: 'wrong' }, { header_digest: 'wrong' },
            { manifest_digest: 'wrong' }, { recipient_pub: 'B'.repeat(43) }, { missing: [true] },
            { missing: [0, 0] }, { missing: [-1] }, { missing: [3] }, { missing: [] },
            { committed: true }, { header_committed: true }, { custody_complete: 1 }, { seq: 0 }]) {
            expect(await h.uploader.status(await h.ack([0, 1, 2], patch))).toBe(false);
        }
        expect(h.progress).toHaveLength(0); expect(h.commands).toHaveLength(1);
        await h.uploader.status(await h.ack([0, 1, 2]));
        expect(await h.uploader.status(await h.ack([1, 2], { seq: 2 }))).toBe(false);
    });
    it('drops replies and further writes after account, connection or original peer key changes', async () => {
        for (const invalidate of [h => h.scope('other'), h => h.generation(), h => h.pub('B'.repeat(43)), h => h.uploader.stop()]) {
            const h = await fixture(); await h.uploader.start(h.row, h.identity);
            const event = await h.ack([0, 1, 2]); invalidate(h);
            expect(await h.uploader.status(event)).toBe(false); expect(h.commands).toHaveLength(1);
        }
    });
    it('retries a refused direct write without waiting for a nonexistent custody acknowledgement', async () => {
        const h = await fixture(); h.options.send.mockResolvedValueOnce(false);
        expect(await h.uploader.start(h.row, h.identity)).toBe(false);
        expect(await h.uploader.start(h.row, h.identity)).toBe(true); expect(h.commands).toHaveLength(1);
    });
    it('coalesces simultaneous starts and reserves the upload count before asynchronous hashing', async () => {
        const h = await fixture(32);
        expect(await Promise.all([h.uploader.start(h.row, h.identity), h.uploader.start(h.row, h.identity)])).toEqual([true, true]);
        expect(h.commands).toHaveLength(1);
        h.uploader.stop(); h.commands.length = 0;
        const accepted = await Promise.all(Array.from({ length: 11 }, (_, index) => h.uploader.start({ ...h.row, id: 'file-' + index }, h.identity)));
        expect(accepted.filter(Boolean)).toHaveLength(10); expect(h.commands).toHaveLength(10);
        h.generation();
        expect(await h.uploader.start({ ...h.row, id: 'new-connection' }, h.identity)).toBe(true);
    });

});
