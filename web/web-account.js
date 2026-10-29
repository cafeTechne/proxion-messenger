// web-account.js: which browser-local keys belong to the signed-in pod account
// in the browser build.
//
// In gateway mode the display name, status and the rest belong to the gateway
// identity and outlive a pod sign-out. In the browser build the pod account IS
// the identity, so these keys are per account: signing out must drop them, and
// signing in as someone else must not inherit them (the next account would
// show, and send join requests and DMs under, the previous account's name).
//
// Device preferences (theme, locale, camera, relay) and the E2E/identity key
// material (namespaced or purged by auth.js) are deliberately not listed here.

// Pod session keys the gateway-mode sign-out also clears.
export const POD_SESSION_KEYS = [
    'proxion_pod_connected', 'proxion_pod_webid', 'proxion_css_url',
    'proxion_pod_setup_skipped', 'proxion_pod_banner_dismissed',
    'proxion_pod_banner_snooze_until',
];

// Per-account state in the browser build.
export const WEB_ACCOUNT_KEYS = [
    'proxion_display_name', 'proxion_status_message', 'proxion_status',
    'proxion_muted_threads', 'proxion_hidden_dms', 'proxion_call_capable_peers',
    'proxion_solid_conversations',
];
export const WEB_ACCOUNT_PREFIXES = ['proxion_seen_'];

// Links stashed across the OIDC redirect (a room invite, a contact invite, the
// wizard step). They are written before sign-in, so they belong to the account
// signing in next: a sign-out drops them, a sign-in keeps them.
export const PRE_SIGNIN_KEYS = ['proxion_pending_join', 'proxion_pending_invite', 'proxion_ob_resume'];

// The WebID the per-account keys above were written for.
export const ACCOUNT_OWNER_KEY = 'proxion_account_webid';

function _remove(storage, key) {
    try { storage.removeItem(key); } catch { /* private mode */ }
}

function _dropAccountState(storage) {
    for (const k of WEB_ACCOUNT_KEYS) _remove(storage, k);
    try {
        for (let i = storage.length - 1; i >= 0; i--) {
            const k = storage.key(i);
            if (k && WEB_ACCOUNT_PREFIXES.some((p) => k.startsWith(p))) _remove(storage, k);
        }
    } catch { /* ignore */ }
}

// Web sign-out: forget the account on this browser (its pod session keys and
// its per-account state), keeping device preferences.
export function purgeWebAccount(storage) {
    _dropAccountState(storage);
    for (const k of [...PRE_SIGNIN_KEYS, ...POD_SESSION_KEYS]) _remove(storage, k);
    _remove(storage, ACCOUNT_OWNER_KEY);
}

// Web sign-in as `webId`: if the per-account keys were written for another
// account (or for an unknown one, from before they were tracked), drop them.
// Returns true when state was dropped, so the caller can reset what it already
// loaded into memory and restore the name from the pod profile.
export function adoptWebAccount(storage, webId) {
    if (!webId) return false;
    let owner = null;
    try { owner = storage.getItem(ACCOUNT_OWNER_KEY) || storage.getItem('proxion_pod_webid'); } catch { /* ignore */ }
    const dropped = owner !== webId;
    if (dropped) _dropAccountState(storage);
    try { storage.setItem(ACCOUNT_OWNER_KEY, webId); } catch { /* private mode */ }
    return dropped;
}

// The header label for a signed-in account with no name: the pod host.
export function webIdHostLabel(webId) {
    return webId ? String(webId).replace(/^https?:\/\//, '').split('/')[0] : '';
}

// Ask a signed-in account for a name at most once per WebID on this browser.
const _PROMPTED_PREFIX = 'proxion_name_prompted::';
export function shouldPromptForName(storage, webId) {
    if (!webId) return false;
    try {
        if (storage.getItem('proxion_display_name')) return false;
        return !storage.getItem(_PROMPTED_PREFIX + webId);
    } catch { return false; }
}
export function markNamePrompted(storage, webId) {
    try { storage.setItem(_PROMPTED_PREFIX + webId, '1'); } catch { /* private mode */ }
}
