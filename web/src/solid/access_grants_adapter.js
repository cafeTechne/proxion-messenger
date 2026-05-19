/**
 * Access grants adapter — feature-gated @inrupt/solid-client-access-grants wrapper.
 *
 * Disabled by default.  Requires ``PROXION_ENABLE_ACCESS_GRANTS=1`` to activate.
 * Issuer allowlist: ``PROXION_ACCESS_GRANTS_ISSUER_ALLOWLIST`` (comma-separated URLs).
 * Scope allowlist:  ``PROXION_ACCESS_GRANTS_SCOPE_ALLOWLIST`` (comma-separated modes).
 * All errors are normalised via error_map.js before surfacing to callers.
 */

import { normalisedError, SOLID_NOT_SUPPORTED, SOLID_FORBIDDEN } from "./error_map.js";

let _violationCount = 0;

function _enabled() {
  if (typeof process !== "undefined") return process.env.PROXION_ENABLE_ACCESS_GRANTS === "1";
  if (typeof window !== "undefined") return window.PROXION_FLAGS?.PROXION_ENABLE_ACCESS_GRANTS === "1";
  return false;
}

function _issuerAllowlist() {
  const raw =
    (typeof process !== "undefined" ? process.env.PROXION_ACCESS_GRANTS_ISSUER_ALLOWLIST : null) ??
    (typeof window !== "undefined" ? window.PROXION_FLAGS?.PROXION_ACCESS_GRANTS_ISSUER_ALLOWLIST : null) ??
    "";
  return raw ? raw.split(",").map(s => s.trim()).filter(Boolean) : [];
}

function _scopeAllowlist() {
  const raw =
    (typeof process !== "undefined" ? process.env.PROXION_ACCESS_GRANTS_SCOPE_ALLOWLIST : null) ??
    (typeof window !== "undefined" ? window.PROXION_FLAGS?.PROXION_ACCESS_GRANTS_SCOPE_ALLOWLIST : null) ??
    "";
  return raw ? raw.split(",").map(s => s.trim()).filter(Boolean) : [];
}

let _ag;
try {
  if (_enabled()) {
    _ag = await import("@inrupt/solid-client-access-grants");
  }
} catch {
  _ag = null;
}

function _gate(op) {
  if (!_enabled()) {
    const err = new Error(
      `Access grants are disabled. Set PROXION_ENABLE_ACCESS_GRANTS=1 to enable. (op=${op})`
    );
    err.code = SOLID_NOT_SUPPORTED;
    throw err;
  }
  if (!_ag) {
    const err = new Error(`@inrupt/solid-client-access-grants not available (op=${op})`);
    err.code = SOLID_NOT_SUPPORTED;
    throw err;
  }
}

function _checkIssuer(issuerUrl) {
  const allowlist = _issuerAllowlist();
  if (allowlist.length === 0) return; // no allowlist configured — pass-through
  if (!allowlist.includes(issuerUrl)) {
    _violationCount++;
    const err = new Error(
      `Issuer ${issuerUrl} is not in PROXION_ACCESS_GRANTS_ISSUER_ALLOWLIST`
    );
    err.code = "access_grant_issuer_violation";
    throw err;
  }
}

function _checkScopes(requestedModes) {
  const allowlist = _scopeAllowlist();
  if (allowlist.length === 0) return; // no allowlist configured — pass-through
  const denied = requestedModes.filter(m => !allowlist.includes(m));
  if (denied.length > 0) {
    _violationCount++;
    const err = new Error(
      `Requested modes [${denied.join(", ")}] are not in PROXION_ACCESS_GRANTS_SCOPE_ALLOWLIST`
    );
    err.code = "access_grant_scope_violation";
    throw err;
  }
}

/**
 * Request a delegated access grant.
 *
 * @param {object} opts
 * @param {string} opts.resourceUrl
 * @param {string[]} opts.modes       e.g. ["Read"]
 * @param {string} opts.requestorWebId
 * @param {string} [opts.issuerUrl]   Validated against issuer allowlist if provided
 * @param {object} [opts.fetchOptions]
 * @returns {Promise<object>}  The access grant object
 */
export async function requestGrant({ resourceUrl, modes, requestorWebId, issuerUrl, fetchOptions }) {
  try {
    _gate("requestGrant");
    if (issuerUrl) _checkIssuer(issuerUrl);
    _checkScopes(modes ?? []);
    return await _ag.issueAccessRequest(
      { resourceUrl, access: Object.fromEntries(modes.map(m => [m.toLowerCase(), true])), requestorWebId },
      fetchOptions,
    );
  } catch (err) {
    throw normalisedError(err, `requestGrant:${resourceUrl}`);
  }
}

/**
 * List access grants for a resource.
 *
 * @param {string} resourceUrl
 * @param {object} [fetchOptions]
 * @returns {Promise<object[]>}
 */
export async function listGrants(resourceUrl, fetchOptions) {
  try {
    _gate("listGrants");
    return await _ag.getAccessGrantAll(resourceUrl, fetchOptions);
  } catch (err) {
    throw normalisedError(err, `listGrants:${resourceUrl}`);
  }
}

/**
 * Revoke an existing access grant.
 *
 * @param {string} grantUrl   The URL of the grant to revoke
 * @param {object} [fetchOptions]
 * @returns {Promise<void>}
 */
export async function revokeGrant(grantUrl, fetchOptions) {
  try {
    _gate("revokeGrant");
    await _ag.revokeAccessGrant(grantUrl, fetchOptions);
  } catch (err) {
    throw normalisedError(err, `revokeGrant:${grantUrl}`);
  }
}

/**
 * Return the count of policy violations recorded this session.
 * @returns {number}
 */
export function getViolationCount() {
  return _violationCount;
}
