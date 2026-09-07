// longchat.test.js — PLAN_ROUND_67 phases B/C: the pure Long Chat helpers.
// No pod, no network: serialization, escaping, date partitioning, parsing.
import { describe, it, expect } from 'vitest';
import {
    NS, P,
    escapeTurtleLiteral, iriRef, dayPath,
    chatIndexUrl, chatChannelIri, chatDayUrl, messageIriFor,
    chatRootUrl, roomIdFromChatContainer,
    buildIndexTurtle, buildAppendPatch, parseLongChatJsonLd, mergeLongChatMessages,
    reactionActionTriples, reactionCancelTriples, parseReactionActions, verifyReactionActions,
} from './longchat.js';
import { signLongChatReaction, signLongChatCancel } from './dmsig.js';

describe('roomIdFromChatContainer (inverse of chatRootUrl)', () => {
    it('round-trips a room id, including one needing url-encoding', () => {
        for (const id of ['general', 'room-abc123', 'a b/c']) {
            expect(roomIdFromChatContainer(chatRootUrl('https://me.pod/', id))).toBe(id);
        }
    });
    it('returns null for a non-chat container', () => {
        expect(roomIdFromChatContainer('https://me.pod/contacts/')).toBe(null);
        expect(roomIdFromChatContainer('')).toBe(null);
    });
});

const ROOT = 'https://alice.pod.example/';
const ALICE = 'https://alice.pod.example/profile/card#me';

describe('escapeTurtleLiteral (RDF injection guard)', () => {
    it('escapes quotes and backslashes', () => {
        expect(escapeTurtleLiteral('he said "hi"')).toBe('he said \\"hi\\"');
        expect(escapeTurtleLiteral('back\\slash')).toBe('back\\\\slash');
    });

    it('escapes newlines, returns and tabs instead of emitting them raw', () => {
        expect(escapeTurtleLiteral('a\nb')).toBe('a\\nb');
        expect(escapeTurtleLiteral('a\r\nb')).toBe('a\\r\\nb');
        expect(escapeTurtleLiteral('a\tb')).toBe('a\\tb');
    });

    it('strips control characters Turtle forbids raw in a literal', () => {
        const withNull = `a${String.fromCharCode(0)}b${String.fromCharCode(7)}c`;
        expect(escapeTurtleLiteral(withNull)).toBe('abc');
    });

    it('neutralises an attempt to close the literal and append triples', () => {
        // A peer sending this as message text is trying to write their own
        // statements into the recipient's pod.
        const attack = '" . <https://evil.example/#e> <http://x/p> "pwned';
        const escaped = escapeTurtleLiteral(attack);
        // No unescaped quote survives, so the literal cannot be terminated.
        expect(/(^|[^\\])"/.test(escaped)).toBe(false);
    });

    it('handles null and undefined', () => {
        expect(escapeTurtleLiteral(null)).toBe('');
        expect(escapeTurtleLiteral(undefined)).toBe('');
    });
});

describe('iriRef (IRI breakout guard)', () => {
    it('wraps a normal IRI', () => {
        expect(iriRef(ALICE)).toBe(`<${ALICE}>`);
    });
    it('strips characters that would terminate or inject inside an IRI', () => {
        const out = iriRef('https://e.example/#x> <p> "v" .');
        expect(out.startsWith('<')).toBe(true);
        expect(out.endsWith('>')).toBe(true);
        expect(out.slice(1, -1)).not.toMatch(/[<>"\s]/);
    });
});

describe('dayPath uses UTC, not local time', () => {
    it('partitions by the UTC date of the message', () => {
        expect(dayPath('2026-07-22T10:00:00Z')).toBe('2026/07/22');
        // 23:30 UTC is still the 22nd in UTC even where local time has rolled over.
        expect(dayPath('2026-07-22T23:30:00Z')).toBe('2026/07/22');
        // 00:30 UTC is the 23rd even where local time is still the 22nd.
        expect(dayPath('2026-07-23T00:30:00Z')).toBe('2026/07/23');
    });
    it('zero-pads month and day', () => {
        expect(dayPath('2026-01-05T00:00:00Z')).toBe('2026/01/05');
    });
    it('falls back to now for an unparseable date instead of emitting NaN', () => {
        expect(dayPath('not-a-date')).toMatch(/^\d{4}\/\d{2}\/\d{2}$/);
    });
});

describe('paths', () => {
    it('builds the channel, index and day-file URLs', () => {
        expect(chatIndexUrl(ROOT, 'general')).toBe(`${ROOT}proxion/rooms/general/index.ttl`);
        expect(chatChannelIri(ROOT, 'general')).toBe(`${ROOT}proxion/rooms/general/index.ttl#this`);
        expect(chatDayUrl(ROOT, 'general', '2026-07-22T10:00:00Z'))
            .toBe(`${ROOT}proxion/rooms/general/2026/07/22/chat.ttl`);
        expect(messageIriFor(ROOT, 'general', 'm-1', '2026-07-22T10:00:00Z'))
            .toBe(`${ROOT}proxion/rooms/general/2026/07/22/chat.ttl#m-1`);
    });
    it('escapes a room id that would otherwise alter the path', () => {
        expect(chatIndexUrl(ROOT, '../../etc')).not.toContain('../../etc');
    });
});

describe('buildIndexTurtle', () => {
    it('declares the channel as meeting:LongChat with a title', () => {
        const ttl = buildIndexTurtle('Design chat');
        expect(ttl).toContain('a meeting:LongChat');
        expect(ttl).toContain(`@prefix meeting: <${NS.meeting}>.`);
        // Titles use Dublin Core ELEMENTS, distinct from the TERMS used on messages.
        expect(ttl).toContain(`@prefix dc: <${NS.dc}>.`);
        expect(ttl).toContain('dc:title "Design chat"');
        expect(NS.dc).not.toBe(NS.dct);
    });
    it('escapes a hostile room title', () => {
        const ttl = buildIndexTurtle('evil" . <a> <b> "c');
        expect(ttl).toContain('\\"');
    });
});

describe('buildAppendPatch', () => {
    const base = {
        channelIri: `${ROOT}proxion/rooms/general/index.ttl#this`,
        messageIri: `${ROOT}proxion/rooms/general/2026/07/22/chat.ttl#m1`,
        content: 'Hello world!',
        createdIso: '2026-07-22T10:00:00Z',
        makerIri: ALICE,
    };

    it('emits SPARQL INSERT DATA with the standard triples', () => {
        const body = buildAppendPatch(base);
        expect(body.startsWith('INSERT DATA {')).toBe(true);
        expect(body).toContain(`<${P.message}>`);
        expect(body).toContain(`<${P.content}>`);
        expect(body).toContain(`<${P.created}>`);
        expect(body).toContain(`<${P.maker}>`);
        expect(body).toContain(`"2026-07-22T10:00:00Z"^^<${P.dateTime}>`);
    });

    it('links the message to the channel with BOTH wf:message and meeting:message', () => {
        // wf:message is what the SolidOS databrowser enumerates on; meeting:message
        // is the written spec. A chat carrying only one is invisible to half the
        // ecosystem, verified live against the real databrowser.
        const body = buildAppendPatch(base);
        expect(body).toContain(`<${P.wfMessage}>`);
        expect(body).toContain(`<${P.message}>`);
        expect(P.wfMessage).toBe('http://www.w3.org/2005/01/wf/flow#message');
    });

    it('omits foaf:maker when the author has no WebID rather than emitting an empty IRI', () => {
        const body = buildAppendPatch({ ...base, makerIri: '' });
        expect(body).not.toContain(P.maker);
        expect(body).toContain(`<${P.content}>`);
    });

    it('links a reply to its parent with sioc:has_reply when replyToIri is given (R101.1)', () => {
        const parent = `${ROOT}proxion/rooms/general/2026/07/21/chat.ttl#p0`;
        const body = buildAppendPatch({ ...base, replyToIri: parent });
        expect(body).toContain(`<${parent}> <${P.hasReply}> <${base.messageIri}> .`);
        expect(P.hasReply).toBe('http://rdfs.org/sioc/ns#has_reply');
        // absent when not a reply
        expect(buildAppendPatch(base)).not.toContain(P.hasReply);
    });

    it('reactionActionTriples emits a schema:LikeAction targeting the message (R101)', () => {
        const t = reactionActionTriples({
            actionIri: `${base.messageIri}-react`,
            msgIri: base.messageIri,
            agentIri: ALICE,
            emoji: '👍',
        });
        const joined = t.join('\n');
        expect(joined).toContain(`a <${P.likeAction}> .`);
        expect(joined).toContain(`<${P.target}> <${base.messageIri}> .`);
        expect(joined).toContain(`<${P.agent}> <${ALICE}> .`);
        expect(joined).toContain(`<${P.content}> "👍" .`);
        expect(P.likeAction).toBe('http://schema.org/LikeAction');
        // agent omitted when unknown
        expect(reactionActionTriples({ actionIri: 'x', msgIri: 'y', emoji: 'z' }).join('\n'))
            .not.toContain(P.agent);
    });

    it('an injection attempt in the message text yields no extra triples', () => {
        const body = buildAppendPatch({
            ...base,
            content: '" . <https://evil.example/#e> <http://evil/p> "owned',
        });
        // Exactly the triples we intended: two channel links (wf + meeting) plus
        // created + content + maker. The payload characters DO still appear, but
        // as inert text inside an escaped literal, so this is structural rather
        // than substring-based.
        const statements = body.split('\n').filter(l => l.trim().endsWith(' .'));
        expect(statements).toHaveLength(5);
        // The quote that would have closed the literal is escaped.
        const contentLine = statements.find(l => l.includes(P.content));
        expect(contentLine).toContain('\\"');
        // and the injected IRI never reaches subject position in any statement.
        for (const s of statements) {
            expect(s.trim().startsWith('<https://evil.example/#e>')).toBe(false);
        }
    });
});

describe('container addressing (shared chats in any pod)', () => {
    it('addresses a chat by its container URL, not just our own roomId path', async () => {
        const { dayFileAt, channelIriAt, indexUrlAt, messageIriAt } = await import('./longchat.js');
        const c = 'https://alice.pod.example/OurChat/';
        expect(indexUrlAt(c)).toBe('https://alice.pod.example/OurChat/index.ttl');
        expect(channelIriAt(c)).toBe('https://alice.pod.example/OurChat/index.ttl#this');
        expect(dayFileAt(c, '2026-07-22T10:00:00Z')).toBe('https://alice.pod.example/OurChat/2026/07/22/chat.ttl');
        expect(messageIriAt(c, 'm1', '2026-07-22T10:00:00Z')).toBe('https://alice.pod.example/OurChat/2026/07/22/chat.ttl#m1');
    });
    it('the roomId helpers are consistent with the container helpers', async () => {
        const { chatDayUrl, dayFileAt, chatRootUrl } = await import('./longchat.js');
        const root = 'https://me.pod/'; const room = 'general'; const d = '2026-07-22T00:00:00Z';
        expect(chatDayUrl(root, room, d)).toBe(dayFileAt(chatRootUrl(root, room), d));
    });
});

describe('buildChatAcl (participant append grant, #4)', () => {
    const OWNER = 'https://alice.pod/profile/card#me';
    const BOB = 'https://bob.pod/profile/card#me';
    const CONTAINER = 'https://alice.pod/OurChat/';

    it('gives the owner control and each participant read/append (not write)', async () => {
        const { buildChatAcl } = await import('./longchat.js');
        const acl = buildChatAcl(OWNER, [BOB], CONTAINER);
        expect(acl).toContain(`acl:agent <${OWNER}>`);
        expect(acl).toContain('acl:Read, acl:Write, acl:Control');   // owner keeps full control
        expect(acl).toContain(`acl:agent <${BOB}>`);
        expect(acl).toContain('acl:Read, acl:Append');               // participant can POST, not overwrite
        // #4: a member never gets Write; only the owner stanza carries it.
        const memberSection = acl.split('#participant0')[1] || '';
        expect(memberSection).not.toContain('acl:Write');
        expect(acl).toContain(`acl:default <${CONTAINER}>`);         // propagates to day files
    });
    it('does not grant the owner a second (participant) stanza, or dedupe wrongly', async () => {
        const { buildChatAcl } = await import('./longchat.js');
        const acl = buildChatAcl(OWNER, [OWNER, BOB, BOB], CONTAINER);
        // owner filtered out of participants; bob appears once
        expect((acl.match(/acl:Read, acl:Append\./g) || []).length).toBe(1);
    });
    it('a hostile WebID cannot inject an extra authorization', async () => {
        const { buildChatAcl } = await import('./longchat.js');
        const evil = 'https://e.pod/#me> acl:mode acl:Control . <#x';
        const acl = buildChatAcl(OWNER, [evil], CONTAINER);
        // Structural, not substring-based: the injected `> ... <` has its spaces
        // and angle brackets stripped by iriRef, so "acl:Control" survives only as
        // inert text INSIDE one mangled IRI, forming no new triple. Proof: exactly
        // two authorization stanzas (owner + the one participant), and the evil
        // agent IRI contains no space or bracket it could break out with.
        const stanzas = (acl.match(/a acl:Authorization;/g) || []).length;
        expect(stanzas).toBe(2);
        const agentLine = acl.split('\n').find(l => l.includes('e.pod') && l.includes('acl:agent'));
        expect(agentLine).toBeTruthy();
        const iri = agentLine.match(/<([^>]*)>/)[1];
        expect(iri).not.toMatch(/[\s<>]/);   // nothing left to break out of the IRI
    });
});

describe('parseLongChatJsonLd', () => {
    const msgNode = (id, content, created, maker) => ({
        '@id': id,
        [P.content]: [{ '@value': content }],
        [P.created]: [{ '@value': created, '@type': P.dateTime }],
        ...(maker ? { [P.maker]: [{ '@id': maker }] } : {}),
    });

    it('extracts messages and ignores the channel node', () => {
        const doc = [
            msgNode('https://p/chat.ttl#m1', 'hello', '2026-07-22T10:00:00Z', ALICE),
            { '@id': 'https://p/index.ttl#this', [P.message]: [{ '@id': 'https://p/chat.ttl#m1' }] },
        ];
        const out = parseLongChatJsonLd(doc, 'general');
        expect(out).toHaveLength(1);
        expect(out[0]).toMatchObject({
            message_id: 'm1', content: 'hello', from_webid: ALICE,
            thread_id: 'general', source: 'longchat',
        });
    });

    it('sorts by timestamp, oldest first', () => {
        const out = parseLongChatJsonLd([
            msgNode('https://p/c#b', 'second', '2026-07-22T11:00:00Z'),
            msgNode('https://p/c#a', 'first', '2026-07-22T10:00:00Z'),
        ]);
        expect(out.map(m => m.content)).toEqual(['first', 'second']);
    });

    it('accepts @graph, bare object and array shapes', () => {
        const n = msgNode('https://p/c#m', 'x', '2026-07-22T10:00:00Z');
        expect(parseLongChatJsonLd({ '@graph': [n] })).toHaveLength(1);
        expect(parseLongChatJsonLd(n)).toHaveLength(1);
        expect(parseLongChatJsonLd([n])).toHaveLength(1);
    });

    it('tolerates scalar (non-array) predicate values', () => {
        const out = parseLongChatJsonLd([{
            '@id': 'https://p/c#m',
            [P.content]: 'plain string',
            [P.created]: '2026-07-22T10:00:00Z',
        }]);
        expect(out[0].content).toBe('plain string');
        expect(out[0].timestamp).toBe('2026-07-22T10:00:00Z');
    });

    it('is empty for junk input rather than throwing', () => {
        expect(parseLongChatJsonLd(null)).toEqual([]);
        expect(parseLongChatJsonLd([null, 'nonsense', {}])).toEqual([]);
    });

    it('caps the number of @graph nodes it walks (foreign day file)', () => {
        const nodes = Array.from({ length: 6000 }, (_, i) =>
            msgNode(`https://p/c#m${i}`, `x${i}`, '2026-07-22T10:00:00Z'));
        const out = parseLongChatJsonLd(nodes);
        expect(out.length).toBeLessThanOrEqual(5000);
    });

    it('defaults the extras that only Proxion writes', () => {
        const out = parseLongChatJsonLd([msgNode('https://p/c#m', 'x', '2026-07-22T10:00:00Z')]);
        expect(out[0].content_type).toBe('text');
        expect(out[0].from_display_name).toBe('');
    });
});

describe('reactions read append-only (#4)', () => {
    const MSG = 'https://p/c/chat.ttl#m1';
    const ACTION = 'https://p/c/chat.ttl#react-m1-alice-👍';
    const action = (extra = {}) => ({
        '@id': ACTION,
        '@type': [P.likeAction],
        [P.target]: [{ '@id': MSG }],
        [P.content]: [{ '@value': '👍' }],
        [P.agent]: [{ '@id': ALICE }],
        ...extra,
    });

    it('reactionCancelTriples tombstones the action instead of deleting it', () => {
        const [triple] = reactionCancelTriples({ actionIri: ACTION, canceledIso: '2026-08-08T11:00:00Z' });
        expect(triple).toContain(`<${ACTION}> <${P.dateDeleted}>`);
        expect(triple).toContain(`"2026-08-08T11:00:00Z"^^<${P.dateTime}>`);
        // Append-only: a cancel is a plain INSERT triple, never a DELETE.
        expect(triple).not.toContain('DELETE');
    });

    it('reactionActionTriples / reactionCancelTriples carry the proofs when supplied', () => {
        const add = reactionActionTriples({ actionIri: ACTION, msgIri: MSG, agentIri: ALICE, emoji: '👍', proof: 'did:key:zA|R' });
        expect(add.some(t => t.includes(`<${P.reactProof}> "did:key:zA|R"`))).toBe(true);
        const cancel = reactionCancelTriples({ actionIri: ACTION, canceledIso: '2026-08-08T11:00:00Z', proof: 'did:key:zA|C' });
        expect(cancel.some(t => t.includes(`<${P.cancelProof}> "did:key:zA|C"`))).toBe(true);
    });

    it('parseReactionActions returns the candidate (unverified, agent not yet trusted)', () => {
        const out = parseReactionActions([action()]);
        expect(out).toHaveLength(1);
        expect(out[0]).toMatchObject({ target_iri: MSG, emoji: '👍', agent: ALICE, verified: false });
    });

    it('parseReactionActions no longer excludes a tombstoned action on the raw tombstone', () => {
        // A raw dateDeleted is unauthenticated, so a forged cancel must not silently
        // hide a reaction: parse returns the candidate with canceled_at extracted,
        // and verifyReactionActions decides whether the cancel is genuine.
        const out = parseReactionActions([
            action({ [P.dateDeleted]: [{ '@value': '2026-08-08T11:00:00Z', '@type': P.dateTime }] }),
        ]);
        expect(out).toHaveLength(1);
        expect(out[0].canceled_at).toBe('2026-08-08T11:00:00Z');
    });

    it('a LikeAction never surfaces as a chat message (nor its tombstone)', () => {
        // The same node stream fed to the message parser: it must skip reactions by
        // type, so a reaction (or a cancelled one) is not mistaken for a message.
        const cancelled = action({ [P.dateDeleted]: [{ '@value': '2026-08-08T11:00:00Z', '@type': P.dateTime }] });
        const msgs = parseLongChatJsonLd([action(), cancelled], 'general');
        expect(msgs).toHaveLength(0);
    });
});

// ── Part C: authenticated reactions and un-reactions ─────────────────────────
describe('verifyReactionActions (#5 Part C)', () => {
    const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
    function b58encode(bytes) {
        let zeros = 0; while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
        const digits = [0];
        for (let i = zeros; i < bytes.length; i++) {
            let carry = bytes[i];
            for (let j = 0; j < digits.length; j++) { carry += digits[j] << 8; digits[j] = carry % 58; carry = (carry / 58) | 0; }
            while (carry > 0) { digits.push(carry % 58); carry = (carry / 58) | 0; }
        }
        let s = ''; for (let k = 0; k < zeros; k++) s += '1';
        for (let q = digits.length - 1; q >= 0; q--) s += B58[digits[q]];
        return s;
    }
    function didFromPub(pub) {
        const mc = new Uint8Array(2 + pub.length); mc[0] = 0xed; mc[1] = 0x01; mc.set(pub, 2);
        return 'did:key:z' + b58encode(mc);
    }
    async function makeIdentity() {
        const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
        const rawPub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
        return { priv: kp.privateKey, did: didFromPub(rawPub) };
    }

    const MSG = 'https://p/c/chat.ttl#m1';
    const BOB = 'https://bob.pod/profile/card#me';
    const CAROL = 'https://carol.pod/profile/card#me';
    const ACTION = 'https://p/c/chat.ttl#react-m1-bob-👍';

    const rootOf = (w) => `https://${new URL(w).host}/`;
    function deps(published) {
        const byRoot = {};
        for (const [w, did] of Object.entries(published)) byRoot[rootOf(w)] = { signer: did };
        return { peerPodRoot: rootOf, fetchPeerSigner: async (root) => byRoot[root] || null };
    }
    function actionNode(over = {}) {
        return {
            '@id': ACTION, '@type': [P.likeAction],
            [P.target]: [{ '@id': MSG }],
            [P.content]: [{ '@value': '👍' }],
            [P.agent]: [{ '@id': BOB }],
            ...over,
        };
    }

    it('accepts a reaction whose proof verifies for the claimed agent', async () => {
        const bob = await makeIdentity();
        const proof = await signLongChatReaction({ action: ACTION, target: MSG, agent: BOB, emoji: '👍' }, bob.priv, bob.did);
        const parsed = parseReactionActions([actionNode({ [P.reactProof]: [{ '@value': proof }] })]);
        const out = await verifyReactionActions(parsed, deps({ [BOB]: bob.did }));
        expect(out).toHaveLength(1);
        expect(out[0]).toMatchObject({ agent: BOB, emoji: '👍', verified: true });
    });

    it('drops a FORGED reaction (agent=victim, signed by the attacker)', async () => {
        const bob = await makeIdentity();       // the real Bob, published signer
        const mallory = await makeIdentity();   // attacker
        // Mallory forges a reaction claiming agent = Bob, signed with her own key.
        const proof = await signLongChatReaction({ action: ACTION, target: MSG, agent: BOB, emoji: '👍' }, mallory.priv, mallory.did);
        const parsed = parseReactionActions([actionNode({ [P.reactProof]: [{ '@value': proof }] })]);
        const out = await verifyReactionActions(parsed, deps({ [BOB]: bob.did }));
        expect(out).toHaveLength(0);            // agent field cannot be forged
    });

    it('an unsigned reaction is not trusted', async () => {
        const bob = await makeIdentity();
        const parsed = parseReactionActions([actionNode()]);   // no proof
        const out = await verifyReactionActions(parsed, deps({ [BOB]: bob.did }));
        expect(out).toHaveLength(0);
    });

    it('honours a cancel signed by the reaction agent', async () => {
        const bob = await makeIdentity();
        const rProof = await signLongChatReaction({ action: ACTION, target: MSG, agent: BOB, emoji: '👍' }, bob.priv, bob.did);
        const cIso = '2026-08-08T11:00:00Z';
        const cProof = await signLongChatCancel({ action: ACTION, canceledIso: cIso }, bob.priv, bob.did);
        const parsed = parseReactionActions([actionNode({
            [P.reactProof]: [{ '@value': rProof }],
            [P.dateDeleted]: [{ '@value': cIso, '@type': P.dateTime }],
            [P.cancelProof]: [{ '@value': cProof }],
        })]);
        const out = await verifyReactionActions(parsed, deps({ [BOB]: bob.did }));
        expect(out).toHaveLength(0);            // genuinely cancelled by its agent
    });

    it('a FORGED cancel by a non-agent does not cancel the reaction', async () => {
        const bob = await makeIdentity();
        const carol = await makeIdentity();     // not the reaction's agent
        const rProof = await signLongChatReaction({ action: ACTION, target: MSG, agent: BOB, emoji: '👍' }, bob.priv, bob.did);
        const cIso = '2026-08-08T11:00:00Z';
        // Carol signs a cancel over Bob's action; she is not the agent.
        const cProof = await signLongChatCancel({ action: ACTION, canceledIso: cIso }, carol.priv, carol.did);
        const parsed = parseReactionActions([actionNode({
            [P.reactProof]: [{ '@value': rProof }],
            [P.dateDeleted]: [{ '@value': cIso, '@type': P.dateTime }],
            [P.cancelProof]: [{ '@value': cProof }],
        })]);
        const out = await verifyReactionActions(parsed, deps({ [BOB]: bob.did, [CAROL]: carol.did }));
        expect(out).toHaveLength(1);            // forged cancel ignored, reaction stays
        expect(out[0].agent).toBe(BOB);
    });
});

describe('mergeLongChatMessages (Phase D)', () => {
    const local = (id, ts, extra = {}) => ({
        message_id: id, timestamp: ts, content: 'local ' + id, source: 'local', ...extra,
    });
    const pod = (id, ts) => ({
        message_id: id, timestamp: ts, content: 'pod ' + id, source: 'longchat',
    });

    it('interleaves pod history with local history by timestamp', () => {
        const out = mergeLongChatMessages(
            [local('b', '2026-07-22T11:00:00Z')],
            [pod('a', '2026-07-22T10:00:00Z'), pod('c', '2026-07-22T12:00:00Z')],
        );
        expect(out.map(m => m.message_id)).toEqual(['a', 'b', 'c']);
    });

    it('prefers the local copy on an id collision (it carries the richer fields)', () => {
        const out = mergeLongChatMessages(
            [local('a', '2026-07-22T10:00:00Z', { from_display_name: 'Alice' })],
            [pod('a', '2026-07-22T10:00:00Z')],
        );
        expect(out).toHaveLength(1);
        expect(out[0].source).toBe('local');
        expect(out[0].from_display_name).toBe('Alice');
    });

    it('handles either side being empty', () => {
        expect(mergeLongChatMessages([], [pod('a', '1')])).toHaveLength(1);
        expect(mergeLongChatMessages([local('a', '1')], [])).toHaveLength(1);
        expect(mergeLongChatMessages()).toEqual([]);
    });

    it('skips entries with no id rather than throwing', () => {
        const out = mergeLongChatMessages([null, { content: 'no id' }], [pod('a', '1')]);
        expect(out).toHaveLength(1);
    });
});
