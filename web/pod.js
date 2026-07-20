import { solidSession, podStorageRoot } from './auth.js';

const SAFE_ID_RE = /^[\w-]{1,128}$/;

async function podFetch(path, options = {}) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const url = root + path;
    try {
        await solidSession.fetch(url, options);
    } catch (err) {
        console.warn('pod write failed:', url, err);
    }
}

function _validateWebId(wid) {
    if (typeof wid !== 'string') return false;
    if (!wid.startsWith('https://')) return false;
    if (/[<>"{}|\\^`\s]/.test(wid)) return false;
    try {
        new URL(wid);
        return true;
    } catch {
        return false;
    }
}

async function _fetchOnePodMessage(url) {
    try {
        const r = await solidSession.fetch(url);
        if (!r.ok) return null;
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('json')) return null;
        const text = await r.text();
        if (text.length > 65536) return null;
        const msg = JSON.parse(text);
        if (
            typeof msg?.message_id !== 'string' ||
            typeof msg?.content !== 'string' ||
            typeof msg?.timestamp !== 'string' ||
            typeof msg?.from_webid !== 'string'
        ) return null;
        if (!SAFE_ID_RE.test(msg.message_id)) return null;
        return msg;
    } catch {
        return null;
    }
}

export async function podWriteMessage(roomId, msg) {
    if (!SAFE_ID_RE.test(roomId) || !SAFE_ID_RE.test(msg?.message_id || '')) return;
    await podFetch(
        `rooms/${roomId}/messages/${msg.message_id}.json`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(msg),
        }
    );
}

export async function podWriteMessageWithIndex(roomId, msg) {
    if (!SAFE_ID_RE.test(roomId) || !SAFE_ID_RE.test(msg?.message_id || '')) return;
    await podWriteMessage(roomId, msg);
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const indexUrl = root + `rooms/${roomId}/messages/index.json`;
    try {
        let ids = [];
        const res = await solidSession.fetch(indexUrl);
        if (res.ok) {
            const raw = await res.json();
            ids = Array.isArray(raw?.ids)
                ? raw.ids.filter((id) => typeof id === 'string' && SAFE_ID_RE.test(id))
                : [];
        }
        if (!ids.includes(msg.message_id)) {
            ids.push(msg.message_id);
            if (ids.length > 10000) ids = ids.slice(-10000);
            await solidSession.fetch(indexUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids }),
            });
        }
    } catch (err) {
        console.warn('pod index update failed:', err);
    }
}

export async function podWriteRoomMeta(roomId, meta) {
    if (!SAFE_ID_RE.test(roomId)) return;
    await podFetch(
        `rooms/${roomId}/room.json`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(meta),
        }
    );
}

export async function podReadMessages(roomId) {
    if (!SAFE_ID_RE.test(roomId)) return [];
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const indexUrl = root + `rooms/${roomId}/messages/index.json`;
    try {
        const res = await solidSession.fetch(indexUrl);
        if (res.status === 404 || !res.ok) return [];
        const raw = await res.json();
        if (!raw || !Array.isArray(raw.ids)) return [];
        const ids = raw.ids.filter((id) => typeof id === 'string' && SAFE_ID_RE.test(id));
        const limited = ids.slice(-200);
        const results = await Promise.allSettled(
            limited.map((id) => _fetchOnePodMessage(root + `rooms/${roomId}/messages/${id}.json`))
        );
        const msgs = results
            .filter((r) => r.status === 'fulfilled' && r.value)
            .map((r) => r.value);
        msgs.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
        return msgs;
    } catch {
        return [];
    }
}

export async function podReadRoomMeta(roomId) {
    if (!SAFE_ID_RE.test(roomId)) return null;
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(root + `rooms/${roomId}/room.json`);
        if (!res.ok) return null;
        const text = await res.text();
        if (text.length > 65536) return null;
        const meta = JSON.parse(text);
        if (typeof meta?.room_id !== 'string') return null;
        return meta;
    } catch {
        return null;
    }
}

function buildWacAcl(ownerWebId, memberWebIds, containerUrl) {
    if (!_validateWebId(ownerWebId)) throw new Error('Invalid owner WebID');
    const validMembers = (memberWebIds || []).filter(_validateWebId);
    const memberLines = validMembers.map((wid) => `    acl:agent <${wid}>;`).join('\n');
    const memberBlock = validMembers.length > 0
        ? `\n<#members>\n    a acl:Authorization;\n${memberLines}\n    acl:accessTo <${containerUrl}>;\n    acl:default <${containerUrl}>;\n    acl:mode acl:Read.`
        : '';
    return (
        `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n\n` +
        `<#owner>\n    a acl:Authorization;\n    acl:agent <${ownerWebId}>;\n` +
        `    acl:accessTo <${containerUrl}>;\n    acl:default <${containerUrl}>;\n` +
        `    acl:mode acl:Read, acl:Write, acl:Control.` +
        memberBlock + '\n'
    );
}

export async function podSetContainerAcl(containerPath, ownerWebId, memberWebIds) {
    const root = podStorageRoot();
    if (!root) return;
    const containerUrl = root + containerPath;
    if (!containerUrl.startsWith(root)) return;
    let acl;
    try {
        acl = buildWacAcl(ownerWebId, memberWebIds, containerUrl);
    } catch (err) {
        console.warn('ACL build failed:', err.message);
        return;
    }
    try {
        await solidSession.fetch(containerUrl + '.acl', {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: acl,
        });
    } catch (err) {
        console.warn('WAC ACL write failed:', err);
    }
}

// --- Bootstrap ---

export async function ensureProxionContainer() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const uri = `${root}proxion/`;
    try {
        const res = await solidSession.fetch(uri, { method: 'HEAD' });
        if (res.status !== 404) return;
        await solidSession.fetch(uri, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: '',
        });
        await solidSession.fetch(`${uri}.acl`, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n<#owner> a acl:Authorization;\n    acl:agent <${solidSession.info.webId}>;\n    acl:accessTo <${uri}>;\n    acl:defaultForNew <${uri}>;\n    acl:mode acl:Read, acl:Write, acl:Control.`,
        });
    } catch (err) {
        console.warn('[pod] ensureProxionContainer failed:', err);
    }
}

// --- Profile ---

export async function podWriteProfile({ displayName, avatarBlob } = {}) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        if (displayName !== undefined) {
            await solidSession.fetch(`${root}proxion/profile/display_name.jsonld`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify({
                    '@context': { px: 'https://proxion.dev/vocab/v1#' },
                    '@type': 'px:Profile',
                    'px:displayName': displayName,
                    'px:updatedAt': new Date().toISOString(),
                }),
            });
        }
        if (avatarBlob) {
            await solidSession.fetch(`${root}proxion/profile/avatar.png`, {
                method: 'PUT',
                headers: { 'Content-Type': avatarBlob.type || 'image/png' },
                body: avatarBlob,
            });
        }
    } catch (err) {
        console.warn('[pod] podWriteProfile failed:', err);
    }
}

export async function podReadProfile() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(`${root}proxion/profile/display_name.jsonld`,
            { headers: { Accept: 'application/ld+json' } });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

// --- Messages (canonical JSON-LD) ---

export async function podWriteMessageJsonLd(threadId, messageId, msg, isRoom = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const dir = isRoom ? `rooms/${threadId}` : `dm/${threadId}`;
    const uri = `${root}proxion/${dir}/messages/${messageId}.jsonld`;
    try {
        await solidSession.fetch(uri, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:Message',
                '@id': uri,
                'px:messageId': messageId,
                'px:threadId': threadId,
                'px:content': msg.content || '',
                'px:contentType': msg.content_type || 'text',
                'px:fromWebid': msg.from_webid || '',
                'px:fromName': msg.from_display_name || '',
                'px:timestamp': msg.timestamp || new Date().toISOString(),
                'px:replyToId': msg.reply_to_id || null,
                'px:replyToSnippet': msg.reply_to_snippet || null,
                'px:forwarded': msg.forwarded || false,
                'px:forwardedFromName': msg.forwarded_from_name || null,
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteMessageJsonLd failed:', err);
    }
}

export async function podDeleteMessage(threadId, messageId, isRoom = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const dir = isRoom ? `rooms/${threadId}` : `dm/${threadId}`;
    try {
        await solidSession.fetch(
            `${root}proxion/${dir}/messages/${messageId}.jsonld`,
            { method: 'DELETE' }
        );
    } catch (err) {
        console.warn('[pod] podDeleteMessage failed:', err);
    }
}

// --- Room Members ---

export async function podWriteRoomMembers(roomId, members) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/rooms/${roomId}/members.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:MemberList',
                'px:roomId': roomId,
                'px:members': members,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteRoomMembers failed:', err);
    }
}

// --- Reactions ---

export async function podWriteReactions(roomId, messageId, reactions) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(
            `${root}proxion/rooms/${roomId}/reactions/${messageId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:ReactionSet',
                'px:messageId': messageId,
                'px:reactions': reactions,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteReactions failed:', err);
    }
}

// --- Read State ---

export async function podWriteReadState(threadId, lastMessageId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/readstate/${threadId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:ReadState',
                'px:threadId': threadId,
                'px:lastReadMessageId': lastMessageId,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteReadState failed:', err);
    }
}

export async function podReadReadState(threadId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(
            `${root}proxion/readstate/${threadId}.jsonld`,
            { headers: { Accept: 'application/ld+json' } }
        );
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

// --- Voice Audio ---

export async function podUploadVoiceAudio(roomId, messageId, audioBlob) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    const fileUri = `${root}proxion/rooms/${roomId}/files/${messageId}.webm`;
    try {
        await solidSession.fetch(fileUri, {
            method: 'PUT',
            headers: { 'Content-Type': 'audio/webm' },
            body: audioBlob,
        });
        return fileUri;
    } catch (err) {
        console.warn('[pod] podUploadVoiceAudio failed:', err);
        return null;
    }
}

export async function podDeleteVoiceAudio(roomId, messageId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(
            `${root}proxion/rooms/${roomId}/files/${messageId}.webm`,
            { method: 'DELETE' }
        );
    } catch (err) {
        console.warn('[pod] podDeleteVoiceAudio failed:', err);
    }
}

// --- File Uploads ---

export async function podUploadFile(roomId, messageId, filename, fileBlob) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_');
    const fileUri = `${root}proxion/rooms/${roomId}/files/${messageId}/${safeName}`;
    try {
        await solidSession.fetch(fileUri, {
            method: 'PUT',
            headers: { 'Content-Type': fileBlob.type || 'application/octet-stream' },
            body: fileBlob,
        });
        return fileUri;
    } catch (err) {
        console.warn('[pod] podUploadFile failed:', err);
        return null;
    }
}

// --- Scheduled Messages ---

export async function podWriteScheduled(id, threadId, sendAt, contentPreview) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/scheduled/${id}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:ScheduledMessage',
                'px:id': id,
                'px:threadId': threadId,
                'px:sendAt': sendAt,
                'px:contentPreview': contentPreview,
                'px:createdAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteScheduled failed:', err);
    }
}

export async function podDeleteScheduled(id) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/scheduled/${id}.jsonld`, { method: 'DELETE' });
    } catch (err) {
        console.warn('[pod] podDeleteScheduled failed:', err);
    }
}

// --- Webhooks ---

async function _sha256Hex(str) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function podWriteWebhook(id, wh) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        const tokenHash = wh.token ? await _sha256Hex(wh.token) : '';
        await solidSession.fetch(`${root}proxion/webhooks/${id}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:Webhook',
                'px:id': id,
                'px:direction': wh.direction,
                'px:botName': wh.bot_name,
                'px:url': wh.url || null,
                'px:tokenHash': tokenHash,
                'px:createdAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteWebhook failed:', err);
    }
}

export async function podDeleteWebhook(id) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/webhooks/${id}.jsonld`, { method: 'DELETE' });
    } catch (err) {
        console.warn('[pod] podDeleteWebhook failed:', err);
    }
}

// --- Generic index helpers ---

async function _readIndex(indexUrl) {
    try {
        const res = await solidSession.fetch(indexUrl, { headers: { Accept: 'application/ld+json' } });
        if (!res.ok) return [];
        const raw = await res.json();
        const ids = raw?.['px:ids'] ?? raw?.ids ?? [];
        return Array.isArray(ids) ? ids.filter(id => typeof id === 'string' && SAFE_ID_RE.test(id)) : [];
    } catch { return []; }
}

async function _writeIndex(indexUrl, ids) {
    try {
        await solidSession.fetch(indexUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:Index',
                'px:ids': ids,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] _writeIndex failed:', indexUrl, err);
    }
}

async function _addToIndex(indexUrl, id) {
    const ids = await _readIndex(indexUrl);
    if (!ids.includes(id)) {
        ids.push(id);
        await _writeIndex(indexUrl, ids);
    }
}

async function _removeFromIndex(indexUrl, id) {
    const ids = await _readIndex(indexUrl);
    const filtered = ids.filter(i => i !== id);
    if (filtered.length !== ids.length) await _writeIndex(indexUrl, filtered);
}

// --- Contacts ---

export async function podWriteContact(certId, certObj) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn || !SAFE_ID_RE.test(certId)) return;
    try {
        await solidSession.fetch(`${root}proxion/contacts/${certId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:Contact',
                '@id': `${root}proxion/contacts/${certId}.jsonld`,
                'px:certId': certId,
                'px:certificate': certObj,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
        await _addToIndex(`${root}proxion/contacts/index.jsonld`, certId);
    } catch (err) {
        console.warn('[pod] podWriteContact failed:', err);
    }
}

export async function podReadContacts() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const ids = await _readIndex(`${root}proxion/contacts/index.jsonld`);
    if (!ids.length) return [];
    const results = await Promise.allSettled(
        ids.map(id => solidSession.fetch(`${root}proxion/contacts/${id}.jsonld`)
            .then(r => r.ok ? r.json() : null)
            .then(doc => doc?.['px:certificate'] ?? null)
            .catch(() => null))
    );
    return results
        .filter(r => r.status === 'fulfilled' && r.value)
        .map(r => r.value);
}

export async function podDeleteContact(certId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn || !SAFE_ID_RE.test(certId)) return;
    try {
        await solidSession.fetch(`${root}proxion/contacts/${certId}.jsonld`, { method: 'DELETE' });
        await _removeFromIndex(`${root}proxion/contacts/index.jsonld`, certId);
    } catch (err) {
        console.warn('[pod] podDeleteContact failed:', err);
    }
}

// --- Invites ---

export async function podWriteInvite(invitationId, inviteObj) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const safeId = invitationId.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 128);
    try {
        await solidSession.fetch(`${root}proxion/invites/${safeId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:PendingInvite',
                'px:invitationId': invitationId,
                'px:invite': inviteObj,
                'px:receivedAt': new Date().toISOString(),
            }),
        });
        await _addToIndex(`${root}proxion/invites/index.jsonld`, safeId);
    } catch (err) {
        console.warn('[pod] podWriteInvite failed:', err);
    }
}

export async function podReadInvites() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const ids = await _readIndex(`${root}proxion/invites/index.jsonld`);
    if (!ids.length) return [];
    const results = await Promise.allSettled(
        ids.map(id => solidSession.fetch(`${root}proxion/invites/${id}.jsonld`)
            .then(r => r.ok ? r.json() : null)
            .then(doc => doc?.['px:invite'] ?? null)
            .catch(() => null))
    );
    return results
        .filter(r => r.status === 'fulfilled' && r.value)
        .map(r => r.value);
}

export async function podDeleteInvite(invitationId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const safeId = invitationId.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 128);
    try {
        await solidSession.fetch(`${root}proxion/invites/${safeId}.jsonld`, { method: 'DELETE' });
        await _removeFromIndex(`${root}proxion/invites/index.jsonld`, safeId);
    } catch (err) {
        console.warn('[pod] podDeleteInvite failed:', err);
    }
}

// --- Room index ---

export async function podWriteRoomIndex(roomIds) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    await _writeIndex(`${root}proxion/rooms/index.jsonld`, roomIds.filter(id => SAFE_ID_RE.test(id)));
}

export async function podReadRoomIndex() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    return _readIndex(`${root}proxion/rooms/index.jsonld`);
}

export async function _podUpdateRoomIndex(roomId, add = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn || !SAFE_ID_RE.test(roomId)) return;
    const indexUrl = `${root}proxion/rooms/index.jsonld`;
    if (add) await _addToIndex(indexUrl, roomId);
    else await _removeFromIndex(indexUrl, roomId);
}

// --- DM thread index ---

export async function podWriteDmIndex(threadIds) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    await _writeIndex(`${root}proxion/dm/index.jsonld`, threadIds);
}

export async function podReadDmIndex() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    return _readIndex(`${root}proxion/dm/index.jsonld`);
}

export async function _podUpdateDmIndex(threadId, add = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const indexUrl = `${root}proxion/dm/index.jsonld`;
    if (add) await _addToIndex(indexUrl, threadId);
    else await _removeFromIndex(indexUrl, threadId);
}

// --- Opt-in DM archive (R61) ---
//
// Off by default. When enabled AND a pod is connected, decrypted DM history is
// written to your own pod as open px:Message JSON-LD, so it syncs across your
// devices and any Solid app you authorize can read it. It stays owner-only: DM
// resources inherit the proxion/ container's owner-only ACL, and we never grant
// member read here (the other party keeps their own copy on their own pod).

export function dmPodArchiveEnabled() {
    try {
        return typeof localStorage !== 'undefined' &&
            localStorage.getItem('proxion_dm_pod_archive') === '1';
    } catch {
        return false;
    }
}

// Write a single DM message to the pod archive (no-op unless enabled + logged
// in). Reuses the canonical JSON-LD writer and maintains a per-thread message
// index so read-back can enumerate without a container LIST.
export async function podArchiveDmMessage(threadId, msg) {
    if (!dmPodArchiveEnabled()) return;
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    if (!SAFE_ID_RE.test(threadId) || !SAFE_ID_RE.test(msg?.message_id || '')) return;
    await podWriteMessageJsonLd(threadId, msg.message_id, msg, false);
    await _addToIndex(`${root}proxion/dm/${threadId}/messages/index.jsonld`, msg.message_id);
    await _podUpdateDmIndex(threadId, true);
}

// Remove a DM message from the pod archive (best-effort; only when logged in).
export async function podArchiveDeleteDmMessage(threadId, messageId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    if (!SAFE_ID_RE.test(threadId) || !SAFE_ID_RE.test(messageId)) return;
    await podDeleteMessage(threadId, messageId, false);
    await _removeFromIndex(`${root}proxion/dm/${threadId}/messages/index.jsonld`, messageId);
}

// Read archived DM history for a thread back into the message shape the client
// renders. Reading your own archive is always fine (independent of the write
// toggle), so enabling archiving on one device restores history on another.
export async function podReadDmMessages(threadId) {
    if (!SAFE_ID_RE.test(threadId)) return [];
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const base = `${root}proxion/dm/${threadId}/messages/`;
    const ids = (await _readIndex(`${base}index.jsonld`)).slice(-200);
    if (!ids.length) return [];
    const results = await Promise.allSettled(
        ids.map((id) => solidSession.fetch(`${base}${id}.jsonld`)
            .then((r) => (r.ok ? r.text() : null))
            .then((text) => {
                if (!text || text.length > 65536) return null;
                const doc = JSON.parse(text);
                if (doc?.['@type'] !== 'px:Message') return null;
                const mid = doc['px:messageId'];
                if (typeof mid !== 'string' || !SAFE_ID_RE.test(mid)) return null;
                return {
                    message_id: mid,
                    thread_id: threadId,
                    content: doc['px:content'] || '',
                    content_type: doc['px:contentType'] || 'text',
                    from_webid: doc['px:fromWebid'] || '',
                    from_display_name: doc['px:fromName'] || '',
                    timestamp: doc['px:timestamp'] || '',
                    reply_to_id: doc['px:replyToId'] || null,
                };
            })
            .catch(() => null))
    );
    const msgs = results
        .filter((r) => r.status === 'fulfilled' && r.value)
        .map((r) => r.value);
    msgs.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
    return msgs;
}
