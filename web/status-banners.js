// Pod / connectivity status banners — leaf DOM toggles plus the NAT-reachability
// warning. No host state or socket coupling; each just flips an element or
// fetches /connectivity to build a one-off guidance banner.
//
// createStatusBanners() — no deps.

import { t } from './i18n.js';
import { escHtml } from './util.js';

export function createStatusBanners() {
    // R16.4.2: pod status dot in the settings modal header
    function _updateSettingsPodDot(state) {
        const dot = document.getElementById('settings-pod-status-dot');
        if (!dot) return;
        if (state === 'connected') {
            dot.style.color = '#4ade80';
            dot.textContent = '● ' + t('pod.dot.connected');
        } else if (state === 'unreachable') {
            dot.style.color = '#fb923c';
            dot.textContent = '● ' + t('pod.dot.unreachable');
        } else {
            dot.style.color = '#8091a7';
            dot.textContent = '● ' + t('pod.dot.none');
        }
    }

    function setPodSyncIndicator(show) {
        const el = document.getElementById("pod-sync-indicator");
        if (el) el.style.display = show ? "" : "none";
    }

    function setPodBanner(show) {
        const el = document.getElementById("pod-connect-banner");
        if (el) el.style.display = show ? "flex" : "none";
    }

    function _showNatWarning() {
        if (document.getElementById("nat-warning-banner")) return;
        if (sessionStorage.getItem("proxion_nat_dismissed")) return;
        // Fetch connectivity details to give actionable, user-friendly guidance
        fetch("/connectivity").then(r => r.json()).then(c => {
            // Reachable directly OR via the sealed relay fallback → no warning.
            if (c.public_url_set || c.relay_fallback_active) return;
            const banner = document.createElement("div");
            banner.id = "nat-warning-banner";
            // Normal-flow block prepended to <body> (a column flex): it pushes the
            // app down rather than overlaying the sidebar header (position:fixed
            // used to cover the logo and nothing repositioned around it).
            banner.style.cssText = "flex-shrink:0;background:#78350f;color:#fef3c7;padding:10px 16px;font-size:0.85em;line-height:1.5;";
            const port = c.local_port || 8080;
            const localIp = c.local_ip || "192.168.x.x";
            const triedUpnp = c.upnp_mapped === false;
            let guide;
            if (triedUpnp) {
                // Vetted t()+static-markup composition (PLAN_ROUND_56 G3): the
                // markup skeleton lives HERE, never inside a locale value. Text
                // fragments come from t(); dynamic server values (port/ip) are
                // escaped before being wrapped in the styled <code> spans.
                const cs = "background:#451a03;padding:1px 4px;border-radius:3px;";
                const code = (x) => `<code style="${cs}">${escHtml(String(x))}</code>`;
                const envLine = `PROXION_PUBLIC_URL=http://YOUR_EXTERNAL_IP:${port}`;
                guide = `<strong>${t('nat.title')}</strong>
                    ${t('nat.subtitle')}
                    <details style="margin-top:6px;cursor:pointer;">
                      <summary><strong>${t('nat.howToFix')} ▾</strong></summary>
                      <div style="margin-top:8px;line-height:1.9;padding:0 4px;">
                        <strong>${t('nat.opt1Title')}</strong> ${t('nat.opt1Note')}<br>
                        ${t('nat.opt1Body', { port: code(port), ip: code(localIp), env: code(envLine) })}
                        &nbsp;<a href="https://portforward.com" target="_blank" rel="noopener" style="color:#fcd34d;">portforward.com</a> ${t('nat.opt1Guides')}<br><br>
                        <strong>${t('nat.opt2Title')}</strong> ${t('nat.opt2Note')}<br>
                        ${t('nat.opt2Run')} ${code('cloudflared tunnel --url http://localhost:' + port)}<br>
                        ${t('nat.opt2Copy', { url: code('https://xxxx.trycloudflare.com'), env: code('PROXION_PUBLIC_URL') })}
                      </div>
                    </details>`;
            } else {
                guide = t('nat.simpleGuide');
            }
            banner.innerHTML = `<div style="display:flex;gap:12px;align-items:flex-start;max-width:900px;margin:0 auto;">
                <span style="flex:1;">${guide}</span>
                <button style="background:transparent;border:none;color:#fef3c7;cursor:pointer;font-size:1.2em;flex-shrink:0;padding:0 4px;line-height:1;" aria-label="${t('common.dismiss')}">×</button>
            </div>`;
            banner.querySelector("button").onclick = () => {
                banner.remove();
                sessionStorage.setItem("proxion_nat_dismissed", "1");
            };
            document.body.prepend(banner);
        }).catch(() => {
            // Fallback: minimal banner if /connectivity unreachable
            const banner = document.createElement("div");
            banner.id = "nat-warning-banner";
            banner.style.cssText = "flex-shrink:0;background:#78350f;color:#fef3c7;padding:8px 16px;font-size:0.85em;display:flex;gap:8px;";
            banner.innerHTML = `<span style="flex:1">${t('nat.fallback', { env: '<code>PROXION_PUBLIC_URL</code>', file: '<code>.env</code>' })}</span><button onclick="this.closest('#nat-warning-banner').remove()" style="background:transparent;border:none;color:#fef3c7;cursor:pointer;" aria-label="${t('common.dismiss')}">×</button>`;
            document.body.prepend(banner);
        });
    }

    return { _updateSettingsPodDot, setPodSyncIndicator, setPodBanner, _showNatWarning };
}
