// webdm.js message requests (desktop client): a verified drop from someone who
// is not a contact waits in the inbox, undecrypted, until the user accepts.
import { describe, it, expect, vi } from 'vitest';
import { createWebDm } from './webdm.js';

const BOB = 'https://pod.example/bob/profile/card#me';

function harness({ drops, verify = 'verified', known = () => false, strictSender = false, blocked = () => false }) {
    const events = [], deleted = [], requests = [], notices = [];
    const e2e = {
        cachePeerPub: vi.fn(),
        ratchetDecrypt: vi.fn(async (from, ct, n, m, rp, pn, opts) => {
            if (!opts.allowInit) { const e = new Error('no session'); e.name = 'E2ENoSessionError'; throw e; }
            return 'plain:' + ct;
        }),
        hasSession: vi.fn(async () => false),
    };
    const dm = createWebDm({
        pod: {
            podReadDmDrops: async () => drops,
            podDeleteDmDrop: async (u) => { deleted.push(u); return true; },
        },
        e2e, handleEvent: (e) => events.push(e),
        getSelfWebId: () => 'https://pod.example/me/profile/card#me',
        verifySender: async () => verify,
        isBlocked: blocked,
        isKnownSender: known,
        onMessageRequest: (r) => requests.push(r),
        onUnverifiedDrop: (n) => notices.push(n),
        strictSender,
    });
    return { dm, e2e, events, deleted, requests, notices };
}

const env = (extra = {}) => ({ from_webid: BOB, from_display_name: 'Bob', message_id: 'm1', content: 'CT', e2e: true, nonce: 'n', msg_num: 0, ratchet_pub: 'r', signer: 'did:key:z', sig: 's', ...extra });

describe('createWebDm message requests', () => {
    it('holds a verified drop from an unknown sender without decrypting or deleting it', async () => {
        const h = harness({ drops: [{ url: 'u1', envelope: env() }] });
        await h.dm.drainOnce();
        expect(h.requests).toEqual([{ from_webid: BOB, from_display_name: 'Bob', message_id: 'm1' }]);
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
        expect(h.deleted).toEqual([]);
        expect(h.events).toEqual([]);
    });

    it('opens it once the sender is a contact', async () => {
        const h = harness({ drops: [{ url: 'u1', envelope: env() }], known: () => true });
        await h.dm.drainOnce();
        expect(h.requests).toEqual([]);
        expect(h.events[0]).toMatchObject({ content: 'plain:CT', sender_verified: true });
        expect(h.deleted).toEqual(['u1']);
    });

    it('never turns an unverified drop into a request; it is refused unopened', async () => {
        const h = harness({ drops: [{ url: 'u1', envelope: env({ sig: undefined }) }], verify: 'rejected' });
        await h.dm.drainOnce();
        expect(h.requests).toEqual([]);
        expect(h.notices).toHaveLength(1);
        expect(h.deleted).toEqual(['u1']);
    });

    it('a blocked sender is deleted unread, never a request', async () => {
        const h = harness({ drops: [{ url: 'u1', envelope: env() }], blocked: (w) => w === BOB });
        await h.dm.drainOnce();
        expect(h.requests).toEqual([]);
        expect(h.deleted).toEqual(['u1']);
    });

    it('strictSender refuses an unverified unencrypted drop from a contact instead of showing it', async () => {
        const h = harness({ drops: [{ url: 'u1', envelope: env({ e2e: false, content: 'forged', sig: undefined }) }],
            verify: 'rejected', known: () => true, strictSender: true });
        await h.dm.drainOnce();
        expect(h.events).toEqual([]);
        expect(h.notices).toEqual([{ from_webid: BOB, message_id: 'm1' }]);
        expect(h.deleted).toEqual(['u1']);
    });
});
