/**
 * Solid notifications adapter — @inrupt/solid-client-notifications wrapper.
 *
 * Wraps WebSocketChannel2023 subscription with capability detection and a
 * typed fallback reason when the server does not support the protocol.
 *
 * Mode is controlled by PROXION_SOLID_NOTIFS_MODE:
 *   auto   — SDK first, fall back to poll on error (default)
 *   sdk    — SDK required; surface error if unsupported
 *   legacy — skip SDK entirely
 */

import { normalisedError, SOLID_NOT_SUPPORTED } from "./error_map.js";

let _notifs;
try {
  _notifs = await import("@inrupt/solid-client-notifications");
} catch {
  _notifs = null;
}

const _subscriptions = new Map(); // id -> { socket, resourceUrl, callbacks }
let _nextId = 1;

function _mode() {
  if (typeof process !== "undefined") return process.env.PROXION_SOLID_NOTIFS_MODE ?? "auto";
  if (typeof window !== "undefined") return window.PROXION_SOLID_NOTIFS_MODE ?? "auto";
  return "auto";
}

/**
 * Subscribe to resource change notifications.
 *
 * @param {string} resourceUrl
 * @param {object} [opts]
 * @param {function} [opts.onUpdate]   Called when the resource changes
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
    if (onUpdate) socket.on("message", onUpdate);
    await socket.connect();

    const id = String(_nextId++);
    _subscriptions.set(id, { socket, resourceUrl });
    return { id, mode: "sdk" };
  } catch (err) {
    const code = err.code ?? "sdk_error";
    if (mode === "sdk") {
      throw normalisedError(err, `subscribe:${resourceUrl}`);
    }
    // auto: fall back, record the reason
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
 * @returns {{available: boolean, mode: string, activeSubscriptions: number}}
 */
export function health() {
  return {
    available: Boolean(_notifs),
    mode: _mode(),
    activeSubscriptions: _subscriptions.size,
  };
}
