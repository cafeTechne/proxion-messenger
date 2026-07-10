import { t } from './i18n.js';
// address.js — the user's own Proxion address bar + invite sharing: copy
// address, render the QR code, open the QR share panel, and update the address
// bar from a gateway event.
//
// A factory with no host mutable state. showToast / showCopyModal are injected;
// QRCode and the window.proxion* invite globals stay global. Returned functions
// are destructured into same-named bindings in main.js.
export function createAddress({ showToast, showCopyModal }) {

    function copyMyAddress() {
        const addr = localStorage.getItem("proxion_my_address") || "";
        if (!addr) return;
        navigator.clipboard.writeText(addr).then(() => {
            showToast(t('address.copied'));
            const btn = document.getElementById("copy-addr-btn");
            if (btn) {
                const orig = btn.textContent;
                btn.textContent = "✓ Copied";
                setTimeout(() => { btn.textContent = orig; }, 2000);
            }
        }).catch(() => { showCopyModal(addr); });
    }

    // R17.1: Render QR code into #my-qr container
    function renderMyQR(url) {
        if (!url || typeof QRCode === 'undefined') return;
        const container = document.getElementById('my-qr');
        if (!container) return;
        container.innerHTML = '';
        new QRCode(container, {
            text: url,
            width: 200,
            height: 200,
            colorDark: '#0f172a',
            colorLight: '#ffffff',
            correctLevel: QRCode.CorrectLevel.M,
        });
    }

    // R17.1: Open QR share panel anchored near the button
    function shareInviteLink() {
        const link = window.proxionInviteLink;
        if (!link) { showToast(t('address.noInviteLink')); return; }
        const panel = document.getElementById('qr-share-panel');
        if (!panel) return;
        // R17.4.5: encode proxion:// deep-link so scanning on a device with the app installed
        // opens it directly; fall back to HTTP short/invite URL on devices without the app.
        const addr = window.proxionAddress || localStorage.getItem('proxion_my_address');
        const qrUrl = addr
            ? `proxion://invite?from=${encodeURIComponent(addr)}`
            : (window.proxionShortInviteUrl || link);
        renderMyQR(qrUrl);
        // position near top-right
        panel.style.top = '64px';
        panel.style.right = '12px';
        panel.style.left = 'auto';
        panel.style.display = 'block';
    }

    function updateMyAddressBar(addr) {
        const bar = document.getElementById("my-address-bar");
        const short = document.getElementById("my-address-short");
        const full = document.getElementById("settings-proxion-address");
        if (full) full.textContent = addr || "";
        if (!addr || !bar || !short) return;
        const atIdx = addr.lastIndexOf("@");
        const truncDid = addr.slice(0, 20) + "…";
        const domain = atIdx > -1 ? addr.slice(atIdx) : "";
        short.textContent = truncDid + domain;
        short.title = addr;
        bar.style.display = "flex";
    }

    return { copyMyAddress, renderMyQR, shareInviteLink, updateMyAddressBar };
}
