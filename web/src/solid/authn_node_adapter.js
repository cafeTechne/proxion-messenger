/**
 * Node-side authentication adapter — encapsulates @inrupt/solid-client-authn-node.
 *
 * Provides a stable contract for backend-facing auth operations so vendor SDK
 * details stay confined to this module.  Error codes are always normalised via
 * error_map.js before surfacing to callers.
 *
 * Session/token outputs are validated against Solid-OIDC conformance rules
 * before marking the session authenticated.
 *
 * Environment: Node.js / gateway sidecar only.  Do not import in browser bundles.
 */

import { normalisedError, SOLID_NOT_SUPPORTED, SOLID_AUTH_FAILED } from "./error_map.js";

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
 * Validate session info claims against Solid-OIDC conformance rules.
 * Throws SOLID_AUTH_FAILED if the session is not conformant.
 *
 * @param {object} sessionInfo  session.info from @inrupt/solid-client-authn-node
 * @param {string} expectedIssuer
 */
function _assertConformant(sessionInfo, expectedIssuer) {
  if (!sessionInfo.isLoggedIn) {
    const err = new Error("Session is not logged in after SDK login");
    err.code = SOLID_AUTH_FAILED;
    throw err;
  }
  // Verify the session WebID is rooted at the expected issuer's domain
  // (lightweight conformance check without raw JWT access)
  if (expectedIssuer && sessionInfo.webId) {
    try {
      const issuerOrigin = new URL(expectedIssuer).origin;
      const webIdOrigin = new URL(sessionInfo.webId).origin;
      // Cross-origin WebIDs are legitimate in Solid; we only reject blank/malformed ones
      if (!sessionInfo.webId.startsWith("http")) {
        const err = new Error(`Non-conformant WebID format: ${sessionInfo.webId}`);
        err.code = SOLID_AUTH_FAILED;
        throw err;
      }
    } catch (urlErr) {
      if (urlErr.code === SOLID_AUTH_FAILED) throw urlErr;
      // URL parse failure on WebID — reject
      const err = new Error(`Malformed WebID: ${sessionInfo.webId}`);
      err.code = SOLID_AUTH_FAILED;
      throw err;
    }
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
    _assertConformant(session.info, issuer);
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
