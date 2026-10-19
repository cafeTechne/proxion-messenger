// Strings that used to be hardcoded English go through t(): load the real
// locale files (pseudo-locale qps, so any English left over stands out) and
// render the surfaces. Also pins the per-key English fallback that the draft
// locales rely on while they lag en.json.
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { initI18n, t, tn, bidiIsolate } from './i18n.js';
const FSI = '\u2068';
const PDI = '\u2069';
import { seenByText } from './send-status.js';
import { createFriendRequests } from './friend-requests.js';
import { createMembers } from './members.js';
import { createRooms } from './rooms.js';
import { presenceLabel } from './util.js';

const localeFile = (code) => fileURLToPath(new URL(`./locales/${code}.json`, import.meta.url));
const EN = JSON.parse(readFileSync(localeFile('en'), 'utf8'));

async function useLocale(code, override) {
    globalThis.localStorage = { getItem: (k) => (k === 'proxion_locale' ? code : null), setItem: () => {} };
    globalThis.fetch = async (url) => {
        const name = String(url).match(/locales\/([^/]+)\.json$/)[1];
        const data = override && name === code ? override : JSON.parse(readFileSync(localeFile(name), 'utf8'));
        return { ok: true, json: async () => structuredClone(data) };
    };
    await initI18n();
}

afterEach(() => { delete globalThis.fetch; delete globalThis.localStorage; });

describe('per-key English fallback for draft locales', () => {
    it('a key missing from the active locale renders its English text', async () => {
        await useLocale('de', { 'sidebar.rooms': 'Räume' });
        expect(t('sidebar.rooms')).toBe('Räume');
        expect(t('btn.scanQr')).toBe(EN['btn.scanQr']);
    });

    it('a plural category missing from the locale falls back too', async () => {
        await useLocale('ar', {});
        // Arabic selects "few" for 3; en has no .few, so .other is used.
        expect(tn('sidebar.unreadCount', 3)).toBe('3 unread messages');
    });

    it('every en key resolves to real text in every shipped locale', async () => {
        for (const code of ['de', 'fr', 'es', 'ar', 'ja', 'ru']) {
            await useLocale(code);
            for (const key of Object.keys(EN)) {
                if (key === '_meta') continue;
                expect(t(key), `${code}:${key}`).not.toBe(key);
            }
        }
    });
});

describe('hardcoded English now translated', () => {
    beforeEach(async () => { await useLocale('qps'); });

    it('"Seen by" is translated and does not HTML-escape a textContent sink', async () => {
        await useLocale('en');
        const text = seenByText([{ receiver_webid: 'did:key:zA', display_name: 'A&B' },
            { receiver_webid: 'did:key:zSelf', display_name: 'Me' }], 'did:key:zSelf');
        // The names are wrapped in bidi isolates (see the bidi tests below).
        expect(text).toBe(`Seen by ${FSI}A&B${PDI}`);
        const many = seenByText(['a', 'b', 'c', 'd', 'e'].map(n => ({ receiver_webid: n, display_name: n })), 'x');
        expect(many).toBe(`Seen by ${FSI}a, b, c${PDI} and 2 more`);
        expect(seenByText([{ receiver_webid: 'me' }], 'me')).toBe('');
    });

    it('the friend request card', () => {
        const list = { style: {}, children: [], appendChild(c) { this.children.push(c); } };
        globalThis.document = {
            getElementById: (id) => (id === 'friend-request-list' ? list : null),
            createElement: () => ({ dataset: {}, style: {} }),
        };
        globalThis.WebSocket = { OPEN: 1 };
        const fr = createFriendRequests({ getSocket: () => null });
        fr.renderPendingInvite({ invitation_id: 'i1', from_did: 'did:key:zA', display_name: 'سارة' });
        fr.renderPendingInvite({ invitation_id: 'i2', from_did: 'did:key:zB' });
        const [named, anon] = list.children.map(li => li.innerHTML);
        for (const english of ['From ', 'claimed name', '>Accept<', '>Ignore<']) expect(named).not.toContain(english);
        expect(named).toContain(`<b><bdi>سارة</bdi></b>`);
        expect(anon).not.toContain('Friend request');
        expect(anon).toContain(t('fr.title'));
    });

    it('member presence (title and screen-reader text)', () => {
        const el = { style: {}, innerHTML: '' };
        globalThis.document = { getElementById: (id) => (id === 'members-list' ? el : null) };
        globalThis.window = { innerWidth: 1200 };
        createMembers({ getActiveView: () => null, requestRoomMembers: () => {} }).renderMembersPanel([
            { webid: 'did:key:zC', display_name: 'Carol', status: 'online', federated: true, gateway: 'gw' },
        ]);
        expect(el.innerHTML).not.toMatch(/, online|title="online"|, federated|Federated member/);
        expect(el.innerHTML).toContain(presenceLabel('online'));
        expect(presenceLabel('')).toBe('');
        expect(presenceLabel('<b>')).toBe('');
    });

    it('the leave-room confirm', () => {
        globalThis.document = { getElementById: () => ({ style: {} }) };
        globalThis.WebSocket = { OPEN: 1 };
        const msgs = [];
        for (const owner of [true, false]) {
            createRooms({
                getSocket: () => ({ readyState: 1, send: () => {} }),
                getActiveView: () => ({ type: 'local_room', id: 'r1' }),
                getRoomCreatorOf: () => new Set(owner ? ['r1'] : []),
                getRoomInviteUrls: () => ({}),
                showConfirm: (msg) => msgs.push(msg),
                showCopyModal: () => {},
            }).leaveRoom();
        }
        expect(msgs).toEqual([t('confirm.leaveRoom.ownerBody'), t('confirm.leaveRoom.body')]);
        for (const m of msgs) expect(m).not.toMatch(/Leave this room/);
    });
});

describe('bidi isolation of user-supplied text', () => {
    it('isolates names interpolated into translated strings, not counts', async () => {
        await useLocale('en');
        expect(t('msg.seenByMore', { names: 'محمد', count: 2 })).toBe(`Seen by ${FSI}محمد${PDI} and 2 more`);
        expect(tn('sidebar.unreadCount', 3)).toBe('3 unread messages');
        expect(bidiIsolate('')).toBe('');
    });
});
