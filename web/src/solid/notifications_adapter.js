/**
 * Solid notifications adapter — @inrupt/solid-client-notifications wrapper.
 *
 * Wraps WebSocketChannel2023 subscription with capability detection, typed
 * fallback reasons, and malformed-payload quarantine.
 *
 * Mode is controlled by PROXION_SOLID_NOTIFS_MODE:
 *   auto   — SDK first, fall back to poll on error (default)
 *   sdk    — SDK required; surface error if unsupported
 *   legacy — skip SDK entirely
 *
 * Fallback reason codes:
 *   notifs_capability_absent    — pod HEAD probe returned no notification link
 *   notifs_protocol_unsupported — pod does not support WebSocketChannel2023
 *   notifs_auth_failed          — auth error during subscribe/connect
 *   notifs_transport_failed     — WebSocket transport error
 *   notifs_payload_invalid      — message payload failed validation (quarantined)
 *   sdk_unavailable             — @inrupt/solid-client-notifications not installed
 *   legacy_forced               — mode=legacy, skipped
 */

import { normalisedError, SOLID_NOT_SUPPORTED } from "./error_map.js";

let _notifs;
try {
  _notifs = await import("@inrupt/solid-client-notifications");
} catch {
  _notifs = null;
}

const _subscriptions = new Map(); // id -> { socket, resourceUrl }
const _quarantine = []; // malformed payloads held for inspection
let _nextId = 1;

function _mode() {
  if (typeof process !== "undefined") return process.env.PROXION_SOLID_NOTIFS_MODE ?? "auto";
  if (typeof window !== "undefined") return window.PROXION_SOLID_NOTIFS_MODE ?? "auto";
  return "auto";
}

function _classifyError(err) {
  const msg = (err?.message ?? "").toLowerCase();
  if (msg.includes("401") || msg.includes("403") || msg.includes("auth") || msg.includes("forbidden")) {
    return "notifs_auth_failed";
  }
  if (msg.includes("capability") || msg.includes("not supported") || msg.includes("no notification")) {
    return "notifs_capability_absent";
  }
  if (msg.includes("protocol") || msg.includes("channel") || msg.includes("websocket")) {
    return "notifs_transport_failed";
  }
  return err.code ?? "notifs_transport_failed";
}

function _validatePayload(payload) {
  if (typeof payload !== "object" || payload === null) return false;
  // Minimal Solid Notifications payload contract: type field required
  return typeof payload.type === "string";
}

/**
 * Probe whether a pod origin supports notifications.
 *
 * @param {string} resourceUrl
 * @param {object} [fetchOptions]
 * @returns {Promise<{supported: boolean, channelTypes: string[], reason?: string}>}
 */
export async function probeCapability(resourceUrl, fetchOptions = {}) {
  try {
    const resp = await fetch(resourceUrl, { method: "HEAD", ...fetchOptions });
    const linkHeader = resp.headers.get("Link") ?? "";
    const hasNotifLink = linkHeader.includes("rel=\"http://www.w3.org/ns/solid/notifications#");
    if (!hasNotifLink) {
      return { supported: false, channelTypes: [], reason: "notifs_capability_absent" };
    }
    const channelTypes = [];
    const matches = linkHeader.matchAll(/type="([^"]+)"/g);
    for (const m of matches) channelTypes.push(m[1]);
    return { supported: true, channelTypes };
  } catch (err) {
    return { supported: false, channelTypes: [], reason: "notifs_transport_failed" };
  }
}

/**
 * Subscribe to resource change notifications.
 *
 * @param {string} resourceUrl
 * @param {object} [opts]
 * @param {function} [opts.onUpdate]   Called when the resource changes (validated payload)
 * @param {object} [opts.fetchOptions] Authenticated fetch options
 * @returns {Promise<{id: string, mode: string, fallbackReason?: string}>}
 */
export async function subscribe(resourceUrl, { onUpdate, fetchOptions } = {}) {
  const mode = _mode();

  if (mode === "legacy") {
    return { id: null, mode: "legacy", fallbackReason: "legacy_forced" };
  }

  if (!_notifs) {
    if (mode === "sdk") {
      const err = new Error("@inrupt/solid-client-notifications not available");
      err.code = SOLID_NOT_SUPPORTED;
      throw err;
    }
    return { id: null, mode: "poll_fallback", fallbackReason: "sdk_unavailable" };
  }

  try {
    const socket = new _notifs.WebsocketNotification(resourceUrl, fetchOptions);

    if (onUpdate) {
      socket.on("message", (raw) => {
        let parsed;
        try {
          parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
        } catch {
          _quarantine.push({ resourceUrl, raw, reason: "notifs_payload_invalid", quarantinedAt: Date.now() });
          return;
        }
        if (!_validatePayload(parsed)) {
          _quarantine.push({ resourceUrl, raw, reason: "notifs_payload_invalid", quarantinedAt: Date.now() });
          return;
        }
        onUpdate(parsed);
      });
    }

    await socket.connect();

    const id = String(_nextId++);
    _subscriptions.set(id, { socket, resourceUrl });
    return { id, mode: "sdk" };
  } catch (err) {
    const code = _classifyError(err);
    if (mode === "sdk") {
      throw normalisedError(err, `subscribe:${resourceUrl}`);
    }
    return { id: null, mode: "poll_fallback", fallbackReason: code };
  }
}

/**
 * Unsubscribe from a previous subscription.
 *
 * @param {string} id  From the ``id`` returned by ``subscribe``
 * @returns {Promise<void>}
 */
export async function unsubscribe(id) {
  const sub = _subscriptions.get(id);
  if (!sub) return;
  try {
    await sub.socket.disconnect();
  } catch { /* ignore */ }
  _subscriptions.delete(id);
}

/**
 * Health check: returns whether the notifications channel is functional.
 *
 * @returns {{available: boolean, mode: string, activeSubscriptions: number, quarantinedCount: number}}
 */
export function health() {
  return {
    available: Boolean(_notifs),
    mode: _mode(),
    activeSubscriptions: _subscriptions.size,
    quarantinedCount: _quarantine.length,
  };
}

/**
 * Return quarantined payloads for inspection and clear the quarantine.
 * @returns {object[]}
 */
export function drainQuarantine() {
  return _quarantine.splice(0);
}
