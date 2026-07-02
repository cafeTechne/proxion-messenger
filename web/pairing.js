// pairing.js — multi-device linking UX (delegation model).
//
// Primary device: "Link a device" -> pair_start -> show a QR/code. When a new
// device submits, confirm its safety code, sign a delegation cert with the
// primary's clientDid key, and pair_approve.
//
// New device: enter/scan the pairing code -> pair_submit its own freshly-minted
// clientDid -> on approval, store the relayed cert and reload; on reconnect the
// register path attaches the cert and the gateway admits it AS the account.
//
// A factory in the main.js idiom. Reassignable host state (socket, identity key)
// is read live via getters; issueDeviceCert is imported directly.
import { issueDeviceCert } from './device-cert.js';

export function createPairing({
    getSocket, getClientDid, getIdentityPrivKey, getGatewayUrl,
    showToast, refreshDevices,
}) {
    // active pairing context: primary {code, deviceDid} | new device {asNew, code}
    const state = { active: null };

    const $ = (id) => document.getElementById(id);
    function _show(id) { const el = $(id); if (el) el.style.display = 'flex'; }
    function _hide(id) { const el = $(id); if (el) el.style.display = 'none'; }
    function _text(id, t) { const el = $(id); if (el) el.textContent = t; }

    // ---- Primary side --------------------------------------------------------
    function startLinking() {
        const socket = getSocket();
        if (!socket) { showToast('Not connected.', 'error'); return; }
        state.active = { code: null, deviceDid: null };
        _show('device-link-modal');
        _text('device-link-status', 'Generating a pairing code…');
        _hide('device-link-approve-row');
        const qr = $('device-link-qr'); if (qr) qr.innerHTML = '';
        _text('device-link-code', '');
        socket.send(JSON.stringify({ cmd: 'pair_start' }));
    }

    function _onStarted(ev) {
        if (!state.active) return;
        state.active.code = ev.pairing_code;
        const payload = JSON.stringify({ v: 1, gw: getGatewayUrl() || '', code: ev.pairing_code });
        const qr = $('device-link-qr');
        if (qr && typeof QRCode !== 'undefined') {
            qr.innerHTML = '';
            new QRCode(qr, {
                text: payload, width: 200, height: 200,
                colorDark: '#0f172a', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.M,
            });
        }
        _text('device-link-code', ev.pairing_code);
        _text('device-link-status', 'Scan this on your other device, or enter the code. Expires in 5 min.');
    }

    function _onRequest(ev) {
        if (!state.active) return;
        state.active.deviceDid = ev.device_did;
        _text('device-link-status', 'A device wants to link. Confirm this code matches:');
        _text('device-link-safety', ev.safety_code || '');
        _show('device-link-approve-row');
    }

    async function approve() {
        const socket = getSocket();
        const priv = getIdentityPrivKey();
        const accountDid = getClientDid();
        if (!socket || !priv || !state.active || !state.active.deviceDid) return;
        let cert;
        try {
            cert = await issueDeviceCert(priv, accountDid, state.active.deviceDid);
        } catch (e) {
            showToast('Could not sign device cert.', 'error');
            return;
        }
        socket.send(JSON.stringify({
            cmd: 'pair_approve', pairing_code: state.active.code, delegation_cert: cert,
        }));
        _text('device-link-status', 'Approving…');
        _hide('device-link-approve-row');
    }

    // Cancel any in-progress pairing on the gateway, then close. Used by Deny,
    // the primary's Close button, and the new device's Cancel — all must release
    // the gateway session rather than leave it live until its TTL.
    function deny() {
        const socket = getSocket();
        if (socket && socket.readyState === WebSocket.OPEN && state.active && state.active.code) {
            socket.send(JSON.stringify({ cmd: 'pair_cancel', pairing_code: state.active.code }));
        }
        _closeAll();
    }

    function _onApproveAck() {
        showToast('Device linked.', 'success');
        _closeAll();
        if (refreshDevices) refreshDevices();
    }

    // ---- New-device side -----------------------------------------------------
    function beginAsNewDevice(code) {
        const socket = getSocket();
        const deviceDid = getClientDid();
        if (!socket || !deviceDid) { showToast('Not connected yet — try again in a moment.', 'error'); return; }
        if (!code) { showToast('Enter a pairing code.', 'error'); return; }
        state.active = { asNew: true, code };
        _text('pair-device-status', 'Contacting the other device…');
        socket.send(JSON.stringify({ cmd: 'pair_submit', pairing_code: code, device_did: deviceDid }));
    }

    function _onSubmitted(ev) {
        _text('pair-device-status', 'Waiting for approval. Confirm this code matches your other device:');
        _text('pair-device-safety', ev.safety_code || '');
    }

    function _onApproved(ev) {
        try {
            localStorage.setItem('proxion_delegation_cert', JSON.stringify(ev.delegation_cert));
        } catch (e) { /* storage full — cert lost, user retries */ }
        _text('pair-device-status', 'Linked! Reloading…');
        _text('pair-device-safety', '');
        showToast('This device is now linked.', 'success');
        setTimeout(() => { location.reload(); }, 1200);
    }

    function _onInvalid(ev) {
        const reason = ev.reason || 'unknown';
        if (state.active && state.active.asNew) {
            _text('pair-device-status', 'Pairing failed: ' + reason);
        } else {
            _text('device-link-status', 'Pairing failed: ' + reason);
        }
        state.active = null;
    }

    function _onCancelled() {
        if (state.active && state.active.asNew) {
            _text('pair-device-status', 'Pairing was cancelled on the other device.');
        }
        state.active = null;
    }

    function _closeAll() {
        _hide('device-link-modal');
        _hide('pair-device-modal');
        state.active = null;
    }

    // ---- Event routing (called from the main dispatch) -----------------------
    function handleEvent(ev) {
        switch (ev.type) {
            case 'pairing_started': _onStarted(ev); return true;
            case 'pairing_request': _onRequest(ev); return true;
            case 'pairing_approve_ack': _onApproveAck(ev); return true;
            case 'pairing_submitted': _onSubmitted(ev); return true;
            case 'pairing_approved': _onApproved(ev); return true;
            case 'pairing_invalid': _onInvalid(ev); return true;
            case 'pairing_cancelled': _onCancelled(ev); return true;
            default: return false;
        }
    }

    return { startLinking, approve, deny, beginAsNewDevice, handleEvent, closeAll: _closeAll, state };
}
