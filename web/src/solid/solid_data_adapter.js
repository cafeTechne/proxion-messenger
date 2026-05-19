/**
 * Solid data adapter — consolidates resource CRUD via @inrupt/solid-client.
 *
 * Replaces bespoke RDF parsing and raw fetch logic with maintained SDK
 * primitives.  Returns normalised data envelopes to preserve existing call
 * contracts during the phased rollout.
 *
 * Phase rollout is controlled by feature flags (read from window.PROXION_FLAGS
 * or process.env, depending on environment):
 *   PROXION_SOLID_USE_ADAPTER_GET=1   — adapter active for reads
 *   PROXION_SOLID_USE_ADAPTER_PUT=1   — adapter active for writes
 *   PROXION_SOLID_USE_ADAPTER_DELETE=1 — adapter active for deletes
 *   PROXION_SOLID_USE_ADAPTER_LIST=1  — adapter active for container listings
 */

import { normalisedError, SOLID_NOT_SUPPORTED } from "./error_map.js";

let _solidClient;
try {
  _solidClient = await import("@inrupt/solid-client");
} catch {
  _solidClient = null;
}

function _flag(name) {
  if (typeof process !== "undefined" && process.env?.[name] === "1") return true;
  if (typeof window !== "undefined" && window.PROXION_FLAGS?.[name]) return true;
  return false;
}

function _requireSdk(op) {
  if (!_solidClient) {
    const err = new Error(`@inrupt/solid-client is not available (op=${op})`);
    err.code = SOLID_NOT_SUPPORTED;
    throw err;
  }
}

/**
 * Read a resource.
 *
 * @param {string} url
 * @param {object} [fetchOptions]  Passed to the authenticated fetch
 * @returns {Promise<{url: string, body: string, contentType: string}>}
 */
export async function readResource(url, fetchOptions) {
  if (!_flag("PROXION_SOLID_USE_ADAPTER_GET")) {
    throw Object.assign(new Error("adapter disabled"), { code: SOLID_NOT_SUPPORTED });
  }
  try {
    _requireSdk("read");
    const dataset = await _solidClient.getSolidDataset(url, fetchOptions);
    return { url, dataset, contentType: "application/ld+json" };
  } catch (err) {
    throw normalisedError(err, `readResource:${url}`);
  }
}

/**
 * Write a resource (creates or replaces).
 *
 * @param {string} url
 * @param {string|Blob} body
 * @param {string} contentType
 * @param {object} [fetchOptions]
 * @returns {Promise<{url: string, etag: string|null}>}
 */
export async function writeResource(url, body, contentType, fetchOptions) {
  if (!_flag("PROXION_SOLID_USE_ADAPTER_PUT")) {
    throw Object.assign(new Error("adapter disabled"), { code: SOLID_NOT_SUPPORTED });
  }
  try {
    _requireSdk("write");
    const blob = body instanceof Blob ? body : new Blob([body], { type: contentType });
    const result = await _solidClient.overwriteFile(url, blob, { contentType, ...fetchOptions });
    return { url: result.internal_resourceInfo?.sourceIri ?? url, etag: null };
  } catch (err) {
    throw normalisedError(err, `writeResource:${url}`);
  }
}

/**
 * Delete a resource.
 *
 * @param {string} url
 * @param {object} [fetchOptions]
 * @returns {Promise<void>}
 */
export async function deleteResource(url, fetchOptions) {
  if (!_flag("PROXION_SOLID_USE_ADAPTER_DELETE")) {
    throw Object.assign(new Error("adapter disabled"), { code: SOLID_NOT_SUPPORTED });
  }
  try {
    _requireSdk("delete");
    await _solidClient.deleteFile(url, fetchOptions);
  } catch (err) {
    throw normalisedError(err, `deleteResource:${url}`);
  }
}

/**
 * List members of an LDP BasicContainer.
 *
 * @param {string} containerUrl  Must end with /
 * @param {object} [fetchOptions]
 * @returns {Promise<string[]>}  Member URLs
 */
export async function listContainer(containerUrl, fetchOptions) {
  if (!_flag("PROXION_SOLID_USE_ADAPTER_LIST")) {
    throw Object.assign(new Error("adapter disabled"), { code: SOLID_NOT_SUPPORTED });
  }
  try {
    _requireSdk("list");
    const dataset = await _solidClient.getSolidDataset(containerUrl, fetchOptions);
    return _solidClient.getContainedResourceUrlAll(dataset);
  } catch (err) {
    throw normalisedError(err, `listContainer:${containerUrl}`);
  }
}

/**
 * Conditional write helper — PUT with ETag precondition.
 *
 * @param {string} url
 * @param {string|Blob} body
 * @param {string} contentType
 * @param {string} etag  Value for If-Match header
 * @param {object} [fetchOptions]
 * @returns {Promise<{url: string, etag: string|null}>}
 */
export async function conditionalWrite(url, body, contentType, etag, fetchOptions) {
  if (!_flag("PROXION_SOLID_USE_ADAPTER_PUT")) {
    throw Object.assign(new Error("adapter disabled"), { code: SOLID_NOT_SUPPORTED });
  }
  try {
    _requireSdk("conditionalWrite");
    const headers = { "If-Match": etag };
    const merged = { ...fetchOptions, headers: { ...(fetchOptions?.headers ?? {}), ...headers } };
    return await writeResource(url, body, contentType, merged);
  } catch (err) {
    throw normalisedError(err, `conditionalWrite:${url}`);
  }
}
