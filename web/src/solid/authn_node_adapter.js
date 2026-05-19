/**
 * Node-side authentication adapter — encapsulates @inrupt/solid-client-authn-node.
 *
 * Provides a stable contract for backend-facing auth operations so vendor SDK
 * details stay confined to this module.  Error codes are always normalised via
 * error_map.js before surfacing to callers.
 *
 * Environment: Node.js / gateway sidecar only.  Do not import in browser bundles.
 */

import { normalisedError, SOLID_NOT_SUPPORTED } from "./error_map.js";

let _Session;
try {
  const authn = await import("@inrupt/solid-client-authn-node");
  _Session = authn.Session;
} catch {
  _Session = null;
}

const _sessions = new Map(); // sessionId -> Session instance

function _requireSdk() {
  if (!_Session) {
    const err = new Error("@inrupt/solid-client-authn-node is not available");
    err.code = SOLID_NOT_SUPPORTED;
    throw err;
  }
}

/**
 * Create and log in a new client-credentials session.
 *
 * @param {object} opts
 * @param {string} opts.issuer      OIDC issuer URL
 * @param {string} opts.clientId
 * @param {string} opts.clientSecret
 * @returns {Promise<string>} sessionId
 */
export async function createSession({ issuer, clientId, clientSecret }) {
  try {
    _requireSdk();
    const session = new _Session();
    await session.login({
      oidcIssuer: issuer,
      clientId,
      clientSecret,
      tokenType: "DPoP",
    });
    _sessions.set(session.info.sessionId, session);
    return session.info.sessionId;
  } catch (err) {
    throw normalisedError(err, "createSession");
  }
}

/**
 * Authenticated fetch using an existing session.
 *
 * @param {string} sessionId
 * @param {string} url
 * @param {RequestInit} [init]
 * @returns {Promise<Response>}
 */
export async function fetchWithSession(sessionId, url, init) {
  try {
    _requireSdk();
    const session = _sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);
    return await session.fetch(url, init);
  } catch (err) {
    throw normalisedError(err, `fetchWithSession:${sessionId}`);
  }
}

/**
 * Force-refresh a session's tokens.
 *
 * @param {string} sessionId
 * @returns {Promise<void>}
 */
export async function refreshSession(sessionId) {
  try {
    _requireSdk();
    const session = _sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);
    // @inrupt/solid-client-authn-node refreshes transparently; this is a no-op hint.
    await session.fetch(session.info.webId ?? "", { method: "HEAD" }).catch(() => {});
  } catch (err) {
    throw normalisedError(err, `refreshSession:${sessionId}`);
  }
}

/**
 * Log out and remove a session.
 *
 * @param {string} sessionId
 * @returns {Promise<void>}
 */
export async function closeSession(sessionId) {
  try {
    _requireSdk();
    const session = _sessions.get(sessionId);
    if (session) {
      await session.logout();
      _sessions.delete(sessionId);
    }
  } catch (err) {
    throw normalisedError(err, `closeSession:${sessionId}`);
  }
}
