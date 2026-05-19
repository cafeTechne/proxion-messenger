/**
 * Access grants adapter — feature-gated @inrupt/solid-client-access-grants wrapper.
 *
 * Disabled by default.  Requires ``PROXION_ENABLE_ACCESS_GRANTS=1`` to activate.
 * All errors are normalised via error_map.js.
 *
 * This adapter enables delegated access patterns (third-party data sharing) without
 * changing the default direct-access trust model.
 */

import { normalisedError, SOLID_NOT_SUPPORTED } from "./error_map.js";

function _enabled() {
  if (typeof process !== "undefined") return process.env.PROXION_ENABLE_ACCESS_GRANTS === "1";
  if (typeof window !== "undefined") return window.PROXION_FLAGS?.PROXION_ENABLE_ACCESS_GRANTS === "1";
  return false;
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

/**
 * Request a delegated access grant.
 *
 * @param {object} opts
 * @param {string} opts.resourceUrl
 * @param {string[]} opts.modes       e.g. ["Read"]
 * @param {string} opts.requestorWebId
 * @param {object} [opts.fetchOptions]
 * @returns {Promise<object>}  The access grant object
 */
export async function requestGrant({ resourceUrl, modes, requestorWebId, fetchOptions }) {
  try {
    _gate("requestGrant");
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
