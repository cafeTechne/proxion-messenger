// account-sync.js: display name, mutes and settings shared by every device
// of one account, through the gateway.
//
// A linked device registers AS the account (the gateway verifies its
// delegation cert), so the gateway keeps one record per account and pushes
// each change to the account's other sessions as an `account_sync` event. A
// snapshot (`full: true`) answers get_account_sync after every register.
//
// Only events for the account this socket registered as are applied, and
// only the allowlisted settings below; device-specific keys (gateway URL,
// local DM history, sync toggles) never travel.

export const SYNCED_PREFS = [
    'proxion_receipts_enabled',
    'proxion_link_previews_enabled',
    'proxion_locale',
    'proxion_theme',
];

const MAX_PREF_LEN = 32;

/**
 * @param {object} deps
 * @param {(payload:object)=>void} deps.send       socketSendOrQueue
 * @param {Storage} deps.storage
 * @param {()=>Set<string>} deps.getMutedThreads
 * @param {(threadId:string, muted:boolean)=>void} deps.applyMute  local mute/unmute + icon
 * @param {(threadId:string, muted:boolean)=>void} deps.sendMute   tell the gateway
 * @param {(name:string)=>void} deps.onName        a synced name arrived
 * @param {(keys:string[])=>void} deps.onPrefs     these synced prefs changed locally
 */
export function createAccountSync({ send, storage, getMutedThreads, applyMute, sendMute, onName, onPrefs }) {
    let account = null;

    const seededKey = (webid) => `proxion_account_sync_seeded:${webid}`;

    function setAccount(webid) {
        account = typeof webid === 'string' && webid ? webid : null;
    }

    function request() {
        if (account) send({ cmd: 'get_account_sync' });
    }

    function pushPref(key) {
        if (!SYNCED_PREFS.includes(key)) return;
        const v = storage.getItem(key);
        send({ cmd: 'set_account_pref', key, value: v === null ? '' : String(v) });
    }

    // Write one pref locally; true when the stored value changed.
    function _setPref(key, value) {
        if (!SYNCED_PREFS.includes(key) || typeof value !== 'string' || value.length > MAX_PREF_LEN) return false;
        const cur = storage.getItem(key);
        if (value === '') {
            if (cur === null) return false;
            storage.removeItem(key);
            return true;
        }
        if (cur === value) return false;
        storage.setItem(key, value);
        return true;
    }

    function _applyName(name) {
        if (typeof name !== 'string') return;
        const n = name.trim().slice(0, 100);
        if (!n || n === storage.getItem('proxion_display_name')) return;
        storage.setItem('proxion_display_name', n);
        onName?.(n);
    }

    function _applyFull(event) {
        // Name: the account's name wins; a device holding one seeds an
        // account that has none.
        if (event.display_name) _applyName(event.display_name);
        else {
            const local = storage.getItem('proxion_display_name');
            if (local) send({ cmd: 'set_identity', display_name: local });
        }
        // Prefs: synced values win; a local value the account lacks seeds it.
        const prefs = event.prefs && typeof event.prefs === 'object' ? event.prefs : {};
        const changed = [];
        for (const key of SYNCED_PREFS) {
            if (Object.prototype.hasOwnProperty.call(prefs, key)) {
                if (_setPref(key, prefs[key])) changed.push(key);
            } else if (storage.getItem(key) !== null) {
                pushPref(key);
            }
        }
        if (changed.length) onPrefs?.(changed);
        // Mutes: the first snapshot on this device merges both ways (mutes
        // made before linking are kept); after that the account's set is
        // the truth, so an unmute on another device sticks here too.
        const server = new Set((Array.isArray(event.muted_threads) ? event.muted_threads : [])
            .filter((t) => typeof t === 'string' && t));
        const local = getMutedThreads();
        let seeded = false;
        try { seeded = storage.getItem(seededKey(account)) === '1'; } catch (_) { /* private mode */ }
        if (!seeded) {
            for (const tid of [...local]) if (!server.has(tid)) sendMute(tid, true);
            try { storage.setItem(seededKey(account), '1'); } catch (_) { /* private mode */ }
        } else {
            for (const tid of [...local]) if (!server.has(tid)) applyMute(tid, false);
        }
        for (const tid of server) if (!local.has(tid)) applyMute(tid, true);
    }

    /** Apply an account_sync event. Returns false when it was ignored. */
    function apply(event) {
        if (!event || !account || event.webid !== account) return false;
        if (event.full === true) { _applyFull(event); return true; }
        if (typeof event.display_name === 'string') _applyName(event.display_name);
        if (event.prefs && typeof event.prefs === 'object') {
            const changed = Object.keys(event.prefs).filter((k) => _setPref(k, event.prefs[k]));
            if (changed.length) onPrefs?.(changed);
        }
        if (event.mutes && typeof event.mutes === 'object') {
            const local = getMutedThreads();
            for (const [tid, muted] of Object.entries(event.mutes)) {
                if (!tid || typeof muted !== 'boolean' || local.has(tid) === muted) continue;
                applyMute(tid, muted);
            }
        }
        return true;
    }

    return { setAccount, request, pushPref, apply, get account() { return account; } };
}
