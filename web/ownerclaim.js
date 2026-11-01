// Gateway ownership: the one-time claim of a public gateway and the owner list
// in Settings.
//
// A gateway that requires sign-in by configuration (a server, not the desktop
// app) starts with no owner and logs a link like https://host/#claim=CODE.
// Opening it signs in as usual and then presents the code once. The fragment
// is taken out of the address bar before anything else runs, so it never
// stays in history or a bookmark, and a fragment is never sent in a request.
// Without the link, an unclaimed gateway shows a banner that asks for the
// code (or the operator's admin token).
//
// createOwnerClaim({ send, showToast, showConfirm, showPromptModal, t, escHtml, initialCode })

const CODE_RE = /^[A-Za-z0-9_-]{16,256}$/;

/** Read and strip a `#claim=CODE` fragment. Returns the code, or '' when the
 *  fragment is absent or malformed (a malformed one is still stripped). */
export function takeClaimFragment(loc, hist) {
    const hash = String(loc?.hash || '');
    if (!hash.startsWith('#claim=')) return '';
    let code = '';
    try { code = decodeURIComponent(hash.slice('#claim='.length)); } catch { code = ''; }
    try {
        hist?.replaceState?.(hist.state ?? null, '', String(loc.pathname || '/') + String(loc.search || ''));
    } catch { /* an address we cannot rewrite still must not be used twice */ }
    return CODE_RE.test(code) ? code : '';
}

export function createOwnerClaim({ send, showToast, showConfirm, showPromptModal, t, escHtml,
    initialCode = '', doc = document } = {}) {
    let pending = initialCode || '';
    let owner = false;
    let selfId = '';

    const banner = () => doc.getElementById('owner-claim-banner');
    const setBanner = (on) => banner()?.classList.toggle('visible', !!on);
    const section = () => doc.getElementById('settings-owner-devices');

    function setOwner(on) {
        owner = !!on;
        const s = section();
        if (s) s.hidden = !owner;
    }

    /** On every `registered`: an owner needs nothing; a pending link claims;
     *  otherwise an unclaimed gateway offers the banner. */
    function onRegistered(event) {
        selfId = event?.webid || '';
        setOwner(event?.owner);
        if (owner) {
            pending = '';
            setBanner(false);
            return;
        }
        if (pending) {
            const code = pending;
            pending = '';
            send({ cmd: 'claim_gateway', code });
            return;
        }
        setBanner(!!event?.claimable);
    }

    async function promptForCode() {
        const value = await showPromptModal(t('claim.prompt'), {
            title: t('claim.title'), confirmLabel: t('claim.verb'), type: 'password', required: true,
        });
        const code = String(value || '').trim();
        if (code) send({ cmd: 'claim_gateway', code });
    }

    function onClaimed() {
        setBanner(false);
        setOwner(true);
        showToast(t('claim.done'));
    }

    function onClaimFailed(event) {
        const reason = event?.reason;
        if (reason === 'rate_limited') showToast(t('claim.failed.rateLimited'));
        else if (reason === 'already_claimed') showToast(t('claim.failed.alreadyClaimed'));
        else if (reason === 'auth_required') showToast(t('claim.failed.authRequired'));
        else showToast(t('claim.failed.invalid'));
        setBanner(reason !== 'already_claimed' && reason !== 'auth_required');
    }

    function requestOwnerDevices() {
        if (owner) send({ cmd: 'list_owner_devices' });
    }

    function renderOwnerDevices(event) {
        const list = doc.getElementById('settings-owner-devices-list');
        setOwner(true);   // only an owner is sent the list
        if (!list) return;
        const devices = Array.isArray(event?.devices) ? event.devices : [];
        list.innerHTML = devices.map((d) => {
            const did = String(d.did || '');
            const name = escHtml(d.display_name || did.slice(0, 24));
            const meta = d.self ? t('owners.thisDevice') : escHtml(did.slice(0, 24));
            return `<div class="list-row">
                <span class="list-row__main">${name}<br><span class="list-row__meta">${meta}</span></span>
                <button data-owner-did="${escHtml(did)}" class="link-btn link-btn--danger">${escHtml(t('owners.remove'))}</button>
            </div>`;
        }).join('');
        list.querySelectorAll('[data-owner-did]').forEach((btn) => {
            btn.addEventListener('click', () => removeOwner(btn.dataset.ownerDid, devices.length));
        });
    }

    function removeOwner(did, count) {
        const self = did === selfId;
        if (!self) {
            send({ cmd: 'remove_owner_device', did });
            return;
        }
        // Removing yourself takes away your own owner rights; as the last owner
        // it leaves the gateway unclaimed.
        showConfirm(t(count <= 1 ? 'owners.removeSelfLast' : 'owners.removeSelf'), () => {
            send({ cmd: 'remove_owner_device', did, confirm: true });
        }, null, { title: t('owners.removeSelfTitle'), confirmLabel: t('owners.remove'), danger: true });
    }

    function onOwnerDeviceRemoved(event) {
        if (event?.did && event.did === selfId) setOwner(false);
    }

    doc.getElementById('owner-claim-btn')?.addEventListener('click', () => { promptForCode(); });

    return {
        onRegistered, onClaimed, onClaimFailed, promptForCode,
        requestOwnerDevices, renderOwnerDevices, onOwnerDeviceRemoved,
        isOwner: () => owner, hasPending: () => !!pending,
    };
}
