/**
 * Browser authentication adapter — encapsulates @inrupt/solid-client-authn-browser.
 *
 * Provides a stable, security-hardened interface for browser OIDC/WebID flows.
 * Anti-CSRF state validation is enforced around every auth callback so that
 * forged redirect completions are rejected.
 *
 * Session outputs are routed through Solid-OIDC conformance checks before
 * marking the session authenticated.  Non-conformant sessions are rejected even
 * if the SDK returns success.
 *
 * Environment: browser only.  Not safe to bundle in Node/gateway contexts.
 */

import { normalisedError, SOLID_NOT_SUPPORTED, SOLID_AUTH_FAILED } from "./error_map.js";

let _authn;
try {
  _authn = await import("@inrupt/solid-client-authn-browser");
} catch {
  _authn = null;
}

// In-memory CSRF state store (session-scoped — survives page reload via sessionStorage)
const _STATE_KEY = "proxion_solid_authn_state";
const _ISSUER_KEY = "proxion_solid_authn_issuer";

function _saveState(state, issuer) {
  try {
    sessionStorage.setItem(_STATE_KEY, state);
    sessionStorage.setItem(_ISSUER_KEY, issuer ?? "");
  } catch { /* private browsing */ }
}

function _loadAndClearState() {
  try {
    const s = sessionStorage.getItem(_STATE_KEY);
    const issuer = sessionStorage.getItem(_ISSUER_KEY) ?? "";
    sessionStorage.removeItem(_STATE_KEY);
    sessionStorage.removeItem(_ISSUER_KEY);
    return { state: s, issuer };
  } catch {
    return { state: null, issuer: "" };
  }
}

function _requireSdk() {
  if (!_authn) {
    const err = new Error("@inrupt/solid-client-authn-browser is not available");
    err.code = SOLID_NOT_SUPPORTED;
    throw err;
  }
}

/**
 * Assert that session info is Solid-OIDC conformant after login.
 * Rejects blank WebIDs and sessions that claim to be logged in without one.
 */
function _assertConformant(sessionInfo) {
  if (!sessionInfo.isLoggedIn) return; // Not yet logged in — conformance runs after redirect
  if (!sessionInfo.webId || !sessionInfo.webId.startsWith("http")) {
    const err = new Error(
      `Non-conformant WebID returned by OIDC session: ${sessionInfo.webId}`
    );
    err.code = SOLID_AUTH_FAILED;
    throw err;
  }
}

/**
 * Begin an OIDC login flow.  Redirects the browser to the issuer's auth endpoint.
 *
 * @param {object} opts
 * @param {string} opts.issuer     Solid OIDC issuer URL
 * @param {string} opts.clientId
 * @param {string} opts.redirectUrl
 * @returns {Promise<void>}
 */
export async function beginLogin({ issuer, clientId, redirectUrl }) {
  try {
    _requireSdk();
    const state = crypto.randomUUID();
    _saveState(state, issuer);
    await _authn.login({
      oidcIssuer: issuer,
      redirectUrl,
      clientId,
      tokenType: "DPoP",
    });
  } catch (err) {
    throw normalisedError(err, "beginLogin");
  }
}

/**
 * Complete the OIDC redirect callback.
 *
 * Validates the ``state`` parameter against the value saved by ``beginLogin``
 * before handing off to the SDK.  Throws ``SOLID_AUTH_FAILED`` on mismatch.
 * Runs Solid-OIDC conformance checks on the resulting session info.
 *
 * @param {string} [currentUrl]  Defaults to window.location.href
 * @returns {Promise<{webId: string, isLoggedIn: boolean}>}
 */
export async function completeLogin(currentUrl) {
  try {
    _requireSdk();
    const url = currentUrl ?? window.location.href;
    const params = new URL(url).searchParams;
    const returnedState = params.get("state");
    const { state: savedState } = _loadAndClearState();

    if (savedState && returnedState && returnedState !== savedState) {
      const err = new Error("CSRF state mismatch — auth callback rejected");
      err.code = SOLID_AUTH_FAILED;
      throw err;
    }

    await _authn.handleIncomingRedirect(url);
    const session = _authn.getDefaultSession();
    _assertConformant(session.info);
    return { webId: session.info.webId ?? null, isLoggedIn: session.info.isLoggedIn };
  } catch (err) {
    throw normalisedError(err, "completeLogin");
  }
}

/**
 * Log out the current session.
 *
 * @param {object} [opts]
 * @param {string} [opts.logoutUrl]
 * @returns {Promise<void>}
 */
export async function logout(opts = {}) {
  try {
    _requireSdk();
    const session = _authn.getDefaultSession();
    await session.logout(opts);
  } catch (err) {
    throw normalisedError(err, "logout");
  }
}

/**
 * @returns {boolean}
 */
export function isLoggedIn() {
  if (!_authn) return false;
  return _authn.getDefaultSession().info.isLoggedIn;
}

/**
 * @returns {string|null}
 */
export function getWebId() {
  if (!_authn) return null;
  return _authn.getDefaultSession().info.webId ?? null;
}
