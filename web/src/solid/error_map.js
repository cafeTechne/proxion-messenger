/**
 * Solid SDK migration — normalised error codes and translation map.
 *
 * Translates SDK, HTTP, and adapter errors into stable application codes
 * so error handlers remain correct across the legacy→Inrupt SDK transition.
 */

export const SOLID_AUTH_REQUIRED = "SOLID_AUTH_REQUIRED";
export const SOLID_AUTH_FAILED = "SOLID_AUTH_FAILED";
export const SOLID_FORBIDDEN = "SOLID_FORBIDDEN";
export const SOLID_NOT_FOUND = "SOLID_NOT_FOUND";
export const SOLID_CONFLICT = "SOLID_CONFLICT";
export const SOLID_PRECONDITION_FAILED = "SOLID_PRECONDITION_FAILED";
export const SOLID_NETWORK_UNAVAILABLE = "SOLID_NETWORK_UNAVAILABLE";
export const SOLID_NOT_SUPPORTED = "SOLID_NOT_SUPPORTED";

const HTTP_TO_CODE = {
  401: SOLID_AUTH_REQUIRED,
  403: SOLID_FORBIDDEN,
  404: SOLID_NOT_FOUND,
  409: SOLID_CONFLICT,
  412: SOLID_PRECONDITION_FAILED,
};

/**
 * Translate an HTTP status code to a normalised error code.
 * @param {number} status
 * @returns {string}
 */
export function httpStatusToCode(status) {
  return HTTP_TO_CODE[status] ?? SOLID_AUTH_FAILED;
}

/**
 * Translate any thrown error to a normalised error code.
 *
 * Checks:
 * 1. If the error has a ``code`` property already set to a known code, return it.
 * 2. If the error has a ``statusCode`` or ``status`` property, map via httpStatusToCode.
 * 3. Heuristic string match on the message for network/auth keywords.
 * 4. Default: SOLID_AUTH_FAILED.
 *
 * @param {Error|unknown} err
 * @returns {string}
 */
export function errorToCode(err) {
  if (!err) return SOLID_AUTH_FAILED;

  const known = new Set([
    SOLID_AUTH_REQUIRED, SOLID_AUTH_FAILED, SOLID_FORBIDDEN, SOLID_NOT_FOUND,
    SOLID_CONFLICT, SOLID_PRECONDITION_FAILED, SOLID_NETWORK_UNAVAILABLE,
    SOLID_NOT_SUPPORTED,
  ]);

  if (typeof err.code === "string" && known.has(err.code)) return err.code;

  const status = err.statusCode ?? err.status;
  if (typeof status === "number" && HTTP_TO_CODE[status]) return HTTP_TO_CODE[status];

  const msg = String(err.message ?? err).toLowerCase();
  if (msg.includes("network") || msg.includes("fetch") || msg.includes("econnrefused")) {
    return SOLID_NETWORK_UNAVAILABLE;
  }
  if (msg.includes("not supported") || msg.includes("unsupported")) {
    return SOLID_NOT_SUPPORTED;
  }
  if (msg.includes("unauthorized") || msg.includes("401")) return SOLID_AUTH_REQUIRED;
  if (msg.includes("forbidden") || msg.includes("403")) return SOLID_FORBIDDEN;
  if (msg.includes("not found") || msg.includes("404")) return SOLID_NOT_FOUND;
  if (msg.includes("conflict") || msg.includes("409")) return SOLID_CONFLICT;

  return SOLID_AUTH_FAILED;
}

/**
 * Wrap an error in a normalised SolidError with a stable ``code`` property.
 * @param {Error|unknown} err
 * @param {string} [context]
 * @returns {Error}
 */
export function normalisedError(err, context) {
  const code = errorToCode(err);
  const base = err instanceof Error ? err : new Error(String(err));
  base.code = code;
  if (context) base.context = context;
  return base;
}
