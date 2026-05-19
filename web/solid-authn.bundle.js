var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __esm = (fn, res) => function __init() {
  return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
};
var __commonJS = (cb, mod) => function __require() {
  return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
};
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// node_modules/jose/dist/browser/runtime/webcrypto.js
var webcrypto_default, isCryptoKey;
var init_webcrypto = __esm({
  "node_modules/jose/dist/browser/runtime/webcrypto.js"() {
    webcrypto_default = crypto;
    isCryptoKey = (key) => key instanceof CryptoKey;
  }
});

// node_modules/jose/dist/browser/runtime/digest.js
var digest, digest_default;
var init_digest = __esm({
  "node_modules/jose/dist/browser/runtime/digest.js"() {
    init_webcrypto();
    digest = async (algorithm, data) => {
      const subtleDigest = `SHA-${algorithm.slice(-3)}`;
      return new Uint8Array(await webcrypto_default.subtle.digest(subtleDigest, data));
    };
    digest_default = digest;
  }
});

// node_modules/jose/dist/browser/lib/buffer_utils.js
function concat(...buffers) {
  const size = buffers.reduce((acc, { length }) => acc + length, 0);
  const buf = new Uint8Array(size);
  let i = 0;
  for (const buffer of buffers) {
    buf.set(buffer, i);
    i += buffer.length;
  }
  return buf;
}
function p2s(alg, p2sInput) {
  return concat(encoder.encode(alg), new Uint8Array([0]), p2sInput);
}
function writeUInt32BE(buf, value, offset) {
  if (value < 0 || value >= MAX_INT32) {
    throw new RangeError(`value must be >= 0 and <= ${MAX_INT32 - 1}. Received ${value}`);
  }
  buf.set([value >>> 24, value >>> 16, value >>> 8, value & 255], offset);
}
function uint64be(value) {
  const high = Math.floor(value / MAX_INT32);
  const low = value % MAX_INT32;
  const buf = new Uint8Array(8);
  writeUInt32BE(buf, high, 0);
  writeUInt32BE(buf, low, 4);
  return buf;
}
function uint32be(value) {
  const buf = new Uint8Array(4);
  writeUInt32BE(buf, value);
  return buf;
}
function lengthAndInput(input) {
  return concat(uint32be(input.length), input);
}
async function concatKdf(secret, bits, value) {
  const iterations = Math.ceil((bits >> 3) / 32);
  const res = new Uint8Array(iterations * 32);
  for (let iter = 0; iter < iterations; iter++) {
    const buf = new Uint8Array(4 + secret.length + value.length);
    buf.set(uint32be(iter + 1));
    buf.set(secret, 4);
    buf.set(value, 4 + secret.length);
    res.set(await digest_default("sha256", buf), iter * 32);
  }
  return res.slice(0, bits >> 3);
}
var encoder, decoder, MAX_INT32;
var init_buffer_utils = __esm({
  "node_modules/jose/dist/browser/lib/buffer_utils.js"() {
    init_digest();
    encoder = new TextEncoder();
    decoder = new TextDecoder();
    MAX_INT32 = 2 ** 32;
  }
});

// node_modules/jose/dist/browser/runtime/base64url.js
var encodeBase64, encode, decodeBase64, decode;
var init_base64url = __esm({
  "node_modules/jose/dist/browser/runtime/base64url.js"() {
    init_buffer_utils();
    encodeBase64 = (input) => {
      let unencoded = input;
      if (typeof unencoded === "string") {
        unencoded = encoder.encode(unencoded);
      }
      const CHUNK_SIZE = 32768;
      const arr = [];
      for (let i = 0; i < unencoded.length; i += CHUNK_SIZE) {
        arr.push(String.fromCharCode.apply(null, unencoded.subarray(i, i + CHUNK_SIZE)));
      }
      return btoa(arr.join(""));
    };
    encode = (input) => {
      return encodeBase64(input).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
    };
    decodeBase64 = (encoded) => {
      const binary = atob(encoded);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      return bytes;
    };
    decode = (input) => {
      let encoded = input;
      if (encoded instanceof Uint8Array) {
        encoded = decoder.decode(encoded);
      }
      encoded = encoded.replace(/-/g, "+").replace(/_/g, "/").replace(/\s/g, "");
      try {
        return decodeBase64(encoded);
      } catch {
        throw new TypeError("The input to be decoded is not correctly encoded.");
      }
    };
  }
});

// node_modules/jose/dist/browser/util/errors.js
var errors_exports = {};
__export(errors_exports, {
  JOSEAlgNotAllowed: () => JOSEAlgNotAllowed,
  JOSEError: () => JOSEError,
  JOSENotSupported: () => JOSENotSupported,
  JWEDecryptionFailed: () => JWEDecryptionFailed,
  JWEInvalid: () => JWEInvalid,
  JWKInvalid: () => JWKInvalid,
  JWKSInvalid: () => JWKSInvalid,
  JWKSMultipleMatchingKeys: () => JWKSMultipleMatchingKeys,
  JWKSNoMatchingKey: () => JWKSNoMatchingKey,
  JWKSTimeout: () => JWKSTimeout,
  JWSInvalid: () => JWSInvalid,
  JWSSignatureVerificationFailed: () => JWSSignatureVerificationFailed,
  JWTClaimValidationFailed: () => JWTClaimValidationFailed,
  JWTExpired: () => JWTExpired,
  JWTInvalid: () => JWTInvalid
});
var JOSEError, JWTClaimValidationFailed, JWTExpired, JOSEAlgNotAllowed, JOSENotSupported, JWEDecryptionFailed, JWEInvalid, JWSInvalid, JWTInvalid, JWKInvalid, JWKSInvalid, JWKSNoMatchingKey, JWKSMultipleMatchingKeys, JWKSTimeout, JWSSignatureVerificationFailed;
var init_errors = __esm({
  "node_modules/jose/dist/browser/util/errors.js"() {
    JOSEError = class extends Error {
      constructor(message2, options) {
        super(message2, options);
        this.code = "ERR_JOSE_GENERIC";
        this.name = this.constructor.name;
        Error.captureStackTrace?.(this, this.constructor);
      }
    };
    JOSEError.code = "ERR_JOSE_GENERIC";
    JWTClaimValidationFailed = class extends JOSEError {
      constructor(message2, payload, claim = "unspecified", reason = "unspecified") {
        super(message2, { cause: { claim, reason, payload } });
        this.code = "ERR_JWT_CLAIM_VALIDATION_FAILED";
        this.claim = claim;
        this.reason = reason;
        this.payload = payload;
      }
    };
    JWTClaimValidationFailed.code = "ERR_JWT_CLAIM_VALIDATION_FAILED";
    JWTExpired = class extends JOSEError {
      constructor(message2, payload, claim = "unspecified", reason = "unspecified") {
        super(message2, { cause: { claim, reason, payload } });
        this.code = "ERR_JWT_EXPIRED";
        this.claim = claim;
        this.reason = reason;
        this.payload = payload;
      }
    };
    JWTExpired.code = "ERR_JWT_EXPIRED";
    JOSEAlgNotAllowed = class extends JOSEError {
      constructor() {
        super(...arguments);
        this.code = "ERR_JOSE_ALG_NOT_ALLOWED";
      }
    };
    JOSEAlgNotAllowed.code = "ERR_JOSE_ALG_NOT_ALLOWED";
    JOSENotSupported = class extends JOSEError {
      constructor() {
        super(...arguments);
        this.code = "ERR_JOSE_NOT_SUPPORTED";
      }
    };
    JOSENotSupported.code = "ERR_JOSE_NOT_SUPPORTED";
    JWEDecryptionFailed = class extends JOSEError {
      constructor(message2 = "decryption operation failed", options) {
        super(message2, options);
        this.code = "ERR_JWE_DECRYPTION_FAILED";
      }
    };
    JWEDecryptionFailed.code = "ERR_JWE_DECRYPTION_FAILED";
    JWEInvalid = class extends JOSEError {
      constructor() {
        super(...arguments);
        this.code = "ERR_JWE_INVALID";
      }
    };
    JWEInvalid.code = "ERR_JWE_INVALID";
    JWSInvalid = class extends JOSEError {
      constructor() {
        super(...arguments);
        this.code = "ERR_JWS_INVALID";
      }
    };
    JWSInvalid.code = "ERR_JWS_INVALID";
    JWTInvalid = class extends JOSEError {
      constructor() {
        super(...arguments);
        this.code = "ERR_JWT_INVALID";
      }
    };
    JWTInvalid.code = "ERR_JWT_INVALID";
    JWKInvalid = class extends JOSEError {
      constructor() {
        super(...arguments);
        this.code = "ERR_JWK_INVALID";
      }
    };
    JWKInvalid.code = "ERR_JWK_INVALID";
    JWKSInvalid = class extends JOSEError {
      constructor() {
        super(...arguments);
        this.code = "ERR_JWKS_INVALID";
      }
    };
    JWKSInvalid.code = "ERR_JWKS_INVALID";
    JWKSNoMatchingKey = class extends JOSEError {
      constructor(message2 = "no applicable key found in the JSON Web Key Set", options) {
        super(message2, options);
        this.code = "ERR_JWKS_NO_MATCHING_KEY";
      }
    };
    JWKSNoMatchingKey.code = "ERR_JWKS_NO_MATCHING_KEY";
    JWKSMultipleMatchingKeys = class extends JOSEError {
      constructor(message2 = "multiple matching keys found in the JSON Web Key Set", options) {
        super(message2, options);
        this.code = "ERR_JWKS_MULTIPLE_MATCHING_KEYS";
      }
    };
    JWKSMultipleMatchingKeys.code = "ERR_JWKS_MULTIPLE_MATCHING_KEYS";
    JWKSTimeout = class extends JOSEError {
      constructor(message2 = "request timed out", options) {
        super(message2, options);
        this.code = "ERR_JWKS_TIMEOUT";
      }
    };
    JWKSTimeout.code = "ERR_JWKS_TIMEOUT";
    JWSSignatureVerificationFailed = class extends JOSEError {
      constructor(message2 = "signature verification failed", options) {
        super(message2, options);
        this.code = "ERR_JWS_SIGNATURE_VERIFICATION_FAILED";
      }
    };
    JWSSignatureVerificationFailed.code = "ERR_JWS_SIGNATURE_VERIFICATION_FAILED";
  }
});

// node_modules/jose/dist/browser/runtime/random.js
var random_default;
var init_random = __esm({
  "node_modules/jose/dist/browser/runtime/random.js"() {
    init_webcrypto();
    random_default = webcrypto_default.getRandomValues.bind(webcrypto_default);
  }
});

// node_modules/jose/dist/browser/lib/iv.js
function bitLength(alg) {
  switch (alg) {
    case "A128GCM":
    case "A128GCMKW":
    case "A192GCM":
    case "A192GCMKW":
    case "A256GCM":
    case "A256GCMKW":
      return 96;
    case "A128CBC-HS256":
    case "A192CBC-HS384":
    case "A256CBC-HS512":
      return 128;
    default:
      throw new JOSENotSupported(`Unsupported JWE Algorithm: ${alg}`);
  }
}
var iv_default;
var init_iv = __esm({
  "node_modules/jose/dist/browser/lib/iv.js"() {
    init_errors();
    init_random();
    iv_default = (alg) => random_default(new Uint8Array(bitLength(alg) >> 3));
  }
});

// node_modules/jose/dist/browser/lib/check_iv_length.js
var checkIvLength, check_iv_length_default;
var init_check_iv_length = __esm({
  "node_modules/jose/dist/browser/lib/check_iv_length.js"() {
    init_errors();
    init_iv();
    checkIvLength = (enc, iv) => {
      if (iv.length << 3 !== bitLength(enc)) {
        throw new JWEInvalid("Invalid Initialization Vector length");
      }
    };
    check_iv_length_default = checkIvLength;
  }
});

// node_modules/jose/dist/browser/runtime/check_cek_length.js
var checkCekLength, check_cek_length_default;
var init_check_cek_length = __esm({
  "node_modules/jose/dist/browser/runtime/check_cek_length.js"() {
    init_errors();
    checkCekLength = (cek, expected) => {
      const actual = cek.byteLength << 3;
      if (actual !== expected) {
        throw new JWEInvalid(`Invalid Content Encryption Key length. Expected ${expected} bits, got ${actual} bits`);
      }
    };
    check_cek_length_default = checkCekLength;
  }
});

// node_modules/jose/dist/browser/runtime/timing_safe_equal.js
var timingSafeEqual, timing_safe_equal_default;
var init_timing_safe_equal = __esm({
  "node_modules/jose/dist/browser/runtime/timing_safe_equal.js"() {
    timingSafeEqual = (a, b) => {
      if (!(a instanceof Uint8Array)) {
        throw new TypeError("First argument must be a buffer");
      }
      if (!(b instanceof Uint8Array)) {
        throw new TypeError("Second argument must be a buffer");
      }
      if (a.length !== b.length) {
        throw new TypeError("Input buffers must have the same length");
      }
      const len = a.length;
      let out = 0;
      let i = -1;
      while (++i < len) {
        out |= a[i] ^ b[i];
      }
      return out === 0;
    };
    timing_safe_equal_default = timingSafeEqual;
  }
});

// node_modules/jose/dist/browser/lib/crypto_key.js
function unusable(name, prop = "algorithm.name") {
  return new TypeError(`CryptoKey does not support this operation, its ${prop} must be ${name}`);
}
function isAlgorithm(algorithm, name) {
  return algorithm.name === name;
}
function getHashLength(hash) {
  return parseInt(hash.name.slice(4), 10);
}
function getNamedCurve(alg) {
  switch (alg) {
    case "ES256":
      return "P-256";
    case "ES384":
      return "P-384";
    case "ES512":
      return "P-521";
    default:
      throw new Error("unreachable");
  }
}
function checkUsage(key, usages) {
  if (usages.length && !usages.some((expected) => key.usages.includes(expected))) {
    let msg = "CryptoKey does not support this operation, its usages must include ";
    if (usages.length > 2) {
      const last = usages.pop();
      msg += `one of ${usages.join(", ")}, or ${last}.`;
    } else if (usages.length === 2) {
      msg += `one of ${usages[0]} or ${usages[1]}.`;
    } else {
      msg += `${usages[0]}.`;
    }
    throw new TypeError(msg);
  }
}
function checkSigCryptoKey(key, alg, ...usages) {
  switch (alg) {
    case "HS256":
    case "HS384":
    case "HS512": {
      if (!isAlgorithm(key.algorithm, "HMAC"))
        throw unusable("HMAC");
      const expected = parseInt(alg.slice(2), 10);
      const actual = getHashLength(key.algorithm.hash);
      if (actual !== expected)
        throw unusable(`SHA-${expected}`, "algorithm.hash");
      break;
    }
    case "RS256":
    case "RS384":
    case "RS512": {
      if (!isAlgorithm(key.algorithm, "RSASSA-PKCS1-v1_5"))
        throw unusable("RSASSA-PKCS1-v1_5");
      const expected = parseInt(alg.slice(2), 10);
      const actual = getHashLength(key.algorithm.hash);
      if (actual !== expected)
        throw unusable(`SHA-${expected}`, "algorithm.hash");
      break;
    }
    case "PS256":
    case "PS384":
    case "PS512": {
      if (!isAlgorithm(key.algorithm, "RSA-PSS"))
        throw unusable("RSA-PSS");
      const expected = parseInt(alg.slice(2), 10);
      const actual = getHashLength(key.algorithm.hash);
      if (actual !== expected)
        throw unusable(`SHA-${expected}`, "algorithm.hash");
      break;
    }
    case "EdDSA": {
      if (key.algorithm.name !== "Ed25519" && key.algorithm.name !== "Ed448") {
        throw unusable("Ed25519 or Ed448");
      }
      break;
    }
    case "Ed25519": {
      if (!isAlgorithm(key.algorithm, "Ed25519"))
        throw unusable("Ed25519");
      break;
    }
    case "ES256":
    case "ES384":
    case "ES512": {
      if (!isAlgorithm(key.algorithm, "ECDSA"))
        throw unusable("ECDSA");
      const expected = getNamedCurve(alg);
      const actual = key.algorithm.namedCurve;
      if (actual !== expected)
        throw unusable(expected, "algorithm.namedCurve");
      break;
    }
    default:
      throw new TypeError("CryptoKey does not support this operation");
  }
  checkUsage(key, usages);
}
function checkEncCryptoKey(key, alg, ...usages) {
  switch (alg) {
    case "A128GCM":
    case "A192GCM":
    case "A256GCM": {
      if (!isAlgorithm(key.algorithm, "AES-GCM"))
        throw unusable("AES-GCM");
      const expected = parseInt(alg.slice(1, 4), 10);
      const actual = key.algorithm.length;
      if (actual !== expected)
        throw unusable(expected, "algorithm.length");
      break;
    }
    case "A128KW":
    case "A192KW":
    case "A256KW": {
      if (!isAlgorithm(key.algorithm, "AES-KW"))
        throw unusable("AES-KW");
      const expected = parseInt(alg.slice(1, 4), 10);
      const actual = key.algorithm.length;
      if (actual !== expected)
        throw unusable(expected, "algorithm.length");
      break;
    }
    case "ECDH": {
      switch (key.algorithm.name) {
        case "ECDH":
        case "X25519":
        case "X448":
          break;
        default:
          throw unusable("ECDH, X25519, or X448");
      }
      break;
    }
    case "PBES2-HS256+A128KW":
    case "PBES2-HS384+A192KW":
    case "PBES2-HS512+A256KW":
      if (!isAlgorithm(key.algorithm, "PBKDF2"))
        throw unusable("PBKDF2");
      break;
    case "RSA-OAEP":
    case "RSA-OAEP-256":
    case "RSA-OAEP-384":
    case "RSA-OAEP-512": {
      if (!isAlgorithm(key.algorithm, "RSA-OAEP"))
        throw unusable("RSA-OAEP");
      const expected = parseInt(alg.slice(9), 10) || 1;
      const actual = getHashLength(key.algorithm.hash);
      if (actual !== expected)
        throw unusable(`SHA-${expected}`, "algorithm.hash");
      break;
    }
    default:
      throw new TypeError("CryptoKey does not support this operation");
  }
  checkUsage(key, usages);
}
var init_crypto_key = __esm({
  "node_modules/jose/dist/browser/lib/crypto_key.js"() {
  }
});

// node_modules/jose/dist/browser/lib/invalid_key_input.js
function message(msg, actual, ...types2) {
  types2 = types2.filter(Boolean);
  if (types2.length > 2) {
    const last = types2.pop();
    msg += `one of type ${types2.join(", ")}, or ${last}.`;
  } else if (types2.length === 2) {
    msg += `one of type ${types2[0]} or ${types2[1]}.`;
  } else {
    msg += `of type ${types2[0]}.`;
  }
  if (actual == null) {
    msg += ` Received ${actual}`;
  } else if (typeof actual === "function" && actual.name) {
    msg += ` Received function ${actual.name}`;
  } else if (typeof actual === "object" && actual != null) {
    if (actual.constructor?.name) {
      msg += ` Received an instance of ${actual.constructor.name}`;
    }
  }
  return msg;
}
function withAlg(alg, actual, ...types2) {
  return message(`Key for the ${alg} algorithm must be `, actual, ...types2);
}
var invalid_key_input_default;
var init_invalid_key_input = __esm({
  "node_modules/jose/dist/browser/lib/invalid_key_input.js"() {
    invalid_key_input_default = (actual, ...types2) => {
      return message("Key must be ", actual, ...types2);
    };
  }
});

// node_modules/jose/dist/browser/runtime/is_key_like.js
var is_key_like_default, types;
var init_is_key_like = __esm({
  "node_modules/jose/dist/browser/runtime/is_key_like.js"() {
    init_webcrypto();
    is_key_like_default = (key) => {
      if (isCryptoKey(key)) {
        return true;
      }
      return key?.[Symbol.toStringTag] === "KeyObject";
    };
    types = ["CryptoKey"];
  }
});

// node_modules/jose/dist/browser/runtime/decrypt.js
async function cbcDecrypt(enc, cek, ciphertext, iv, tag2, aad) {
  if (!(cek instanceof Uint8Array)) {
    throw new TypeError(invalid_key_input_default(cek, "Uint8Array"));
  }
  const keySize = parseInt(enc.slice(1, 4), 10);
  const encKey = await webcrypto_default.subtle.importKey("raw", cek.subarray(keySize >> 3), "AES-CBC", false, ["decrypt"]);
  const macKey = await webcrypto_default.subtle.importKey("raw", cek.subarray(0, keySize >> 3), {
    hash: `SHA-${keySize << 1}`,
    name: "HMAC"
  }, false, ["sign"]);
  const macData = concat(aad, iv, ciphertext, uint64be(aad.length << 3));
  const expectedTag = new Uint8Array((await webcrypto_default.subtle.sign("HMAC", macKey, macData)).slice(0, keySize >> 3));
  let macCheckPassed;
  try {
    macCheckPassed = timing_safe_equal_default(tag2, expectedTag);
  } catch {
  }
  if (!macCheckPassed) {
    throw new JWEDecryptionFailed();
  }
  let plaintext;
  try {
    plaintext = new Uint8Array(await webcrypto_default.subtle.decrypt({ iv, name: "AES-CBC" }, encKey, ciphertext));
  } catch {
  }
  if (!plaintext) {
    throw new JWEDecryptionFailed();
  }
  return plaintext;
}
async function gcmDecrypt(enc, cek, ciphertext, iv, tag2, aad) {
  let encKey;
  if (cek instanceof Uint8Array) {
    encKey = await webcrypto_default.subtle.importKey("raw", cek, "AES-GCM", false, ["decrypt"]);
  } else {
    checkEncCryptoKey(cek, enc, "decrypt");
    encKey = cek;
  }
  try {
    return new Uint8Array(await webcrypto_default.subtle.decrypt({
      additionalData: aad,
      iv,
      name: "AES-GCM",
      tagLength: 128
    }, encKey, concat(ciphertext, tag2)));
  } catch {
    throw new JWEDecryptionFailed();
  }
}
var decrypt, decrypt_default;
var init_decrypt = __esm({
  "node_modules/jose/dist/browser/runtime/decrypt.js"() {
    init_buffer_utils();
    init_check_iv_length();
    init_check_cek_length();
    init_timing_safe_equal();
    init_errors();
    init_webcrypto();
    init_crypto_key();
    init_invalid_key_input();
    init_is_key_like();
    decrypt = async (enc, cek, ciphertext, iv, tag2, aad) => {
      if (!isCryptoKey(cek) && !(cek instanceof Uint8Array)) {
        throw new TypeError(invalid_key_input_default(cek, ...types, "Uint8Array"));
      }
      if (!iv) {
        throw new JWEInvalid("JWE Initialization Vector missing");
      }
      if (!tag2) {
        throw new JWEInvalid("JWE Authentication Tag missing");
      }
      check_iv_length_default(enc, iv);
      switch (enc) {
        case "A128CBC-HS256":
        case "A192CBC-HS384":
        case "A256CBC-HS512":
          if (cek instanceof Uint8Array)
            check_cek_length_default(cek, parseInt(enc.slice(-3), 10));
          return cbcDecrypt(enc, cek, ciphertext, iv, tag2, aad);
        case "A128GCM":
        case "A192GCM":
        case "A256GCM":
          if (cek instanceof Uint8Array)
            check_cek_length_default(cek, parseInt(enc.slice(1, 4), 10));
          return gcmDecrypt(enc, cek, ciphertext, iv, tag2, aad);
        default:
          throw new JOSENotSupported("Unsupported JWE Content Encryption Algorithm");
      }
    };
    decrypt_default = decrypt;
  }
});

// node_modules/jose/dist/browser/lib/is_disjoint.js
var isDisjoint, is_disjoint_default;
var init_is_disjoint = __esm({
  "node_modules/jose/dist/browser/lib/is_disjoint.js"() {
    isDisjoint = (...headers) => {
      const sources = headers.filter(Boolean);
      if (sources.length === 0 || sources.length === 1) {
        return true;
      }
      let acc;
      for (const header of sources) {
        const parameters = Object.keys(header);
        if (!acc || acc.size === 0) {
          acc = new Set(parameters);
          continue;
        }
        for (const parameter of parameters) {
          if (acc.has(parameter)) {
            return false;
          }
          acc.add(parameter);
        }
      }
      return true;
    };
    is_disjoint_default = isDisjoint;
  }
});

// node_modules/jose/dist/browser/lib/is_object.js
function isObjectLike(value) {
  return typeof value === "object" && value !== null;
}
function isObject(input) {
  if (!isObjectLike(input) || Object.prototype.toString.call(input) !== "[object Object]") {
    return false;
  }
  if (Object.getPrototypeOf(input) === null) {
    return true;
  }
  let proto = input;
  while (Object.getPrototypeOf(proto) !== null) {
    proto = Object.getPrototypeOf(proto);
  }
  return Object.getPrototypeOf(input) === proto;
}
var init_is_object = __esm({
  "node_modules/jose/dist/browser/lib/is_object.js"() {
  }
});

// node_modules/jose/dist/browser/runtime/bogus.js
var bogusWebCrypto, bogus_default;
var init_bogus = __esm({
  "node_modules/jose/dist/browser/runtime/bogus.js"() {
    bogusWebCrypto = [
      { hash: "SHA-256", name: "HMAC" },
      true,
      ["sign"]
    ];
    bogus_default = bogusWebCrypto;
  }
});

// node_modules/jose/dist/browser/runtime/aeskw.js
function checkKeySize(key, alg) {
  if (key.algorithm.length !== parseInt(alg.slice(1, 4), 10)) {
    throw new TypeError(`Invalid key size for alg: ${alg}`);
  }
}
function getCryptoKey(key, alg, usage) {
  if (isCryptoKey(key)) {
    checkEncCryptoKey(key, alg, usage);
    return key;
  }
  if (key instanceof Uint8Array) {
    return webcrypto_default.subtle.importKey("raw", key, "AES-KW", true, [usage]);
  }
  throw new TypeError(invalid_key_input_default(key, ...types, "Uint8Array"));
}
var wrap, unwrap;
var init_aeskw = __esm({
  "node_modules/jose/dist/browser/runtime/aeskw.js"() {
    init_bogus();
    init_webcrypto();
    init_crypto_key();
    init_invalid_key_input();
    init_is_key_like();
    wrap = async (alg, key, cek) => {
      const cryptoKey = await getCryptoKey(key, alg, "wrapKey");
      checkKeySize(cryptoKey, alg);
      const cryptoKeyCek = await webcrypto_default.subtle.importKey("raw", cek, ...bogus_default);
      return new Uint8Array(await webcrypto_default.subtle.wrapKey("raw", cryptoKeyCek, cryptoKey, "AES-KW"));
    };
    unwrap = async (alg, key, encryptedKey) => {
      const cryptoKey = await getCryptoKey(key, alg, "unwrapKey");
      checkKeySize(cryptoKey, alg);
      const cryptoKeyCek = await webcrypto_default.subtle.unwrapKey("raw", encryptedKey, cryptoKey, "AES-KW", ...bogus_default);
      return new Uint8Array(await webcrypto_default.subtle.exportKey("raw", cryptoKeyCek));
    };
  }
});

// node_modules/jose/dist/browser/runtime/ecdhes.js
async function deriveKey(publicKey, privateKey, algorithm, keyLength, apu = new Uint8Array(0), apv = new Uint8Array(0)) {
  if (!isCryptoKey(publicKey)) {
    throw new TypeError(invalid_key_input_default(publicKey, ...types));
  }
  checkEncCryptoKey(publicKey, "ECDH");
  if (!isCryptoKey(privateKey)) {
    throw new TypeError(invalid_key_input_default(privateKey, ...types));
  }
  checkEncCryptoKey(privateKey, "ECDH", "deriveBits");
  const value = concat(lengthAndInput(encoder.encode(algorithm)), lengthAndInput(apu), lengthAndInput(apv), uint32be(keyLength));
  let length;
  if (publicKey.algorithm.name === "X25519") {
    length = 256;
  } else if (publicKey.algorithm.name === "X448") {
    length = 448;
  } else {
    length = Math.ceil(parseInt(publicKey.algorithm.namedCurve.substr(-3), 10) / 8) << 3;
  }
  const sharedSecret = new Uint8Array(await webcrypto_default.subtle.deriveBits({
    name: publicKey.algorithm.name,
    public: publicKey
  }, privateKey, length));
  return concatKdf(sharedSecret, keyLength, value);
}
async function generateEpk(key) {
  if (!isCryptoKey(key)) {
    throw new TypeError(invalid_key_input_default(key, ...types));
  }
  return webcrypto_default.subtle.generateKey(key.algorithm, true, ["deriveBits"]);
}
function ecdhAllowed(key) {
  if (!isCryptoKey(key)) {
    throw new TypeError(invalid_key_input_default(key, ...types));
  }
  return ["P-256", "P-384", "P-521"].includes(key.algorithm.namedCurve) || key.algorithm.name === "X25519" || key.algorithm.name === "X448";
}
var init_ecdhes = __esm({
  "node_modules/jose/dist/browser/runtime/ecdhes.js"() {
    init_buffer_utils();
    init_webcrypto();
    init_crypto_key();
    init_invalid_key_input();
    init_is_key_like();
  }
});

// node_modules/jose/dist/browser/lib/check_p2s.js
function checkP2s(p2s2) {
  if (!(p2s2 instanceof Uint8Array) || p2s2.length < 8) {
    throw new JWEInvalid("PBES2 Salt Input must be 8 or more octets");
  }
}
var init_check_p2s = __esm({
  "node_modules/jose/dist/browser/lib/check_p2s.js"() {
    init_errors();
  }
});

// node_modules/jose/dist/browser/runtime/pbes2kw.js
function getCryptoKey2(key, alg) {
  if (key instanceof Uint8Array) {
    return webcrypto_default.subtle.importKey("raw", key, "PBKDF2", false, ["deriveBits"]);
  }
  if (isCryptoKey(key)) {
    checkEncCryptoKey(key, alg, "deriveBits", "deriveKey");
    return key;
  }
  throw new TypeError(invalid_key_input_default(key, ...types, "Uint8Array"));
}
async function deriveKey2(p2s2, alg, p2c, key) {
  checkP2s(p2s2);
  const salt = p2s(alg, p2s2);
  const keylen = parseInt(alg.slice(13, 16), 10);
  const subtleAlg = {
    hash: `SHA-${alg.slice(8, 11)}`,
    iterations: p2c,
    name: "PBKDF2",
    salt
  };
  const wrapAlg = {
    length: keylen,
    name: "AES-KW"
  };
  const cryptoKey = await getCryptoKey2(key, alg);
  if (cryptoKey.usages.includes("deriveBits")) {
    return new Uint8Array(await webcrypto_default.subtle.deriveBits(subtleAlg, cryptoKey, keylen));
  }
  if (cryptoKey.usages.includes("deriveKey")) {
    return webcrypto_default.subtle.deriveKey(subtleAlg, cryptoKey, wrapAlg, false, ["wrapKey", "unwrapKey"]);
  }
  throw new TypeError('PBKDF2 key "usages" must include "deriveBits" or "deriveKey"');
}
var encrypt, decrypt2;
var init_pbes2kw = __esm({
  "node_modules/jose/dist/browser/runtime/pbes2kw.js"() {
    init_random();
    init_buffer_utils();
    init_base64url();
    init_aeskw();
    init_check_p2s();
    init_webcrypto();
    init_crypto_key();
    init_invalid_key_input();
    init_is_key_like();
    encrypt = async (alg, key, cek, p2c = 2048, p2s2 = random_default(new Uint8Array(16))) => {
      const derived = await deriveKey2(p2s2, alg, p2c, key);
      const encryptedKey = await wrap(alg.slice(-6), derived, cek);
      return { encryptedKey, p2c, p2s: encode(p2s2) };
    };
    decrypt2 = async (alg, key, encryptedKey, p2c, p2s2) => {
      const derived = await deriveKey2(p2s2, alg, p2c, key);
      return unwrap(alg.slice(-6), derived, encryptedKey);
    };
  }
});

// node_modules/jose/dist/browser/runtime/subtle_rsaes.js
function subtleRsaEs(alg) {
  switch (alg) {
    case "RSA-OAEP":
    case "RSA-OAEP-256":
    case "RSA-OAEP-384":
    case "RSA-OAEP-512":
      return "RSA-OAEP";
    default:
      throw new JOSENotSupported(`alg ${alg} is not supported either by JOSE or your javascript runtime`);
  }
}
var init_subtle_rsaes = __esm({
  "node_modules/jose/dist/browser/runtime/subtle_rsaes.js"() {
    init_errors();
  }
});

// node_modules/jose/dist/browser/runtime/check_key_length.js
var check_key_length_default;
var init_check_key_length = __esm({
  "node_modules/jose/dist/browser/runtime/check_key_length.js"() {
    check_key_length_default = (alg, key) => {
      if (alg.startsWith("RS") || alg.startsWith("PS")) {
        const { modulusLength } = key.algorithm;
        if (typeof modulusLength !== "number" || modulusLength < 2048) {
          throw new TypeError(`${alg} requires key modulusLength to be 2048 bits or larger`);
        }
      }
    };
  }
});

// node_modules/jose/dist/browser/runtime/rsaes.js
var encrypt2, decrypt3;
var init_rsaes = __esm({
  "node_modules/jose/dist/browser/runtime/rsaes.js"() {
    init_subtle_rsaes();
    init_bogus();
    init_webcrypto();
    init_crypto_key();
    init_check_key_length();
    init_invalid_key_input();
    init_is_key_like();
    encrypt2 = async (alg, key, cek) => {
      if (!isCryptoKey(key)) {
        throw new TypeError(invalid_key_input_default(key, ...types));
      }
      checkEncCryptoKey(key, alg, "encrypt", "wrapKey");
      check_key_length_default(alg, key);
      if (key.usages.includes("encrypt")) {
        return new Uint8Array(await webcrypto_default.subtle.encrypt(subtleRsaEs(alg), key, cek));
      }
      if (key.usages.includes("wrapKey")) {
        const cryptoKeyCek = await webcrypto_default.subtle.importKey("raw", cek, ...bogus_default);
        return new Uint8Array(await webcrypto_default.subtle.wrapKey("raw", cryptoKeyCek, key, subtleRsaEs(alg)));
      }
      throw new TypeError('RSA-OAEP key "usages" must include "encrypt" or "wrapKey" for this operation');
    };
    decrypt3 = async (alg, key, encryptedKey) => {
      if (!isCryptoKey(key)) {
        throw new TypeError(invalid_key_input_default(key, ...types));
      }
      checkEncCryptoKey(key, alg, "decrypt", "unwrapKey");
      check_key_length_default(alg, key);
      if (key.usages.includes("decrypt")) {
        return new Uint8Array(await webcrypto_default.subtle.decrypt(subtleRsaEs(alg), key, encryptedKey));
      }
      if (key.usages.includes("unwrapKey")) {
        const cryptoKeyCek = await webcrypto_default.subtle.unwrapKey("raw", encryptedKey, key, subtleRsaEs(alg), ...bogus_default);
        return new Uint8Array(await webcrypto_default.subtle.exportKey("raw", cryptoKeyCek));
      }
      throw new TypeError('RSA-OAEP key "usages" must include "decrypt" or "unwrapKey" for this operation');
    };
  }
});

// node_modules/jose/dist/browser/lib/is_jwk.js
function isJWK(key) {
  return isObject(key) && typeof key.kty === "string";
}
function isPrivateJWK(key) {
  return key.kty !== "oct" && typeof key.d === "string";
}
function isPublicJWK(key) {
  return key.kty !== "oct" && typeof key.d === "undefined";
}
function isSecretJWK(key) {
  return isJWK(key) && key.kty === "oct" && typeof key.k === "string";
}
var init_is_jwk = __esm({
  "node_modules/jose/dist/browser/lib/is_jwk.js"() {
    init_is_object();
  }
});

// node_modules/jose/dist/browser/runtime/jwk_to_key.js
function subtleMapping(jwk) {
  let algorithm;
  let keyUsages;
  switch (jwk.kty) {
    case "RSA": {
      switch (jwk.alg) {
        case "PS256":
        case "PS384":
        case "PS512":
          algorithm = { name: "RSA-PSS", hash: `SHA-${jwk.alg.slice(-3)}` };
          keyUsages = jwk.d ? ["sign"] : ["verify"];
          break;
        case "RS256":
        case "RS384":
        case "RS512":
          algorithm = { name: "RSASSA-PKCS1-v1_5", hash: `SHA-${jwk.alg.slice(-3)}` };
          keyUsages = jwk.d ? ["sign"] : ["verify"];
          break;
        case "RSA-OAEP":
        case "RSA-OAEP-256":
        case "RSA-OAEP-384":
        case "RSA-OAEP-512":
          algorithm = {
            name: "RSA-OAEP",
            hash: `SHA-${parseInt(jwk.alg.slice(-3), 10) || 1}`
          };
          keyUsages = jwk.d ? ["decrypt", "unwrapKey"] : ["encrypt", "wrapKey"];
          break;
        default:
          throw new JOSENotSupported('Invalid or unsupported JWK "alg" (Algorithm) Parameter value');
      }
      break;
    }
    case "EC": {
      switch (jwk.alg) {
        case "ES256":
          algorithm = { name: "ECDSA", namedCurve: "P-256" };
          keyUsages = jwk.d ? ["sign"] : ["verify"];
          break;
        case "ES384":
          algorithm = { name: "ECDSA", namedCurve: "P-384" };
          keyUsages = jwk.d ? ["sign"] : ["verify"];
          break;
        case "ES512":
          algorithm = { name: "ECDSA", namedCurve: "P-521" };
          keyUsages = jwk.d ? ["sign"] : ["verify"];
          break;
        case "ECDH-ES":
        case "ECDH-ES+A128KW":
        case "ECDH-ES+A192KW":
        case "ECDH-ES+A256KW":
          algorithm = { name: "ECDH", namedCurve: jwk.crv };
          keyUsages = jwk.d ? ["deriveBits"] : [];
          break;
        default:
          throw new JOSENotSupported('Invalid or unsupported JWK "alg" (Algorithm) Parameter value');
      }
      break;
    }
    case "OKP": {
      switch (jwk.alg) {
        case "Ed25519":
          algorithm = { name: "Ed25519" };
          keyUsages = jwk.d ? ["sign"] : ["verify"];
          break;
        case "EdDSA":
          algorithm = { name: jwk.crv };
          keyUsages = jwk.d ? ["sign"] : ["verify"];
          break;
        case "ECDH-ES":
        case "ECDH-ES+A128KW":
        case "ECDH-ES+A192KW":
        case "ECDH-ES+A256KW":
          algorithm = { name: jwk.crv };
          keyUsages = jwk.d ? ["deriveBits"] : [];
          break;
        default:
          throw new JOSENotSupported('Invalid or unsupported JWK "alg" (Algorithm) Parameter value');
      }
      break;
    }
    default:
      throw new JOSENotSupported('Invalid or unsupported JWK "kty" (Key Type) Parameter value');
  }
  return { algorithm, keyUsages };
}
var parse, jwk_to_key_default;
var init_jwk_to_key = __esm({
  "node_modules/jose/dist/browser/runtime/jwk_to_key.js"() {
    init_webcrypto();
    init_errors();
    parse = async (jwk) => {
      if (!jwk.alg) {
        throw new TypeError('"alg" argument is required when "jwk.alg" is not present');
      }
      const { algorithm, keyUsages } = subtleMapping(jwk);
      const rest = [
        algorithm,
        jwk.ext ?? false,
        jwk.key_ops ?? keyUsages
      ];
      const keyData = { ...jwk };
      delete keyData.alg;
      delete keyData.use;
      return webcrypto_default.subtle.importKey("jwk", keyData, ...rest);
    };
    jwk_to_key_default = parse;
  }
});

// node_modules/jose/dist/browser/runtime/normalize_key.js
var exportKeyValue, privCache, pubCache, isKeyObject, importAndCache, normalizePublicKey, normalizePrivateKey, normalize_key_default;
var init_normalize_key = __esm({
  "node_modules/jose/dist/browser/runtime/normalize_key.js"() {
    init_is_jwk();
    init_base64url();
    init_jwk_to_key();
    exportKeyValue = (k) => decode(k);
    isKeyObject = (key) => {
      return key?.[Symbol.toStringTag] === "KeyObject";
    };
    importAndCache = async (cache, key, jwk, alg, freeze = false) => {
      let cached = cache.get(key);
      if (cached?.[alg]) {
        return cached[alg];
      }
      const cryptoKey = await jwk_to_key_default({ ...jwk, alg });
      if (freeze)
        Object.freeze(key);
      if (!cached) {
        cache.set(key, { [alg]: cryptoKey });
      } else {
        cached[alg] = cryptoKey;
      }
      return cryptoKey;
    };
    normalizePublicKey = (key, alg) => {
      if (isKeyObject(key)) {
        let jwk = key.export({ format: "jwk" });
        delete jwk.d;
        delete jwk.dp;
        delete jwk.dq;
        delete jwk.p;
        delete jwk.q;
        delete jwk.qi;
        if (jwk.k) {
          return exportKeyValue(jwk.k);
        }
        pubCache || (pubCache = /* @__PURE__ */ new WeakMap());
        return importAndCache(pubCache, key, jwk, alg);
      }
      if (isJWK(key)) {
        if (key.k)
          return decode(key.k);
        pubCache || (pubCache = /* @__PURE__ */ new WeakMap());
        const cryptoKey = importAndCache(pubCache, key, key, alg, true);
        return cryptoKey;
      }
      return key;
    };
    normalizePrivateKey = (key, alg) => {
      if (isKeyObject(key)) {
        let jwk = key.export({ format: "jwk" });
        if (jwk.k) {
          return exportKeyValue(jwk.k);
        }
        privCache || (privCache = /* @__PURE__ */ new WeakMap());
        return importAndCache(privCache, key, jwk, alg);
      }
      if (isJWK(key)) {
        if (key.k)
          return decode(key.k);
        privCache || (privCache = /* @__PURE__ */ new WeakMap());
        const cryptoKey = importAndCache(privCache, key, key, alg, true);
        return cryptoKey;
      }
      return key;
    };
    normalize_key_default = { normalizePublicKey, normalizePrivateKey };
  }
});

// node_modules/jose/dist/browser/lib/cek.js
function bitLength2(alg) {
  switch (alg) {
    case "A128GCM":
      return 128;
    case "A192GCM":
      return 192;
    case "A256GCM":
    case "A128CBC-HS256":
      return 256;
    case "A192CBC-HS384":
      return 384;
    case "A256CBC-HS512":
      return 512;
    default:
      throw new JOSENotSupported(`Unsupported JWE Algorithm: ${alg}`);
  }
}
var cek_default;
var init_cek = __esm({
  "node_modules/jose/dist/browser/lib/cek.js"() {
    init_errors();
    init_random();
    cek_default = (alg) => random_default(new Uint8Array(bitLength2(alg) >> 3));
  }
});

// node_modules/jose/dist/browser/lib/format_pem.js
var format_pem_default;
var init_format_pem = __esm({
  "node_modules/jose/dist/browser/lib/format_pem.js"() {
    format_pem_default = (b64, descriptor) => {
      const newlined = (b64.match(/.{1,64}/g) || []).join("\n");
      return `-----BEGIN ${descriptor}-----
${newlined}
-----END ${descriptor}-----`;
    };
  }
});

// node_modules/jose/dist/browser/runtime/asn1.js
function getElement(seq) {
  const result = [];
  let next = 0;
  while (next < seq.length) {
    const nextPart = parseElement(seq.subarray(next));
    result.push(nextPart);
    next += nextPart.byteLength;
  }
  return result;
}
function parseElement(bytes) {
  let position = 0;
  let tag2 = bytes[0] & 31;
  position++;
  if (tag2 === 31) {
    tag2 = 0;
    while (bytes[position] >= 128) {
      tag2 = tag2 * 128 + bytes[position] - 128;
      position++;
    }
    tag2 = tag2 * 128 + bytes[position] - 128;
    position++;
  }
  let length = 0;
  if (bytes[position] < 128) {
    length = bytes[position];
    position++;
  } else if (length === 128) {
    length = 0;
    while (bytes[position + length] !== 0 || bytes[position + length + 1] !== 0) {
      if (length > bytes.byteLength) {
        throw new TypeError("invalid indefinite form length");
      }
      length++;
    }
    const byteLength2 = position + length + 2;
    return {
      byteLength: byteLength2,
      contents: bytes.subarray(position, position + length),
      raw: bytes.subarray(0, byteLength2)
    };
  } else {
    const numberOfDigits = bytes[position] & 127;
    position++;
    length = 0;
    for (let i = 0; i < numberOfDigits; i++) {
      length = length * 256 + bytes[position];
      position++;
    }
  }
  const byteLength = position + length;
  return {
    byteLength,
    contents: bytes.subarray(position, byteLength),
    raw: bytes.subarray(0, byteLength)
  };
}
function spkiFromX509(buf) {
  const tbsCertificate = getElement(getElement(parseElement(buf).contents)[0].contents);
  return encodeBase64(tbsCertificate[tbsCertificate[0].raw[0] === 160 ? 6 : 5].raw);
}
function getSPKI(x509) {
  const pem = x509.replace(/(?:-----(?:BEGIN|END) CERTIFICATE-----|\s)/g, "");
  const raw = decodeBase64(pem);
  return format_pem_default(spkiFromX509(raw), "PUBLIC KEY");
}
var genericExport, toSPKI, toPKCS8, findOid, getNamedCurve2, genericImport, fromPKCS8, fromSPKI, fromX509;
var init_asn1 = __esm({
  "node_modules/jose/dist/browser/runtime/asn1.js"() {
    init_webcrypto();
    init_invalid_key_input();
    init_base64url();
    init_format_pem();
    init_errors();
    init_is_key_like();
    genericExport = async (keyType, keyFormat, key) => {
      if (!isCryptoKey(key)) {
        throw new TypeError(invalid_key_input_default(key, ...types));
      }
      if (!key.extractable) {
        throw new TypeError("CryptoKey is not extractable");
      }
      if (key.type !== keyType) {
        throw new TypeError(`key is not a ${keyType} key`);
      }
      return format_pem_default(encodeBase64(new Uint8Array(await webcrypto_default.subtle.exportKey(keyFormat, key))), `${keyType.toUpperCase()} KEY`);
    };
    toSPKI = (key) => {
      return genericExport("public", "spki", key);
    };
    toPKCS8 = (key) => {
      return genericExport("private", "pkcs8", key);
    };
    findOid = (keyData, oid, from = 0) => {
      if (from === 0) {
        oid.unshift(oid.length);
        oid.unshift(6);
      }
      const i = keyData.indexOf(oid[0], from);
      if (i === -1)
        return false;
      const sub = keyData.subarray(i, i + oid.length);
      if (sub.length !== oid.length)
        return false;
      return sub.every((value, index) => value === oid[index]) || findOid(keyData, oid, i + 1);
    };
    getNamedCurve2 = (keyData) => {
      switch (true) {
        case findOid(keyData, [42, 134, 72, 206, 61, 3, 1, 7]):
          return "P-256";
        case findOid(keyData, [43, 129, 4, 0, 34]):
          return "P-384";
        case findOid(keyData, [43, 129, 4, 0, 35]):
          return "P-521";
        case findOid(keyData, [43, 101, 110]):
          return "X25519";
        case findOid(keyData, [43, 101, 111]):
          return "X448";
        case findOid(keyData, [43, 101, 112]):
          return "Ed25519";
        case findOid(keyData, [43, 101, 113]):
          return "Ed448";
        default:
          throw new JOSENotSupported("Invalid or unsupported EC Key Curve or OKP Key Sub Type");
      }
    };
    genericImport = async (replace, keyFormat, pem, alg, options) => {
      let algorithm;
      let keyUsages;
      const keyData = new Uint8Array(atob(pem.replace(replace, "")).split("").map((c) => c.charCodeAt(0)));
      const isPublic = keyFormat === "spki";
      switch (alg) {
        case "PS256":
        case "PS384":
        case "PS512":
          algorithm = { name: "RSA-PSS", hash: `SHA-${alg.slice(-3)}` };
          keyUsages = isPublic ? ["verify"] : ["sign"];
          break;
        case "RS256":
        case "RS384":
        case "RS512":
          algorithm = { name: "RSASSA-PKCS1-v1_5", hash: `SHA-${alg.slice(-3)}` };
          keyUsages = isPublic ? ["verify"] : ["sign"];
          break;
        case "RSA-OAEP":
        case "RSA-OAEP-256":
        case "RSA-OAEP-384":
        case "RSA-OAEP-512":
          algorithm = {
            name: "RSA-OAEP",
            hash: `SHA-${parseInt(alg.slice(-3), 10) || 1}`
          };
          keyUsages = isPublic ? ["encrypt", "wrapKey"] : ["decrypt", "unwrapKey"];
          break;
        case "ES256":
          algorithm = { name: "ECDSA", namedCurve: "P-256" };
          keyUsages = isPublic ? ["verify"] : ["sign"];
          break;
        case "ES384":
          algorithm = { name: "ECDSA", namedCurve: "P-384" };
          keyUsages = isPublic ? ["verify"] : ["sign"];
          break;
        case "ES512":
          algorithm = { name: "ECDSA", namedCurve: "P-521" };
          keyUsages = isPublic ? ["verify"] : ["sign"];
          break;
        case "ECDH-ES":
        case "ECDH-ES+A128KW":
        case "ECDH-ES+A192KW":
        case "ECDH-ES+A256KW": {
          const namedCurve = getNamedCurve2(keyData);
          algorithm = namedCurve.startsWith("P-") ? { name: "ECDH", namedCurve } : { name: namedCurve };
          keyUsages = isPublic ? [] : ["deriveBits"];
          break;
        }
        case "Ed25519":
          algorithm = { name: "Ed25519" };
          keyUsages = isPublic ? ["verify"] : ["sign"];
          break;
        case "EdDSA":
          algorithm = { name: getNamedCurve2(keyData) };
          keyUsages = isPublic ? ["verify"] : ["sign"];
          break;
        default:
          throw new JOSENotSupported('Invalid or unsupported "alg" (Algorithm) value');
      }
      return webcrypto_default.subtle.importKey(keyFormat, keyData, algorithm, options?.extractable ?? false, keyUsages);
    };
    fromPKCS8 = (pem, alg, options) => {
      return genericImport(/(?:-----(?:BEGIN|END) PRIVATE KEY-----|\s)/g, "pkcs8", pem, alg, options);
    };
    fromSPKI = (pem, alg, options) => {
      return genericImport(/(?:-----(?:BEGIN|END) PUBLIC KEY-----|\s)/g, "spki", pem, alg, options);
    };
    fromX509 = (pem, alg, options) => {
      let spki;
      try {
        spki = getSPKI(pem);
      } catch (cause) {
        throw new TypeError("Failed to parse the X.509 certificate", { cause });
      }
      return fromSPKI(spki, alg, options);
    };
  }
});

// node_modules/jose/dist/browser/key/import.js
async function importSPKI(spki, alg, options) {
  if (typeof spki !== "string" || spki.indexOf("-----BEGIN PUBLIC KEY-----") !== 0) {
    throw new TypeError('"spki" must be SPKI formatted string');
  }
  return fromSPKI(spki, alg, options);
}
async function importX509(x509, alg, options) {
  if (typeof x509 !== "string" || x509.indexOf("-----BEGIN CERTIFICATE-----") !== 0) {
    throw new TypeError('"x509" must be X.509 formatted string');
  }
  return fromX509(x509, alg, options);
}
async function importPKCS8(pkcs8, alg, options) {
  if (typeof pkcs8 !== "string" || pkcs8.indexOf("-----BEGIN PRIVATE KEY-----") !== 0) {
    throw new TypeError('"pkcs8" must be PKCS#8 formatted string');
  }
  return fromPKCS8(pkcs8, alg, options);
}
async function importJWK(jwk, alg) {
  if (!isObject(jwk)) {
    throw new TypeError("JWK must be an object");
  }
  alg || (alg = jwk.alg);
  switch (jwk.kty) {
    case "oct":
      if (typeof jwk.k !== "string" || !jwk.k) {
        throw new TypeError('missing "k" (Key Value) Parameter value');
      }
      return decode(jwk.k);
    case "RSA":
      if ("oth" in jwk && jwk.oth !== void 0) {
        throw new JOSENotSupported('RSA JWK "oth" (Other Primes Info) Parameter value is not supported');
      }
    case "EC":
    case "OKP":
      return jwk_to_key_default({ ...jwk, alg });
    default:
      throw new JOSENotSupported('Unsupported "kty" (Key Type) Parameter value');
  }
}
var init_import = __esm({
  "node_modules/jose/dist/browser/key/import.js"() {
    init_base64url();
    init_asn1();
    init_jwk_to_key();
    init_errors();
    init_is_object();
  }
});

// node_modules/jose/dist/browser/lib/check_key_type.js
function checkKeyType(allowJwk, alg, key, usage) {
  const symmetric = alg.startsWith("HS") || alg === "dir" || alg.startsWith("PBES2") || /^A\d{3}(?:GCM)?KW$/.test(alg);
  if (symmetric) {
    symmetricTypeCheck(alg, key, usage, allowJwk);
  } else {
    asymmetricTypeCheck(alg, key, usage, allowJwk);
  }
}
var tag, jwkMatchesOp, symmetricTypeCheck, asymmetricTypeCheck, check_key_type_default, checkKeyTypeWithJwk;
var init_check_key_type = __esm({
  "node_modules/jose/dist/browser/lib/check_key_type.js"() {
    init_invalid_key_input();
    init_is_key_like();
    init_is_jwk();
    tag = (key) => key?.[Symbol.toStringTag];
    jwkMatchesOp = (alg, key, usage) => {
      if (key.use !== void 0 && key.use !== "sig") {
        throw new TypeError("Invalid key for this operation, when present its use must be sig");
      }
      if (key.key_ops !== void 0 && key.key_ops.includes?.(usage) !== true) {
        throw new TypeError(`Invalid key for this operation, when present its key_ops must include ${usage}`);
      }
      if (key.alg !== void 0 && key.alg !== alg) {
        throw new TypeError(`Invalid key for this operation, when present its alg must be ${alg}`);
      }
      return true;
    };
    symmetricTypeCheck = (alg, key, usage, allowJwk) => {
      if (key instanceof Uint8Array)
        return;
      if (allowJwk && isJWK(key)) {
        if (isSecretJWK(key) && jwkMatchesOp(alg, key, usage))
          return;
        throw new TypeError(`JSON Web Key for symmetric algorithms must have JWK "kty" (Key Type) equal to "oct" and the JWK "k" (Key Value) present`);
      }
      if (!is_key_like_default(key)) {
        throw new TypeError(withAlg(alg, key, ...types, "Uint8Array", allowJwk ? "JSON Web Key" : null));
      }
      if (key.type !== "secret") {
        throw new TypeError(`${tag(key)} instances for symmetric algorithms must be of type "secret"`);
      }
    };
    asymmetricTypeCheck = (alg, key, usage, allowJwk) => {
      if (allowJwk && isJWK(key)) {
        switch (usage) {
          case "sign":
            if (isPrivateJWK(key) && jwkMatchesOp(alg, key, usage))
              return;
            throw new TypeError(`JSON Web Key for this operation be a private JWK`);
          case "verify":
            if (isPublicJWK(key) && jwkMatchesOp(alg, key, usage))
              return;
            throw new TypeError(`JSON Web Key for this operation be a public JWK`);
        }
      }
      if (!is_key_like_default(key)) {
        throw new TypeError(withAlg(alg, key, ...types, allowJwk ? "JSON Web Key" : null));
      }
      if (key.type === "secret") {
        throw new TypeError(`${tag(key)} instances for asymmetric algorithms must not be of type "secret"`);
      }
      if (usage === "sign" && key.type === "public") {
        throw new TypeError(`${tag(key)} instances for asymmetric algorithm signing must be of type "private"`);
      }
      if (usage === "decrypt" && key.type === "public") {
        throw new TypeError(`${tag(key)} instances for asymmetric algorithm decryption must be of type "private"`);
      }
      if (key.algorithm && usage === "verify" && key.type === "private") {
        throw new TypeError(`${tag(key)} instances for asymmetric algorithm verifying must be of type "public"`);
      }
      if (key.algorithm && usage === "encrypt" && key.type === "private") {
        throw new TypeError(`${tag(key)} instances for asymmetric algorithm encryption must be of type "public"`);
      }
    };
    check_key_type_default = checkKeyType.bind(void 0, false);
    checkKeyTypeWithJwk = checkKeyType.bind(void 0, true);
  }
});

// node_modules/jose/dist/browser/runtime/encrypt.js
async function cbcEncrypt(enc, plaintext, cek, iv, aad) {
  if (!(cek instanceof Uint8Array)) {
    throw new TypeError(invalid_key_input_default(cek, "Uint8Array"));
  }
  const keySize = parseInt(enc.slice(1, 4), 10);
  const encKey = await webcrypto_default.subtle.importKey("raw", cek.subarray(keySize >> 3), "AES-CBC", false, ["encrypt"]);
  const macKey = await webcrypto_default.subtle.importKey("raw", cek.subarray(0, keySize >> 3), {
    hash: `SHA-${keySize << 1}`,
    name: "HMAC"
  }, false, ["sign"]);
  const ciphertext = new Uint8Array(await webcrypto_default.subtle.encrypt({
    iv,
    name: "AES-CBC"
  }, encKey, plaintext));
  const macData = concat(aad, iv, ciphertext, uint64be(aad.length << 3));
  const tag2 = new Uint8Array((await webcrypto_default.subtle.sign("HMAC", macKey, macData)).slice(0, keySize >> 3));
  return { ciphertext, tag: tag2, iv };
}
async function gcmEncrypt(enc, plaintext, cek, iv, aad) {
  let encKey;
  if (cek instanceof Uint8Array) {
    encKey = await webcrypto_default.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  } else {
    checkEncCryptoKey(cek, enc, "encrypt");
    encKey = cek;
  }
  const encrypted = new Uint8Array(await webcrypto_default.subtle.encrypt({
    additionalData: aad,
    iv,
    name: "AES-GCM",
    tagLength: 128
  }, encKey, plaintext));
  const tag2 = encrypted.slice(-16);
  const ciphertext = encrypted.slice(0, -16);
  return { ciphertext, tag: tag2, iv };
}
var encrypt3, encrypt_default;
var init_encrypt = __esm({
  "node_modules/jose/dist/browser/runtime/encrypt.js"() {
    init_buffer_utils();
    init_check_iv_length();
    init_check_cek_length();
    init_webcrypto();
    init_crypto_key();
    init_invalid_key_input();
    init_iv();
    init_errors();
    init_is_key_like();
    encrypt3 = async (enc, plaintext, cek, iv, aad) => {
      if (!isCryptoKey(cek) && !(cek instanceof Uint8Array)) {
        throw new TypeError(invalid_key_input_default(cek, ...types, "Uint8Array"));
      }
      if (iv) {
        check_iv_length_default(enc, iv);
      } else {
        iv = iv_default(enc);
      }
      switch (enc) {
        case "A128CBC-HS256":
        case "A192CBC-HS384":
        case "A256CBC-HS512":
          if (cek instanceof Uint8Array) {
            check_cek_length_default(cek, parseInt(enc.slice(-3), 10));
          }
          return cbcEncrypt(enc, plaintext, cek, iv, aad);
        case "A128GCM":
        case "A192GCM":
        case "A256GCM":
          if (cek instanceof Uint8Array) {
            check_cek_length_default(cek, parseInt(enc.slice(1, 4), 10));
          }
          return gcmEncrypt(enc, plaintext, cek, iv, aad);
        default:
          throw new JOSENotSupported("Unsupported JWE Content Encryption Algorithm");
      }
    };
    encrypt_default = encrypt3;
  }
});

// node_modules/jose/dist/browser/lib/aesgcmkw.js
async function wrap2(alg, key, cek, iv) {
  const jweAlgorithm = alg.slice(0, 7);
  const wrapped = await encrypt_default(jweAlgorithm, cek, key, iv, new Uint8Array(0));
  return {
    encryptedKey: wrapped.ciphertext,
    iv: encode(wrapped.iv),
    tag: encode(wrapped.tag)
  };
}
async function unwrap2(alg, key, encryptedKey, iv, tag2) {
  const jweAlgorithm = alg.slice(0, 7);
  return decrypt_default(jweAlgorithm, key, encryptedKey, iv, tag2, new Uint8Array(0));
}
var init_aesgcmkw = __esm({
  "node_modules/jose/dist/browser/lib/aesgcmkw.js"() {
    init_encrypt();
    init_decrypt();
    init_base64url();
  }
});

// node_modules/jose/dist/browser/lib/decrypt_key_management.js
async function decryptKeyManagement(alg, key, encryptedKey, joseHeader, options) {
  check_key_type_default(alg, key, "decrypt");
  key = await normalize_key_default.normalizePrivateKey?.(key, alg) || key;
  switch (alg) {
    case "dir": {
      if (encryptedKey !== void 0)
        throw new JWEInvalid("Encountered unexpected JWE Encrypted Key");
      return key;
    }
    case "ECDH-ES":
      if (encryptedKey !== void 0)
        throw new JWEInvalid("Encountered unexpected JWE Encrypted Key");
    case "ECDH-ES+A128KW":
    case "ECDH-ES+A192KW":
    case "ECDH-ES+A256KW": {
      if (!isObject(joseHeader.epk))
        throw new JWEInvalid(`JOSE Header "epk" (Ephemeral Public Key) missing or invalid`);
      if (!ecdhAllowed(key))
        throw new JOSENotSupported("ECDH with the provided key is not allowed or not supported by your javascript runtime");
      const epk = await importJWK(joseHeader.epk, alg);
      let partyUInfo;
      let partyVInfo;
      if (joseHeader.apu !== void 0) {
        if (typeof joseHeader.apu !== "string")
          throw new JWEInvalid(`JOSE Header "apu" (Agreement PartyUInfo) invalid`);
        try {
          partyUInfo = decode(joseHeader.apu);
        } catch {
          throw new JWEInvalid("Failed to base64url decode the apu");
        }
      }
      if (joseHeader.apv !== void 0) {
        if (typeof joseHeader.apv !== "string")
          throw new JWEInvalid(`JOSE Header "apv" (Agreement PartyVInfo) invalid`);
        try {
          partyVInfo = decode(joseHeader.apv);
        } catch {
          throw new JWEInvalid("Failed to base64url decode the apv");
        }
      }
      const sharedSecret = await deriveKey(epk, key, alg === "ECDH-ES" ? joseHeader.enc : alg, alg === "ECDH-ES" ? bitLength2(joseHeader.enc) : parseInt(alg.slice(-5, -2), 10), partyUInfo, partyVInfo);
      if (alg === "ECDH-ES")
        return sharedSecret;
      if (encryptedKey === void 0)
        throw new JWEInvalid("JWE Encrypted Key missing");
      return unwrap(alg.slice(-6), sharedSecret, encryptedKey);
    }
    case "RSA1_5":
    case "RSA-OAEP":
    case "RSA-OAEP-256":
    case "RSA-OAEP-384":
    case "RSA-OAEP-512": {
      if (encryptedKey === void 0)
        throw new JWEInvalid("JWE Encrypted Key missing");
      return decrypt3(alg, key, encryptedKey);
    }
    case "PBES2-HS256+A128KW":
    case "PBES2-HS384+A192KW":
    case "PBES2-HS512+A256KW": {
      if (encryptedKey === void 0)
        throw new JWEInvalid("JWE Encrypted Key missing");
      if (typeof joseHeader.p2c !== "number")
        throw new JWEInvalid(`JOSE Header "p2c" (PBES2 Count) missing or invalid`);
      const p2cLimit = options?.maxPBES2Count || 1e4;
      if (joseHeader.p2c > p2cLimit)
        throw new JWEInvalid(`JOSE Header "p2c" (PBES2 Count) out is of acceptable bounds`);
      if (typeof joseHeader.p2s !== "string")
        throw new JWEInvalid(`JOSE Header "p2s" (PBES2 Salt) missing or invalid`);
      let p2s2;
      try {
        p2s2 = decode(joseHeader.p2s);
      } catch {
        throw new JWEInvalid("Failed to base64url decode the p2s");
      }
      return decrypt2(alg, key, encryptedKey, joseHeader.p2c, p2s2);
    }
    case "A128KW":
    case "A192KW":
    case "A256KW": {
      if (encryptedKey === void 0)
        throw new JWEInvalid("JWE Encrypted Key missing");
      return unwrap(alg, key, encryptedKey);
    }
    case "A128GCMKW":
    case "A192GCMKW":
    case "A256GCMKW": {
      if (encryptedKey === void 0)
        throw new JWEInvalid("JWE Encrypted Key missing");
      if (typeof joseHeader.iv !== "string")
        throw new JWEInvalid(`JOSE Header "iv" (Initialization Vector) missing or invalid`);
      if (typeof joseHeader.tag !== "string")
        throw new JWEInvalid(`JOSE Header "tag" (Authentication Tag) missing or invalid`);
      let iv;
      try {
        iv = decode(joseHeader.iv);
      } catch {
        throw new JWEInvalid("Failed to base64url decode the iv");
      }
      let tag2;
      try {
        tag2 = decode(joseHeader.tag);
      } catch {
        throw new JWEInvalid("Failed to base64url decode the tag");
      }
      return unwrap2(alg, key, encryptedKey, iv, tag2);
    }
    default: {
      throw new JOSENotSupported('Invalid or unsupported "alg" (JWE Algorithm) header value');
    }
  }
}
var decrypt_key_management_default;
var init_decrypt_key_management = __esm({
  "node_modules/jose/dist/browser/lib/decrypt_key_management.js"() {
    init_aeskw();
    init_ecdhes();
    init_pbes2kw();
    init_rsaes();
    init_base64url();
    init_normalize_key();
    init_errors();
    init_cek();
    init_import();
    init_check_key_type();
    init_is_object();
    init_aesgcmkw();
    decrypt_key_management_default = decryptKeyManagement;
  }
});

// node_modules/jose/dist/browser/lib/validate_crit.js
function validateCrit(Err, recognizedDefault, recognizedOption, protectedHeader, joseHeader) {
  if (joseHeader.crit !== void 0 && protectedHeader?.crit === void 0) {
    throw new Err('"crit" (Critical) Header Parameter MUST be integrity protected');
  }
  if (!protectedHeader || protectedHeader.crit === void 0) {
    return /* @__PURE__ */ new Set();
  }
  if (!Array.isArray(protectedHeader.crit) || protectedHeader.crit.length === 0 || protectedHeader.crit.some((input) => typeof input !== "string" || input.length === 0)) {
    throw new Err('"crit" (Critical) Header Parameter MUST be an array of non-empty strings when present');
  }
  let recognized;
  if (recognizedOption !== void 0) {
    recognized = new Map([...Object.entries(recognizedOption), ...recognizedDefault.entries()]);
  } else {
    recognized = recognizedDefault;
  }
  for (const parameter of protectedHeader.crit) {
    if (!recognized.has(parameter)) {
      throw new JOSENotSupported(`Extension Header Parameter "${parameter}" is not recognized`);
    }
    if (joseHeader[parameter] === void 0) {
      throw new Err(`Extension Header Parameter "${parameter}" is missing`);
    }
    if (recognized.get(parameter) && protectedHeader[parameter] === void 0) {
      throw new Err(`Extension Header Parameter "${parameter}" MUST be integrity protected`);
    }
  }
  return new Set(protectedHeader.crit);
}
var validate_crit_default;
var init_validate_crit = __esm({
  "node_modules/jose/dist/browser/lib/validate_crit.js"() {
    init_errors();
    validate_crit_default = validateCrit;
  }
});

// node_modules/jose/dist/browser/lib/validate_algorithms.js
var validateAlgorithms, validate_algorithms_default;
var init_validate_algorithms = __esm({
  "node_modules/jose/dist/browser/lib/validate_algorithms.js"() {
    validateAlgorithms = (option, algorithms) => {
      if (algorithms !== void 0 && (!Array.isArray(algorithms) || algorithms.some((s) => typeof s !== "string"))) {
        throw new TypeError(`"${option}" option must be an array of strings`);
      }
      if (!algorithms) {
        return void 0;
      }
      return new Set(algorithms);
    };
    validate_algorithms_default = validateAlgorithms;
  }
});

// node_modules/jose/dist/browser/jwe/flattened/decrypt.js
async function flattenedDecrypt(jwe, key, options) {
  if (!isObject(jwe)) {
    throw new JWEInvalid("Flattened JWE must be an object");
  }
  if (jwe.protected === void 0 && jwe.header === void 0 && jwe.unprotected === void 0) {
    throw new JWEInvalid("JOSE Header missing");
  }
  if (jwe.iv !== void 0 && typeof jwe.iv !== "string") {
    throw new JWEInvalid("JWE Initialization Vector incorrect type");
  }
  if (typeof jwe.ciphertext !== "string") {
    throw new JWEInvalid("JWE Ciphertext missing or incorrect type");
  }
  if (jwe.tag !== void 0 && typeof jwe.tag !== "string") {
    throw new JWEInvalid("JWE Authentication Tag incorrect type");
  }
  if (jwe.protected !== void 0 && typeof jwe.protected !== "string") {
    throw new JWEInvalid("JWE Protected Header incorrect type");
  }
  if (jwe.encrypted_key !== void 0 && typeof jwe.encrypted_key !== "string") {
    throw new JWEInvalid("JWE Encrypted Key incorrect type");
  }
  if (jwe.aad !== void 0 && typeof jwe.aad !== "string") {
    throw new JWEInvalid("JWE AAD incorrect type");
  }
  if (jwe.header !== void 0 && !isObject(jwe.header)) {
    throw new JWEInvalid("JWE Shared Unprotected Header incorrect type");
  }
  if (jwe.unprotected !== void 0 && !isObject(jwe.unprotected)) {
    throw new JWEInvalid("JWE Per-Recipient Unprotected Header incorrect type");
  }
  let parsedProt;
  if (jwe.protected) {
    try {
      const protectedHeader2 = decode(jwe.protected);
      parsedProt = JSON.parse(decoder.decode(protectedHeader2));
    } catch {
      throw new JWEInvalid("JWE Protected Header is invalid");
    }
  }
  if (!is_disjoint_default(parsedProt, jwe.header, jwe.unprotected)) {
    throw new JWEInvalid("JWE Protected, JWE Unprotected Header, and JWE Per-Recipient Unprotected Header Parameter names must be disjoint");
  }
  const joseHeader = {
    ...parsedProt,
    ...jwe.header,
    ...jwe.unprotected
  };
  validate_crit_default(JWEInvalid, /* @__PURE__ */ new Map(), options?.crit, parsedProt, joseHeader);
  if (joseHeader.zip !== void 0) {
    throw new JOSENotSupported('JWE "zip" (Compression Algorithm) Header Parameter is not supported.');
  }
  const { alg, enc } = joseHeader;
  if (typeof alg !== "string" || !alg) {
    throw new JWEInvalid("missing JWE Algorithm (alg) in JWE Header");
  }
  if (typeof enc !== "string" || !enc) {
    throw new JWEInvalid("missing JWE Encryption Algorithm (enc) in JWE Header");
  }
  const keyManagementAlgorithms = options && validate_algorithms_default("keyManagementAlgorithms", options.keyManagementAlgorithms);
  const contentEncryptionAlgorithms = options && validate_algorithms_default("contentEncryptionAlgorithms", options.contentEncryptionAlgorithms);
  if (keyManagementAlgorithms && !keyManagementAlgorithms.has(alg) || !keyManagementAlgorithms && alg.startsWith("PBES2")) {
    throw new JOSEAlgNotAllowed('"alg" (Algorithm) Header Parameter value not allowed');
  }
  if (contentEncryptionAlgorithms && !contentEncryptionAlgorithms.has(enc)) {
    throw new JOSEAlgNotAllowed('"enc" (Encryption Algorithm) Header Parameter value not allowed');
  }
  let encryptedKey;
  if (jwe.encrypted_key !== void 0) {
    try {
      encryptedKey = decode(jwe.encrypted_key);
    } catch {
      throw new JWEInvalid("Failed to base64url decode the encrypted_key");
    }
  }
  let resolvedKey = false;
  if (typeof key === "function") {
    key = await key(parsedProt, jwe);
    resolvedKey = true;
  }
  let cek;
  try {
    cek = await decrypt_key_management_default(alg, key, encryptedKey, joseHeader, options);
  } catch (err) {
    if (err instanceof TypeError || err instanceof JWEInvalid || err instanceof JOSENotSupported) {
      throw err;
    }
    cek = cek_default(enc);
  }
  let iv;
  let tag2;
  if (jwe.iv !== void 0) {
    try {
      iv = decode(jwe.iv);
    } catch {
      throw new JWEInvalid("Failed to base64url decode the iv");
    }
  }
  if (jwe.tag !== void 0) {
    try {
      tag2 = decode(jwe.tag);
    } catch {
      throw new JWEInvalid("Failed to base64url decode the tag");
    }
  }
  const protectedHeader = encoder.encode(jwe.protected ?? "");
  let additionalData;
  if (jwe.aad !== void 0) {
    additionalData = concat(protectedHeader, encoder.encode("."), encoder.encode(jwe.aad));
  } else {
    additionalData = protectedHeader;
  }
  let ciphertext;
  try {
    ciphertext = decode(jwe.ciphertext);
  } catch {
    throw new JWEInvalid("Failed to base64url decode the ciphertext");
  }
  const plaintext = await decrypt_default(enc, cek, ciphertext, iv, tag2, additionalData);
  const result = { plaintext };
  if (jwe.protected !== void 0) {
    result.protectedHeader = parsedProt;
  }
  if (jwe.aad !== void 0) {
    try {
      result.additionalAuthenticatedData = decode(jwe.aad);
    } catch {
      throw new JWEInvalid("Failed to base64url decode the aad");
    }
  }
  if (jwe.unprotected !== void 0) {
    result.sharedUnprotectedHeader = jwe.unprotected;
  }
  if (jwe.header !== void 0) {
    result.unprotectedHeader = jwe.header;
  }
  if (resolvedKey) {
    return { ...result, key };
  }
  return result;
}
var init_decrypt2 = __esm({
  "node_modules/jose/dist/browser/jwe/flattened/decrypt.js"() {
    init_base64url();
    init_decrypt();
    init_errors();
    init_is_disjoint();
    init_is_object();
    init_decrypt_key_management();
    init_buffer_utils();
    init_cek();
    init_validate_crit();
    init_validate_algorithms();
  }
});

// node_modules/jose/dist/browser/jwe/compact/decrypt.js
async function compactDecrypt(jwe, key, options) {
  if (jwe instanceof Uint8Array) {
    jwe = decoder.decode(jwe);
  }
  if (typeof jwe !== "string") {
    throw new JWEInvalid("Compact JWE must be a string or Uint8Array");
  }
  const { 0: protectedHeader, 1: encryptedKey, 2: iv, 3: ciphertext, 4: tag2, length } = jwe.split(".");
  if (length !== 5) {
    throw new JWEInvalid("Invalid Compact JWE");
  }
  const decrypted = await flattenedDecrypt({
    ciphertext,
    iv: iv || void 0,
    protected: protectedHeader,
    tag: tag2 || void 0,
    encrypted_key: encryptedKey || void 0
  }, key, options);
  const result = { plaintext: decrypted.plaintext, protectedHeader: decrypted.protectedHeader };
  if (typeof key === "function") {
    return { ...result, key: decrypted.key };
  }
  return result;
}
var init_decrypt3 = __esm({
  "node_modules/jose/dist/browser/jwe/compact/decrypt.js"() {
    init_decrypt2();
    init_errors();
    init_buffer_utils();
  }
});

// node_modules/jose/dist/browser/jwe/general/decrypt.js
async function generalDecrypt(jwe, key, options) {
  if (!isObject(jwe)) {
    throw new JWEInvalid("General JWE must be an object");
  }
  if (!Array.isArray(jwe.recipients) || !jwe.recipients.every(isObject)) {
    throw new JWEInvalid("JWE Recipients missing or incorrect type");
  }
  if (!jwe.recipients.length) {
    throw new JWEInvalid("JWE Recipients has no members");
  }
  for (const recipient of jwe.recipients) {
    try {
      return await flattenedDecrypt({
        aad: jwe.aad,
        ciphertext: jwe.ciphertext,
        encrypted_key: recipient.encrypted_key,
        header: recipient.header,
        iv: jwe.iv,
        protected: jwe.protected,
        tag: jwe.tag,
        unprotected: jwe.unprotected
      }, key, options);
    } catch {
    }
  }
  throw new JWEDecryptionFailed();
}
var init_decrypt4 = __esm({
  "node_modules/jose/dist/browser/jwe/general/decrypt.js"() {
    init_decrypt2();
    init_errors();
    init_is_object();
  }
});

// node_modules/jose/dist/browser/lib/private_symbols.js
var unprotected;
var init_private_symbols = __esm({
  "node_modules/jose/dist/browser/lib/private_symbols.js"() {
    unprotected = /* @__PURE__ */ Symbol();
  }
});

// node_modules/jose/dist/browser/runtime/key_to_jwk.js
var keyToJWK, key_to_jwk_default;
var init_key_to_jwk = __esm({
  "node_modules/jose/dist/browser/runtime/key_to_jwk.js"() {
    init_webcrypto();
    init_invalid_key_input();
    init_base64url();
    init_is_key_like();
    keyToJWK = async (key) => {
      if (key instanceof Uint8Array) {
        return {
          kty: "oct",
          k: encode(key)
        };
      }
      if (!isCryptoKey(key)) {
        throw new TypeError(invalid_key_input_default(key, ...types, "Uint8Array"));
      }
      if (!key.extractable) {
        throw new TypeError("non-extractable CryptoKey cannot be exported as a JWK");
      }
      const { ext, key_ops, alg, use, ...jwk } = await webcrypto_default.subtle.exportKey("jwk", key);
      return jwk;
    };
    key_to_jwk_default = keyToJWK;
  }
});

// node_modules/jose/dist/browser/key/export.js
async function exportSPKI(key) {
  return toSPKI(key);
}
async function exportPKCS8(key) {
  return toPKCS8(key);
}
async function exportJWK(key) {
  return key_to_jwk_default(key);
}
var init_export = __esm({
  "node_modules/jose/dist/browser/key/export.js"() {
    init_asn1();
    init_asn1();
    init_key_to_jwk();
  }
});

// node_modules/jose/dist/browser/lib/encrypt_key_management.js
async function encryptKeyManagement(alg, enc, key, providedCek, providedParameters = {}) {
  let encryptedKey;
  let parameters;
  let cek;
  check_key_type_default(alg, key, "encrypt");
  key = await normalize_key_default.normalizePublicKey?.(key, alg) || key;
  switch (alg) {
    case "dir": {
      cek = key;
      break;
    }
    case "ECDH-ES":
    case "ECDH-ES+A128KW":
    case "ECDH-ES+A192KW":
    case "ECDH-ES+A256KW": {
      if (!ecdhAllowed(key)) {
        throw new JOSENotSupported("ECDH with the provided key is not allowed or not supported by your javascript runtime");
      }
      const { apu, apv } = providedParameters;
      let { epk: ephemeralKey } = providedParameters;
      ephemeralKey || (ephemeralKey = (await generateEpk(key)).privateKey);
      const { x, y, crv, kty } = await exportJWK(ephemeralKey);
      const sharedSecret = await deriveKey(key, ephemeralKey, alg === "ECDH-ES" ? enc : alg, alg === "ECDH-ES" ? bitLength2(enc) : parseInt(alg.slice(-5, -2), 10), apu, apv);
      parameters = { epk: { x, crv, kty } };
      if (kty === "EC")
        parameters.epk.y = y;
      if (apu)
        parameters.apu = encode(apu);
      if (apv)
        parameters.apv = encode(apv);
      if (alg === "ECDH-ES") {
        cek = sharedSecret;
        break;
      }
      cek = providedCek || cek_default(enc);
      const kwAlg = alg.slice(-6);
      encryptedKey = await wrap(kwAlg, sharedSecret, cek);
      break;
    }
    case "RSA1_5":
    case "RSA-OAEP":
    case "RSA-OAEP-256":
    case "RSA-OAEP-384":
    case "RSA-OAEP-512": {
      cek = providedCek || cek_default(enc);
      encryptedKey = await encrypt2(alg, key, cek);
      break;
    }
    case "PBES2-HS256+A128KW":
    case "PBES2-HS384+A192KW":
    case "PBES2-HS512+A256KW": {
      cek = providedCek || cek_default(enc);
      const { p2c, p2s: p2s2 } = providedParameters;
      ({ encryptedKey, ...parameters } = await encrypt(alg, key, cek, p2c, p2s2));
      break;
    }
    case "A128KW":
    case "A192KW":
    case "A256KW": {
      cek = providedCek || cek_default(enc);
      encryptedKey = await wrap(alg, key, cek);
      break;
    }
    case "A128GCMKW":
    case "A192GCMKW":
    case "A256GCMKW": {
      cek = providedCek || cek_default(enc);
      const { iv } = providedParameters;
      ({ encryptedKey, ...parameters } = await wrap2(alg, key, cek, iv));
      break;
    }
    default: {
      throw new JOSENotSupported('Invalid or unsupported "alg" (JWE Algorithm) header value');
    }
  }
  return { cek, encryptedKey, parameters };
}
var encrypt_key_management_default;
var init_encrypt_key_management = __esm({
  "node_modules/jose/dist/browser/lib/encrypt_key_management.js"() {
    init_aeskw();
    init_ecdhes();
    init_pbes2kw();
    init_rsaes();
    init_base64url();
    init_normalize_key();
    init_cek();
    init_errors();
    init_export();
    init_check_key_type();
    init_aesgcmkw();
    encrypt_key_management_default = encryptKeyManagement;
  }
});

// node_modules/jose/dist/browser/jwe/flattened/encrypt.js
var FlattenedEncrypt;
var init_encrypt2 = __esm({
  "node_modules/jose/dist/browser/jwe/flattened/encrypt.js"() {
    init_base64url();
    init_private_symbols();
    init_encrypt();
    init_encrypt_key_management();
    init_errors();
    init_is_disjoint();
    init_buffer_utils();
    init_validate_crit();
    FlattenedEncrypt = class {
      constructor(plaintext) {
        if (!(plaintext instanceof Uint8Array)) {
          throw new TypeError("plaintext must be an instance of Uint8Array");
        }
        this._plaintext = plaintext;
      }
      setKeyManagementParameters(parameters) {
        if (this._keyManagementParameters) {
          throw new TypeError("setKeyManagementParameters can only be called once");
        }
        this._keyManagementParameters = parameters;
        return this;
      }
      setProtectedHeader(protectedHeader) {
        if (this._protectedHeader) {
          throw new TypeError("setProtectedHeader can only be called once");
        }
        this._protectedHeader = protectedHeader;
        return this;
      }
      setSharedUnprotectedHeader(sharedUnprotectedHeader) {
        if (this._sharedUnprotectedHeader) {
          throw new TypeError("setSharedUnprotectedHeader can only be called once");
        }
        this._sharedUnprotectedHeader = sharedUnprotectedHeader;
        return this;
      }
      setUnprotectedHeader(unprotectedHeader) {
        if (this._unprotectedHeader) {
          throw new TypeError("setUnprotectedHeader can only be called once");
        }
        this._unprotectedHeader = unprotectedHeader;
        return this;
      }
      setAdditionalAuthenticatedData(aad) {
        this._aad = aad;
        return this;
      }
      setContentEncryptionKey(cek) {
        if (this._cek) {
          throw new TypeError("setContentEncryptionKey can only be called once");
        }
        this._cek = cek;
        return this;
      }
      setInitializationVector(iv) {
        if (this._iv) {
          throw new TypeError("setInitializationVector can only be called once");
        }
        this._iv = iv;
        return this;
      }
      async encrypt(key, options) {
        if (!this._protectedHeader && !this._unprotectedHeader && !this._sharedUnprotectedHeader) {
          throw new JWEInvalid("either setProtectedHeader, setUnprotectedHeader, or sharedUnprotectedHeader must be called before #encrypt()");
        }
        if (!is_disjoint_default(this._protectedHeader, this._unprotectedHeader, this._sharedUnprotectedHeader)) {
          throw new JWEInvalid("JWE Protected, JWE Shared Unprotected and JWE Per-Recipient Header Parameter names must be disjoint");
        }
        const joseHeader = {
          ...this._protectedHeader,
          ...this._unprotectedHeader,
          ...this._sharedUnprotectedHeader
        };
        validate_crit_default(JWEInvalid, /* @__PURE__ */ new Map(), options?.crit, this._protectedHeader, joseHeader);
        if (joseHeader.zip !== void 0) {
          throw new JOSENotSupported('JWE "zip" (Compression Algorithm) Header Parameter is not supported.');
        }
        const { alg, enc } = joseHeader;
        if (typeof alg !== "string" || !alg) {
          throw new JWEInvalid('JWE "alg" (Algorithm) Header Parameter missing or invalid');
        }
        if (typeof enc !== "string" || !enc) {
          throw new JWEInvalid('JWE "enc" (Encryption Algorithm) Header Parameter missing or invalid');
        }
        let encryptedKey;
        if (this._cek && (alg === "dir" || alg === "ECDH-ES")) {
          throw new TypeError(`setContentEncryptionKey cannot be called with JWE "alg" (Algorithm) Header ${alg}`);
        }
        let cek;
        {
          let parameters;
          ({ cek, encryptedKey, parameters } = await encrypt_key_management_default(alg, enc, key, this._cek, this._keyManagementParameters));
          if (parameters) {
            if (options && unprotected in options) {
              if (!this._unprotectedHeader) {
                this.setUnprotectedHeader(parameters);
              } else {
                this._unprotectedHeader = { ...this._unprotectedHeader, ...parameters };
              }
            } else if (!this._protectedHeader) {
              this.setProtectedHeader(parameters);
            } else {
              this._protectedHeader = { ...this._protectedHeader, ...parameters };
            }
          }
        }
        let additionalData;
        let protectedHeader;
        let aadMember;
        if (this._protectedHeader) {
          protectedHeader = encoder.encode(encode(JSON.stringify(this._protectedHeader)));
        } else {
          protectedHeader = encoder.encode("");
        }
        if (this._aad) {
          aadMember = encode(this._aad);
          additionalData = concat(protectedHeader, encoder.encode("."), encoder.encode(aadMember));
        } else {
          additionalData = protectedHeader;
        }
        const { ciphertext, tag: tag2, iv } = await encrypt_default(enc, this._plaintext, cek, this._iv, additionalData);
        const jwe = {
          ciphertext: encode(ciphertext)
        };
        if (iv) {
          jwe.iv = encode(iv);
        }
        if (tag2) {
          jwe.tag = encode(tag2);
        }
        if (encryptedKey) {
          jwe.encrypted_key = encode(encryptedKey);
        }
        if (aadMember) {
          jwe.aad = aadMember;
        }
        if (this._protectedHeader) {
          jwe.protected = decoder.decode(protectedHeader);
        }
        if (this._sharedUnprotectedHeader) {
          jwe.unprotected = this._sharedUnprotectedHeader;
        }
        if (this._unprotectedHeader) {
          jwe.header = this._unprotectedHeader;
        }
        return jwe;
      }
    };
  }
});

// node_modules/jose/dist/browser/jwe/general/encrypt.js
var IndividualRecipient, GeneralEncrypt;
var init_encrypt3 = __esm({
  "node_modules/jose/dist/browser/jwe/general/encrypt.js"() {
    init_encrypt2();
    init_private_symbols();
    init_errors();
    init_cek();
    init_is_disjoint();
    init_encrypt_key_management();
    init_base64url();
    init_validate_crit();
    IndividualRecipient = class {
      constructor(enc, key, options) {
        this.parent = enc;
        this.key = key;
        this.options = options;
      }
      setUnprotectedHeader(unprotectedHeader) {
        if (this.unprotectedHeader) {
          throw new TypeError("setUnprotectedHeader can only be called once");
        }
        this.unprotectedHeader = unprotectedHeader;
        return this;
      }
      addRecipient(...args) {
        return this.parent.addRecipient(...args);
      }
      encrypt(...args) {
        return this.parent.encrypt(...args);
      }
      done() {
        return this.parent;
      }
    };
    GeneralEncrypt = class {
      constructor(plaintext) {
        this._recipients = [];
        this._plaintext = plaintext;
      }
      addRecipient(key, options) {
        const recipient = new IndividualRecipient(this, key, { crit: options?.crit });
        this._recipients.push(recipient);
        return recipient;
      }
      setProtectedHeader(protectedHeader) {
        if (this._protectedHeader) {
          throw new TypeError("setProtectedHeader can only be called once");
        }
        this._protectedHeader = protectedHeader;
        return this;
      }
      setSharedUnprotectedHeader(sharedUnprotectedHeader) {
        if (this._unprotectedHeader) {
          throw new TypeError("setSharedUnprotectedHeader can only be called once");
        }
        this._unprotectedHeader = sharedUnprotectedHeader;
        return this;
      }
      setAdditionalAuthenticatedData(aad) {
        this._aad = aad;
        return this;
      }
      async encrypt() {
        if (!this._recipients.length) {
          throw new JWEInvalid("at least one recipient must be added");
        }
        if (this._recipients.length === 1) {
          const [recipient] = this._recipients;
          const flattened = await new FlattenedEncrypt(this._plaintext).setAdditionalAuthenticatedData(this._aad).setProtectedHeader(this._protectedHeader).setSharedUnprotectedHeader(this._unprotectedHeader).setUnprotectedHeader(recipient.unprotectedHeader).encrypt(recipient.key, { ...recipient.options });
          const jwe2 = {
            ciphertext: flattened.ciphertext,
            iv: flattened.iv,
            recipients: [{}],
            tag: flattened.tag
          };
          if (flattened.aad)
            jwe2.aad = flattened.aad;
          if (flattened.protected)
            jwe2.protected = flattened.protected;
          if (flattened.unprotected)
            jwe2.unprotected = flattened.unprotected;
          if (flattened.encrypted_key)
            jwe2.recipients[0].encrypted_key = flattened.encrypted_key;
          if (flattened.header)
            jwe2.recipients[0].header = flattened.header;
          return jwe2;
        }
        let enc;
        for (let i = 0; i < this._recipients.length; i++) {
          const recipient = this._recipients[i];
          if (!is_disjoint_default(this._protectedHeader, this._unprotectedHeader, recipient.unprotectedHeader)) {
            throw new JWEInvalid("JWE Protected, JWE Shared Unprotected and JWE Per-Recipient Header Parameter names must be disjoint");
          }
          const joseHeader = {
            ...this._protectedHeader,
            ...this._unprotectedHeader,
            ...recipient.unprotectedHeader
          };
          const { alg } = joseHeader;
          if (typeof alg !== "string" || !alg) {
            throw new JWEInvalid('JWE "alg" (Algorithm) Header Parameter missing or invalid');
          }
          if (alg === "dir" || alg === "ECDH-ES") {
            throw new JWEInvalid('"dir" and "ECDH-ES" alg may only be used with a single recipient');
          }
          if (typeof joseHeader.enc !== "string" || !joseHeader.enc) {
            throw new JWEInvalid('JWE "enc" (Encryption Algorithm) Header Parameter missing or invalid');
          }
          if (!enc) {
            enc = joseHeader.enc;
          } else if (enc !== joseHeader.enc) {
            throw new JWEInvalid('JWE "enc" (Encryption Algorithm) Header Parameter must be the same for all recipients');
          }
          validate_crit_default(JWEInvalid, /* @__PURE__ */ new Map(), recipient.options.crit, this._protectedHeader, joseHeader);
          if (joseHeader.zip !== void 0) {
            throw new JOSENotSupported('JWE "zip" (Compression Algorithm) Header Parameter is not supported.');
          }
        }
        const cek = cek_default(enc);
        const jwe = {
          ciphertext: "",
          iv: "",
          recipients: [],
          tag: ""
        };
        for (let i = 0; i < this._recipients.length; i++) {
          const recipient = this._recipients[i];
          const target = {};
          jwe.recipients.push(target);
          const joseHeader = {
            ...this._protectedHeader,
            ...this._unprotectedHeader,
            ...recipient.unprotectedHeader
          };
          const p2c = joseHeader.alg.startsWith("PBES2") ? 2048 + i : void 0;
          if (i === 0) {
            const flattened = await new FlattenedEncrypt(this._plaintext).setAdditionalAuthenticatedData(this._aad).setContentEncryptionKey(cek).setProtectedHeader(this._protectedHeader).setSharedUnprotectedHeader(this._unprotectedHeader).setUnprotectedHeader(recipient.unprotectedHeader).setKeyManagementParameters({ p2c }).encrypt(recipient.key, {
              ...recipient.options,
              [unprotected]: true
            });
            jwe.ciphertext = flattened.ciphertext;
            jwe.iv = flattened.iv;
            jwe.tag = flattened.tag;
            if (flattened.aad)
              jwe.aad = flattened.aad;
            if (flattened.protected)
              jwe.protected = flattened.protected;
            if (flattened.unprotected)
              jwe.unprotected = flattened.unprotected;
            target.encrypted_key = flattened.encrypted_key;
            if (flattened.header)
              target.header = flattened.header;
            continue;
          }
          const { encryptedKey, parameters } = await encrypt_key_management_default(recipient.unprotectedHeader?.alg || this._protectedHeader?.alg || this._unprotectedHeader?.alg, enc, recipient.key, cek, { p2c });
          target.encrypted_key = encode(encryptedKey);
          if (recipient.unprotectedHeader || parameters)
            target.header = { ...recipient.unprotectedHeader, ...parameters };
        }
        return jwe;
      }
    };
  }
});

// node_modules/jose/dist/browser/runtime/subtle_dsa.js
function subtleDsa(alg, algorithm) {
  const hash = `SHA-${alg.slice(-3)}`;
  switch (alg) {
    case "HS256":
    case "HS384":
    case "HS512":
      return { hash, name: "HMAC" };
    case "PS256":
    case "PS384":
    case "PS512":
      return { hash, name: "RSA-PSS", saltLength: alg.slice(-3) >> 3 };
    case "RS256":
    case "RS384":
    case "RS512":
      return { hash, name: "RSASSA-PKCS1-v1_5" };
    case "ES256":
    case "ES384":
    case "ES512":
      return { hash, name: "ECDSA", namedCurve: algorithm.namedCurve };
    case "Ed25519":
      return { name: "Ed25519" };
    case "EdDSA":
      return { name: algorithm.name };
    default:
      throw new JOSENotSupported(`alg ${alg} is not supported either by JOSE or your javascript runtime`);
  }
}
var init_subtle_dsa = __esm({
  "node_modules/jose/dist/browser/runtime/subtle_dsa.js"() {
    init_errors();
  }
});

// node_modules/jose/dist/browser/runtime/get_sign_verify_key.js
async function getCryptoKey3(alg, key, usage) {
  if (usage === "sign") {
    key = await normalize_key_default.normalizePrivateKey(key, alg);
  }
  if (usage === "verify") {
    key = await normalize_key_default.normalizePublicKey(key, alg);
  }
  if (isCryptoKey(key)) {
    checkSigCryptoKey(key, alg, usage);
    return key;
  }
  if (key instanceof Uint8Array) {
    if (!alg.startsWith("HS")) {
      throw new TypeError(invalid_key_input_default(key, ...types));
    }
    return webcrypto_default.subtle.importKey("raw", key, { hash: `SHA-${alg.slice(-3)}`, name: "HMAC" }, false, [usage]);
  }
  throw new TypeError(invalid_key_input_default(key, ...types, "Uint8Array", "JSON Web Key"));
}
var init_get_sign_verify_key = __esm({
  "node_modules/jose/dist/browser/runtime/get_sign_verify_key.js"() {
    init_webcrypto();
    init_crypto_key();
    init_invalid_key_input();
    init_is_key_like();
    init_normalize_key();
  }
});

// node_modules/jose/dist/browser/runtime/verify.js
var verify, verify_default;
var init_verify = __esm({
  "node_modules/jose/dist/browser/runtime/verify.js"() {
    init_subtle_dsa();
    init_webcrypto();
    init_check_key_length();
    init_get_sign_verify_key();
    verify = async (alg, key, signature, data) => {
      const cryptoKey = await getCryptoKey3(alg, key, "verify");
      check_key_length_default(alg, cryptoKey);
      const algorithm = subtleDsa(alg, cryptoKey.algorithm);
      try {
        return await webcrypto_default.subtle.verify(algorithm, cryptoKey, signature, data);
      } catch {
        return false;
      }
    };
    verify_default = verify;
  }
});

// node_modules/jose/dist/browser/jws/flattened/verify.js
async function flattenedVerify(jws, key, options) {
  if (!isObject(jws)) {
    throw new JWSInvalid("Flattened JWS must be an object");
  }
  if (jws.protected === void 0 && jws.header === void 0) {
    throw new JWSInvalid('Flattened JWS must have either of the "protected" or "header" members');
  }
  if (jws.protected !== void 0 && typeof jws.protected !== "string") {
    throw new JWSInvalid("JWS Protected Header incorrect type");
  }
  if (jws.payload === void 0) {
    throw new JWSInvalid("JWS Payload missing");
  }
  if (typeof jws.signature !== "string") {
    throw new JWSInvalid("JWS Signature missing or incorrect type");
  }
  if (jws.header !== void 0 && !isObject(jws.header)) {
    throw new JWSInvalid("JWS Unprotected Header incorrect type");
  }
  let parsedProt = {};
  if (jws.protected) {
    try {
      const protectedHeader = decode(jws.protected);
      parsedProt = JSON.parse(decoder.decode(protectedHeader));
    } catch {
      throw new JWSInvalid("JWS Protected Header is invalid");
    }
  }
  if (!is_disjoint_default(parsedProt, jws.header)) {
    throw new JWSInvalid("JWS Protected and JWS Unprotected Header Parameter names must be disjoint");
  }
  const joseHeader = {
    ...parsedProt,
    ...jws.header
  };
  const extensions = validate_crit_default(JWSInvalid, /* @__PURE__ */ new Map([["b64", true]]), options?.crit, parsedProt, joseHeader);
  let b64 = true;
  if (extensions.has("b64")) {
    b64 = parsedProt.b64;
    if (typeof b64 !== "boolean") {
      throw new JWSInvalid('The "b64" (base64url-encode payload) Header Parameter must be a boolean');
    }
  }
  const { alg } = joseHeader;
  if (typeof alg !== "string" || !alg) {
    throw new JWSInvalid('JWS "alg" (Algorithm) Header Parameter missing or invalid');
  }
  const algorithms = options && validate_algorithms_default("algorithms", options.algorithms);
  if (algorithms && !algorithms.has(alg)) {
    throw new JOSEAlgNotAllowed('"alg" (Algorithm) Header Parameter value not allowed');
  }
  if (b64) {
    if (typeof jws.payload !== "string") {
      throw new JWSInvalid("JWS Payload must be a string");
    }
  } else if (typeof jws.payload !== "string" && !(jws.payload instanceof Uint8Array)) {
    throw new JWSInvalid("JWS Payload must be a string or an Uint8Array instance");
  }
  let resolvedKey = false;
  if (typeof key === "function") {
    key = await key(parsedProt, jws);
    resolvedKey = true;
    checkKeyTypeWithJwk(alg, key, "verify");
    if (isJWK(key)) {
      key = await importJWK(key, alg);
    }
  } else {
    checkKeyTypeWithJwk(alg, key, "verify");
  }
  const data = concat(encoder.encode(jws.protected ?? ""), encoder.encode("."), typeof jws.payload === "string" ? encoder.encode(jws.payload) : jws.payload);
  let signature;
  try {
    signature = decode(jws.signature);
  } catch {
    throw new JWSInvalid("Failed to base64url decode the signature");
  }
  const verified = await verify_default(alg, key, signature, data);
  if (!verified) {
    throw new JWSSignatureVerificationFailed();
  }
  let payload;
  if (b64) {
    try {
      payload = decode(jws.payload);
    } catch {
      throw new JWSInvalid("Failed to base64url decode the payload");
    }
  } else if (typeof jws.payload === "string") {
    payload = encoder.encode(jws.payload);
  } else {
    payload = jws.payload;
  }
  const result = { payload };
  if (jws.protected !== void 0) {
    result.protectedHeader = parsedProt;
  }
  if (jws.header !== void 0) {
    result.unprotectedHeader = jws.header;
  }
  if (resolvedKey) {
    return { ...result, key };
  }
  return result;
}
var init_verify2 = __esm({
  "node_modules/jose/dist/browser/jws/flattened/verify.js"() {
    init_base64url();
    init_verify();
    init_errors();
    init_buffer_utils();
    init_is_disjoint();
    init_is_object();
    init_check_key_type();
    init_validate_crit();
    init_validate_algorithms();
    init_is_jwk();
    init_import();
  }
});

// node_modules/jose/dist/browser/jws/compact/verify.js
async function compactVerify(jws, key, options) {
  if (jws instanceof Uint8Array) {
    jws = decoder.decode(jws);
  }
  if (typeof jws !== "string") {
    throw new JWSInvalid("Compact JWS must be a string or Uint8Array");
  }
  const { 0: protectedHeader, 1: payload, 2: signature, length } = jws.split(".");
  if (length !== 3) {
    throw new JWSInvalid("Invalid Compact JWS");
  }
  const verified = await flattenedVerify({ payload, protected: protectedHeader, signature }, key, options);
  const result = { payload: verified.payload, protectedHeader: verified.protectedHeader };
  if (typeof key === "function") {
    return { ...result, key: verified.key };
  }
  return result;
}
var init_verify3 = __esm({
  "node_modules/jose/dist/browser/jws/compact/verify.js"() {
    init_verify2();
    init_errors();
    init_buffer_utils();
  }
});

// node_modules/jose/dist/browser/jws/general/verify.js
async function generalVerify(jws, key, options) {
  if (!isObject(jws)) {
    throw new JWSInvalid("General JWS must be an object");
  }
  if (!Array.isArray(jws.signatures) || !jws.signatures.every(isObject)) {
    throw new JWSInvalid("JWS Signatures missing or incorrect type");
  }
  for (const signature of jws.signatures) {
    try {
      return await flattenedVerify({
        header: signature.header,
        payload: jws.payload,
        protected: signature.protected,
        signature: signature.signature
      }, key, options);
    } catch {
    }
  }
  throw new JWSSignatureVerificationFailed();
}
var init_verify4 = __esm({
  "node_modules/jose/dist/browser/jws/general/verify.js"() {
    init_verify2();
    init_errors();
    init_is_object();
  }
});

// node_modules/jose/dist/browser/lib/epoch.js
var epoch_default;
var init_epoch = __esm({
  "node_modules/jose/dist/browser/lib/epoch.js"() {
    epoch_default = (date) => Math.floor(date.getTime() / 1e3);
  }
});

// node_modules/jose/dist/browser/lib/secs.js
var minute, hour, day, week, year, REGEX, secs_default;
var init_secs = __esm({
  "node_modules/jose/dist/browser/lib/secs.js"() {
    minute = 60;
    hour = minute * 60;
    day = hour * 24;
    week = day * 7;
    year = day * 365.25;
    REGEX = /^(\+|\-)? ?(\d+|\d+\.\d+) ?(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w|years?|yrs?|y)(?: (ago|from now))?$/i;
    secs_default = (str) => {
      const matched = REGEX.exec(str);
      if (!matched || matched[4] && matched[1]) {
        throw new TypeError("Invalid time period format");
      }
      const value = parseFloat(matched[2]);
      const unit = matched[3].toLowerCase();
      let numericDate;
      switch (unit) {
        case "sec":
        case "secs":
        case "second":
        case "seconds":
        case "s":
          numericDate = Math.round(value);
          break;
        case "minute":
        case "minutes":
        case "min":
        case "mins":
        case "m":
          numericDate = Math.round(value * minute);
          break;
        case "hour":
        case "hours":
        case "hr":
        case "hrs":
        case "h":
          numericDate = Math.round(value * hour);
          break;
        case "day":
        case "days":
        case "d":
          numericDate = Math.round(value * day);
          break;
        case "week":
        case "weeks":
        case "w":
          numericDate = Math.round(value * week);
          break;
        default:
          numericDate = Math.round(value * year);
          break;
      }
      if (matched[1] === "-" || matched[4] === "ago") {
        return -numericDate;
      }
      return numericDate;
    };
  }
});

// node_modules/jose/dist/browser/lib/jwt_claims_set.js
var normalizeTyp, checkAudiencePresence, jwt_claims_set_default;
var init_jwt_claims_set = __esm({
  "node_modules/jose/dist/browser/lib/jwt_claims_set.js"() {
    init_errors();
    init_buffer_utils();
    init_epoch();
    init_secs();
    init_is_object();
    normalizeTyp = (value) => value.toLowerCase().replace(/^application\//, "");
    checkAudiencePresence = (audPayload, audOption) => {
      if (typeof audPayload === "string") {
        return audOption.includes(audPayload);
      }
      if (Array.isArray(audPayload)) {
        return audOption.some(Set.prototype.has.bind(new Set(audPayload)));
      }
      return false;
    };
    jwt_claims_set_default = (protectedHeader, encodedPayload, options = {}) => {
      let payload;
      try {
        payload = JSON.parse(decoder.decode(encodedPayload));
      } catch {
      }
      if (!isObject(payload)) {
        throw new JWTInvalid("JWT Claims Set must be a top-level JSON object");
      }
      const { typ } = options;
      if (typ && (typeof protectedHeader.typ !== "string" || normalizeTyp(protectedHeader.typ) !== normalizeTyp(typ))) {
        throw new JWTClaimValidationFailed('unexpected "typ" JWT header value', payload, "typ", "check_failed");
      }
      const { requiredClaims = [], issuer, subject, audience, maxTokenAge } = options;
      const presenceCheck = [...requiredClaims];
      if (maxTokenAge !== void 0)
        presenceCheck.push("iat");
      if (audience !== void 0)
        presenceCheck.push("aud");
      if (subject !== void 0)
        presenceCheck.push("sub");
      if (issuer !== void 0)
        presenceCheck.push("iss");
      for (const claim of new Set(presenceCheck.reverse())) {
        if (!(claim in payload)) {
          throw new JWTClaimValidationFailed(`missing required "${claim}" claim`, payload, claim, "missing");
        }
      }
      if (issuer && !(Array.isArray(issuer) ? issuer : [issuer]).includes(payload.iss)) {
        throw new JWTClaimValidationFailed('unexpected "iss" claim value', payload, "iss", "check_failed");
      }
      if (subject && payload.sub !== subject) {
        throw new JWTClaimValidationFailed('unexpected "sub" claim value', payload, "sub", "check_failed");
      }
      if (audience && !checkAudiencePresence(payload.aud, typeof audience === "string" ? [audience] : audience)) {
        throw new JWTClaimValidationFailed('unexpected "aud" claim value', payload, "aud", "check_failed");
      }
      let tolerance;
      switch (typeof options.clockTolerance) {
        case "string":
          tolerance = secs_default(options.clockTolerance);
          break;
        case "number":
          tolerance = options.clockTolerance;
          break;
        case "undefined":
          tolerance = 0;
          break;
        default:
          throw new TypeError("Invalid clockTolerance option type");
      }
      const { currentDate } = options;
      const now = epoch_default(currentDate || /* @__PURE__ */ new Date());
      if ((payload.iat !== void 0 || maxTokenAge) && typeof payload.iat !== "number") {
        throw new JWTClaimValidationFailed('"iat" claim must be a number', payload, "iat", "invalid");
      }
      if (payload.nbf !== void 0) {
        if (typeof payload.nbf !== "number") {
          throw new JWTClaimValidationFailed('"nbf" claim must be a number', payload, "nbf", "invalid");
        }
        if (payload.nbf > now + tolerance) {
          throw new JWTClaimValidationFailed('"nbf" claim timestamp check failed', payload, "nbf", "check_failed");
        }
      }
      if (payload.exp !== void 0) {
        if (typeof payload.exp !== "number") {
          throw new JWTClaimValidationFailed('"exp" claim must be a number', payload, "exp", "invalid");
        }
        if (payload.exp <= now - tolerance) {
          throw new JWTExpired('"exp" claim timestamp check failed', payload, "exp", "check_failed");
        }
      }
      if (maxTokenAge) {
        const age = now - payload.iat;
        const max = typeof maxTokenAge === "number" ? maxTokenAge : secs_default(maxTokenAge);
        if (age - tolerance > max) {
          throw new JWTExpired('"iat" claim timestamp check failed (too far in the past)', payload, "iat", "check_failed");
        }
        if (age < 0 - tolerance) {
          throw new JWTClaimValidationFailed('"iat" claim timestamp check failed (it should be in the past)', payload, "iat", "check_failed");
        }
      }
      return payload;
    };
  }
});

// node_modules/jose/dist/browser/jwt/verify.js
async function jwtVerify(jwt, key, options) {
  const verified = await compactVerify(jwt, key, options);
  if (verified.protectedHeader.crit?.includes("b64") && verified.protectedHeader.b64 === false) {
    throw new JWTInvalid("JWTs MUST NOT use unencoded payload");
  }
  const payload = jwt_claims_set_default(verified.protectedHeader, verified.payload, options);
  const result = { payload, protectedHeader: verified.protectedHeader };
  if (typeof key === "function") {
    return { ...result, key: verified.key };
  }
  return result;
}
var init_verify5 = __esm({
  "node_modules/jose/dist/browser/jwt/verify.js"() {
    init_verify3();
    init_jwt_claims_set();
    init_errors();
  }
});

// node_modules/jose/dist/browser/jwt/decrypt.js
async function jwtDecrypt(jwt, key, options) {
  const decrypted = await compactDecrypt(jwt, key, options);
  const payload = jwt_claims_set_default(decrypted.protectedHeader, decrypted.plaintext, options);
  const { protectedHeader } = decrypted;
  if (protectedHeader.iss !== void 0 && protectedHeader.iss !== payload.iss) {
    throw new JWTClaimValidationFailed('replicated "iss" claim header parameter mismatch', payload, "iss", "mismatch");
  }
  if (protectedHeader.sub !== void 0 && protectedHeader.sub !== payload.sub) {
    throw new JWTClaimValidationFailed('replicated "sub" claim header parameter mismatch', payload, "sub", "mismatch");
  }
  if (protectedHeader.aud !== void 0 && JSON.stringify(protectedHeader.aud) !== JSON.stringify(payload.aud)) {
    throw new JWTClaimValidationFailed('replicated "aud" claim header parameter mismatch', payload, "aud", "mismatch");
  }
  const result = { payload, protectedHeader };
  if (typeof key === "function") {
    return { ...result, key: decrypted.key };
  }
  return result;
}
var init_decrypt5 = __esm({
  "node_modules/jose/dist/browser/jwt/decrypt.js"() {
    init_decrypt3();
    init_jwt_claims_set();
    init_errors();
  }
});

// node_modules/jose/dist/browser/jwe/compact/encrypt.js
var CompactEncrypt;
var init_encrypt4 = __esm({
  "node_modules/jose/dist/browser/jwe/compact/encrypt.js"() {
    init_encrypt2();
    CompactEncrypt = class {
      constructor(plaintext) {
        this._flattened = new FlattenedEncrypt(plaintext);
      }
      setContentEncryptionKey(cek) {
        this._flattened.setContentEncryptionKey(cek);
        return this;
      }
      setInitializationVector(iv) {
        this._flattened.setInitializationVector(iv);
        return this;
      }
      setProtectedHeader(protectedHeader) {
        this._flattened.setProtectedHeader(protectedHeader);
        return this;
      }
      setKeyManagementParameters(parameters) {
        this._flattened.setKeyManagementParameters(parameters);
        return this;
      }
      async encrypt(key, options) {
        const jwe = await this._flattened.encrypt(key, options);
        return [jwe.protected, jwe.encrypted_key, jwe.iv, jwe.ciphertext, jwe.tag].join(".");
      }
    };
  }
});

// node_modules/jose/dist/browser/runtime/sign.js
var sign, sign_default;
var init_sign = __esm({
  "node_modules/jose/dist/browser/runtime/sign.js"() {
    init_subtle_dsa();
    init_webcrypto();
    init_check_key_length();
    init_get_sign_verify_key();
    sign = async (alg, key, data) => {
      const cryptoKey = await getCryptoKey3(alg, key, "sign");
      check_key_length_default(alg, cryptoKey);
      const signature = await webcrypto_default.subtle.sign(subtleDsa(alg, cryptoKey.algorithm), cryptoKey, data);
      return new Uint8Array(signature);
    };
    sign_default = sign;
  }
});

// node_modules/jose/dist/browser/jws/flattened/sign.js
var FlattenedSign;
var init_sign2 = __esm({
  "node_modules/jose/dist/browser/jws/flattened/sign.js"() {
    init_base64url();
    init_sign();
    init_is_disjoint();
    init_errors();
    init_buffer_utils();
    init_check_key_type();
    init_validate_crit();
    FlattenedSign = class {
      constructor(payload) {
        if (!(payload instanceof Uint8Array)) {
          throw new TypeError("payload must be an instance of Uint8Array");
        }
        this._payload = payload;
      }
      setProtectedHeader(protectedHeader) {
        if (this._protectedHeader) {
          throw new TypeError("setProtectedHeader can only be called once");
        }
        this._protectedHeader = protectedHeader;
        return this;
      }
      setUnprotectedHeader(unprotectedHeader) {
        if (this._unprotectedHeader) {
          throw new TypeError("setUnprotectedHeader can only be called once");
        }
        this._unprotectedHeader = unprotectedHeader;
        return this;
      }
      async sign(key, options) {
        if (!this._protectedHeader && !this._unprotectedHeader) {
          throw new JWSInvalid("either setProtectedHeader or setUnprotectedHeader must be called before #sign()");
        }
        if (!is_disjoint_default(this._protectedHeader, this._unprotectedHeader)) {
          throw new JWSInvalid("JWS Protected and JWS Unprotected Header Parameter names must be disjoint");
        }
        const joseHeader = {
          ...this._protectedHeader,
          ...this._unprotectedHeader
        };
        const extensions = validate_crit_default(JWSInvalid, /* @__PURE__ */ new Map([["b64", true]]), options?.crit, this._protectedHeader, joseHeader);
        let b64 = true;
        if (extensions.has("b64")) {
          b64 = this._protectedHeader.b64;
          if (typeof b64 !== "boolean") {
            throw new JWSInvalid('The "b64" (base64url-encode payload) Header Parameter must be a boolean');
          }
        }
        const { alg } = joseHeader;
        if (typeof alg !== "string" || !alg) {
          throw new JWSInvalid('JWS "alg" (Algorithm) Header Parameter missing or invalid');
        }
        checkKeyTypeWithJwk(alg, key, "sign");
        let payload = this._payload;
        if (b64) {
          payload = encoder.encode(encode(payload));
        }
        let protectedHeader;
        if (this._protectedHeader) {
          protectedHeader = encoder.encode(encode(JSON.stringify(this._protectedHeader)));
        } else {
          protectedHeader = encoder.encode("");
        }
        const data = concat(protectedHeader, encoder.encode("."), payload);
        const signature = await sign_default(alg, key, data);
        const jws = {
          signature: encode(signature),
          payload: ""
        };
        if (b64) {
          jws.payload = decoder.decode(payload);
        }
        if (this._unprotectedHeader) {
          jws.header = this._unprotectedHeader;
        }
        if (this._protectedHeader) {
          jws.protected = decoder.decode(protectedHeader);
        }
        return jws;
      }
    };
  }
});

// node_modules/jose/dist/browser/jws/compact/sign.js
var CompactSign;
var init_sign3 = __esm({
  "node_modules/jose/dist/browser/jws/compact/sign.js"() {
    init_sign2();
    CompactSign = class {
      constructor(payload) {
        this._flattened = new FlattenedSign(payload);
      }
      setProtectedHeader(protectedHeader) {
        this._flattened.setProtectedHeader(protectedHeader);
        return this;
      }
      async sign(key, options) {
        const jws = await this._flattened.sign(key, options);
        if (jws.payload === void 0) {
          throw new TypeError("use the flattened module for creating JWS with b64: false");
        }
        return `${jws.protected}.${jws.payload}.${jws.signature}`;
      }
    };
  }
});

// node_modules/jose/dist/browser/jws/general/sign.js
var IndividualSignature, GeneralSign;
var init_sign4 = __esm({
  "node_modules/jose/dist/browser/jws/general/sign.js"() {
    init_sign2();
    init_errors();
    IndividualSignature = class {
      constructor(sig, key, options) {
        this.parent = sig;
        this.key = key;
        this.options = options;
      }
      setProtectedHeader(protectedHeader) {
        if (this.protectedHeader) {
          throw new TypeError("setProtectedHeader can only be called once");
        }
        this.protectedHeader = protectedHeader;
        return this;
      }
      setUnprotectedHeader(unprotectedHeader) {
        if (this.unprotectedHeader) {
          throw new TypeError("setUnprotectedHeader can only be called once");
        }
        this.unprotectedHeader = unprotectedHeader;
        return this;
      }
      addSignature(...args) {
        return this.parent.addSignature(...args);
      }
      sign(...args) {
        return this.parent.sign(...args);
      }
      done() {
        return this.parent;
      }
    };
    GeneralSign = class {
      constructor(payload) {
        this._signatures = [];
        this._payload = payload;
      }
      addSignature(key, options) {
        const signature = new IndividualSignature(this, key, options);
        this._signatures.push(signature);
        return signature;
      }
      async sign() {
        if (!this._signatures.length) {
          throw new JWSInvalid("at least one signature must be added");
        }
        const jws = {
          signatures: [],
          payload: ""
        };
        for (let i = 0; i < this._signatures.length; i++) {
          const signature = this._signatures[i];
          const flattened = new FlattenedSign(this._payload);
          flattened.setProtectedHeader(signature.protectedHeader);
          flattened.setUnprotectedHeader(signature.unprotectedHeader);
          const { payload, ...rest } = await flattened.sign(signature.key, signature.options);
          if (i === 0) {
            jws.payload = payload;
          } else if (jws.payload !== payload) {
            throw new JWSInvalid("inconsistent use of JWS Unencoded Payload (RFC7797)");
          }
          jws.signatures.push(rest);
        }
        return jws;
      }
    };
  }
});

// node_modules/jose/dist/browser/jwt/produce.js
function validateInput(label, input) {
  if (!Number.isFinite(input)) {
    throw new TypeError(`Invalid ${label} input`);
  }
  return input;
}
var ProduceJWT;
var init_produce = __esm({
  "node_modules/jose/dist/browser/jwt/produce.js"() {
    init_epoch();
    init_is_object();
    init_secs();
    ProduceJWT = class {
      constructor(payload = {}) {
        if (!isObject(payload)) {
          throw new TypeError("JWT Claims Set MUST be an object");
        }
        this._payload = payload;
      }
      setIssuer(issuer) {
        this._payload = { ...this._payload, iss: issuer };
        return this;
      }
      setSubject(subject) {
        this._payload = { ...this._payload, sub: subject };
        return this;
      }
      setAudience(audience) {
        this._payload = { ...this._payload, aud: audience };
        return this;
      }
      setJti(jwtId) {
        this._payload = { ...this._payload, jti: jwtId };
        return this;
      }
      setNotBefore(input) {
        if (typeof input === "number") {
          this._payload = { ...this._payload, nbf: validateInput("setNotBefore", input) };
        } else if (input instanceof Date) {
          this._payload = { ...this._payload, nbf: validateInput("setNotBefore", epoch_default(input)) };
        } else {
          this._payload = { ...this._payload, nbf: epoch_default(/* @__PURE__ */ new Date()) + secs_default(input) };
        }
        return this;
      }
      setExpirationTime(input) {
        if (typeof input === "number") {
          this._payload = { ...this._payload, exp: validateInput("setExpirationTime", input) };
        } else if (input instanceof Date) {
          this._payload = { ...this._payload, exp: validateInput("setExpirationTime", epoch_default(input)) };
        } else {
          this._payload = { ...this._payload, exp: epoch_default(/* @__PURE__ */ new Date()) + secs_default(input) };
        }
        return this;
      }
      setIssuedAt(input) {
        if (typeof input === "undefined") {
          this._payload = { ...this._payload, iat: epoch_default(/* @__PURE__ */ new Date()) };
        } else if (input instanceof Date) {
          this._payload = { ...this._payload, iat: validateInput("setIssuedAt", epoch_default(input)) };
        } else if (typeof input === "string") {
          this._payload = {
            ...this._payload,
            iat: validateInput("setIssuedAt", epoch_default(/* @__PURE__ */ new Date()) + secs_default(input))
          };
        } else {
          this._payload = { ...this._payload, iat: validateInput("setIssuedAt", input) };
        }
        return this;
      }
    };
  }
});

// node_modules/jose/dist/browser/jwt/sign.js
var SignJWT;
var init_sign5 = __esm({
  "node_modules/jose/dist/browser/jwt/sign.js"() {
    init_sign3();
    init_errors();
    init_buffer_utils();
    init_produce();
    SignJWT = class extends ProduceJWT {
      setProtectedHeader(protectedHeader) {
        this._protectedHeader = protectedHeader;
        return this;
      }
      async sign(key, options) {
        const sig = new CompactSign(encoder.encode(JSON.stringify(this._payload)));
        sig.setProtectedHeader(this._protectedHeader);
        if (Array.isArray(this._protectedHeader?.crit) && this._protectedHeader.crit.includes("b64") && this._protectedHeader.b64 === false) {
          throw new JWTInvalid("JWTs MUST NOT use unencoded payload");
        }
        return sig.sign(key, options);
      }
    };
  }
});

// node_modules/jose/dist/browser/jwt/encrypt.js
var EncryptJWT;
var init_encrypt5 = __esm({
  "node_modules/jose/dist/browser/jwt/encrypt.js"() {
    init_encrypt4();
    init_buffer_utils();
    init_produce();
    EncryptJWT = class extends ProduceJWT {
      setProtectedHeader(protectedHeader) {
        if (this._protectedHeader) {
          throw new TypeError("setProtectedHeader can only be called once");
        }
        this._protectedHeader = protectedHeader;
        return this;
      }
      setKeyManagementParameters(parameters) {
        if (this._keyManagementParameters) {
          throw new TypeError("setKeyManagementParameters can only be called once");
        }
        this._keyManagementParameters = parameters;
        return this;
      }
      setContentEncryptionKey(cek) {
        if (this._cek) {
          throw new TypeError("setContentEncryptionKey can only be called once");
        }
        this._cek = cek;
        return this;
      }
      setInitializationVector(iv) {
        if (this._iv) {
          throw new TypeError("setInitializationVector can only be called once");
        }
        this._iv = iv;
        return this;
      }
      replicateIssuerAsHeader() {
        this._replicateIssuerAsHeader = true;
        return this;
      }
      replicateSubjectAsHeader() {
        this._replicateSubjectAsHeader = true;
        return this;
      }
      replicateAudienceAsHeader() {
        this._replicateAudienceAsHeader = true;
        return this;
      }
      async encrypt(key, options) {
        const enc = new CompactEncrypt(encoder.encode(JSON.stringify(this._payload)));
        if (this._replicateIssuerAsHeader) {
          this._protectedHeader = { ...this._protectedHeader, iss: this._payload.iss };
        }
        if (this._replicateSubjectAsHeader) {
          this._protectedHeader = { ...this._protectedHeader, sub: this._payload.sub };
        }
        if (this._replicateAudienceAsHeader) {
          this._protectedHeader = { ...this._protectedHeader, aud: this._payload.aud };
        }
        enc.setProtectedHeader(this._protectedHeader);
        if (this._iv) {
          enc.setInitializationVector(this._iv);
        }
        if (this._cek) {
          enc.setContentEncryptionKey(this._cek);
        }
        if (this._keyManagementParameters) {
          enc.setKeyManagementParameters(this._keyManagementParameters);
        }
        return enc.encrypt(key, options);
      }
    };
  }
});

// node_modules/jose/dist/browser/jwk/thumbprint.js
async function calculateJwkThumbprint(jwk, digestAlgorithm) {
  if (!isObject(jwk)) {
    throw new TypeError("JWK must be an object");
  }
  digestAlgorithm ?? (digestAlgorithm = "sha256");
  if (digestAlgorithm !== "sha256" && digestAlgorithm !== "sha384" && digestAlgorithm !== "sha512") {
    throw new TypeError('digestAlgorithm must one of "sha256", "sha384", or "sha512"');
  }
  let components;
  switch (jwk.kty) {
    case "EC":
      check(jwk.crv, '"crv" (Curve) Parameter');
      check(jwk.x, '"x" (X Coordinate) Parameter');
      check(jwk.y, '"y" (Y Coordinate) Parameter');
      components = { crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y };
      break;
    case "OKP":
      check(jwk.crv, '"crv" (Subtype of Key Pair) Parameter');
      check(jwk.x, '"x" (Public Key) Parameter');
      components = { crv: jwk.crv, kty: jwk.kty, x: jwk.x };
      break;
    case "RSA":
      check(jwk.e, '"e" (Exponent) Parameter');
      check(jwk.n, '"n" (Modulus) Parameter');
      components = { e: jwk.e, kty: jwk.kty, n: jwk.n };
      break;
    case "oct":
      check(jwk.k, '"k" (Key Value) Parameter');
      components = { k: jwk.k, kty: jwk.kty };
      break;
    default:
      throw new JOSENotSupported('"kty" (Key Type) Parameter missing or unsupported');
  }
  const data = encoder.encode(JSON.stringify(components));
  return encode(await digest_default(digestAlgorithm, data));
}
async function calculateJwkThumbprintUri(jwk, digestAlgorithm) {
  digestAlgorithm ?? (digestAlgorithm = "sha256");
  const thumbprint = await calculateJwkThumbprint(jwk, digestAlgorithm);
  return `urn:ietf:params:oauth:jwk-thumbprint:sha-${digestAlgorithm.slice(-3)}:${thumbprint}`;
}
var check;
var init_thumbprint = __esm({
  "node_modules/jose/dist/browser/jwk/thumbprint.js"() {
    init_digest();
    init_base64url();
    init_errors();
    init_buffer_utils();
    init_is_object();
    check = (value, description) => {
      if (typeof value !== "string" || !value) {
        throw new JWKInvalid(`${description} missing or invalid`);
      }
    };
  }
});

// node_modules/jose/dist/browser/jwk/embedded.js
async function EmbeddedJWK(protectedHeader, token) {
  const joseHeader = {
    ...protectedHeader,
    ...token?.header
  };
  if (!isObject(joseHeader.jwk)) {
    throw new JWSInvalid('"jwk" (JSON Web Key) Header Parameter must be a JSON object');
  }
  const key = await importJWK({ ...joseHeader.jwk, ext: true }, joseHeader.alg);
  if (key instanceof Uint8Array || key.type !== "public") {
    throw new JWSInvalid('"jwk" (JSON Web Key) Header Parameter must be a public key');
  }
  return key;
}
var init_embedded = __esm({
  "node_modules/jose/dist/browser/jwk/embedded.js"() {
    init_import();
    init_is_object();
    init_errors();
  }
});

// node_modules/jose/dist/browser/jwks/local.js
function getKtyFromAlg(alg) {
  switch (typeof alg === "string" && alg.slice(0, 2)) {
    case "RS":
    case "PS":
      return "RSA";
    case "ES":
      return "EC";
    case "Ed":
      return "OKP";
    default:
      throw new JOSENotSupported('Unsupported "alg" value for a JSON Web Key Set');
  }
}
function isJWKSLike(jwks) {
  return jwks && typeof jwks === "object" && Array.isArray(jwks.keys) && jwks.keys.every(isJWKLike);
}
function isJWKLike(key) {
  return isObject(key);
}
function clone(obj) {
  if (typeof structuredClone === "function") {
    return structuredClone(obj);
  }
  return JSON.parse(JSON.stringify(obj));
}
async function importWithAlgCache(cache, jwk, alg) {
  const cached = cache.get(jwk) || cache.set(jwk, {}).get(jwk);
  if (cached[alg] === void 0) {
    const key = await importJWK({ ...jwk, ext: true }, alg);
    if (key instanceof Uint8Array || key.type !== "public") {
      throw new JWKSInvalid("JSON Web Key Set members must be public keys");
    }
    cached[alg] = key;
  }
  return cached[alg];
}
function createLocalJWKSet(jwks) {
  const set = new LocalJWKSet(jwks);
  const localJWKSet = async (protectedHeader, token) => set.getKey(protectedHeader, token);
  Object.defineProperties(localJWKSet, {
    jwks: {
      value: () => clone(set._jwks),
      enumerable: true,
      configurable: false,
      writable: false
    }
  });
  return localJWKSet;
}
var LocalJWKSet;
var init_local = __esm({
  "node_modules/jose/dist/browser/jwks/local.js"() {
    init_import();
    init_errors();
    init_is_object();
    LocalJWKSet = class {
      constructor(jwks) {
        this._cached = /* @__PURE__ */ new WeakMap();
        if (!isJWKSLike(jwks)) {
          throw new JWKSInvalid("JSON Web Key Set malformed");
        }
        this._jwks = clone(jwks);
      }
      async getKey(protectedHeader, token) {
        const { alg, kid } = { ...protectedHeader, ...token?.header };
        const kty = getKtyFromAlg(alg);
        const candidates = this._jwks.keys.filter((jwk2) => {
          let candidate = kty === jwk2.kty;
          if (candidate && typeof kid === "string") {
            candidate = kid === jwk2.kid;
          }
          if (candidate && typeof jwk2.alg === "string") {
            candidate = alg === jwk2.alg;
          }
          if (candidate && typeof jwk2.use === "string") {
            candidate = jwk2.use === "sig";
          }
          if (candidate && Array.isArray(jwk2.key_ops)) {
            candidate = jwk2.key_ops.includes("verify");
          }
          if (candidate) {
            switch (alg) {
              case "ES256":
                candidate = jwk2.crv === "P-256";
                break;
              case "ES256K":
                candidate = jwk2.crv === "secp256k1";
                break;
              case "ES384":
                candidate = jwk2.crv === "P-384";
                break;
              case "ES512":
                candidate = jwk2.crv === "P-521";
                break;
              case "Ed25519":
                candidate = jwk2.crv === "Ed25519";
                break;
              case "EdDSA":
                candidate = jwk2.crv === "Ed25519" || jwk2.crv === "Ed448";
                break;
            }
          }
          return candidate;
        });
        const { 0: jwk, length } = candidates;
        if (length === 0) {
          throw new JWKSNoMatchingKey();
        }
        if (length !== 1) {
          const error = new JWKSMultipleMatchingKeys();
          const { _cached } = this;
          error[Symbol.asyncIterator] = async function* () {
            for (const jwk2 of candidates) {
              try {
                yield await importWithAlgCache(_cached, jwk2, alg);
              } catch {
              }
            }
          };
          throw error;
        }
        return importWithAlgCache(this._cached, jwk, alg);
      }
    };
  }
});

// node_modules/jose/dist/browser/runtime/fetch_jwks.js
var fetchJwks, fetch_jwks_default;
var init_fetch_jwks = __esm({
  "node_modules/jose/dist/browser/runtime/fetch_jwks.js"() {
    init_errors();
    fetchJwks = async (url, timeout, options) => {
      let controller;
      let id;
      let timedOut = false;
      if (typeof AbortController === "function") {
        controller = new AbortController();
        id = setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, timeout);
      }
      const response = await fetch(url.href, {
        signal: controller ? controller.signal : void 0,
        redirect: "manual",
        headers: options.headers
      }).catch((err) => {
        if (timedOut)
          throw new JWKSTimeout();
        throw err;
      });
      if (id !== void 0)
        clearTimeout(id);
      if (response.status !== 200) {
        throw new JOSEError("Expected 200 OK from the JSON Web Key Set HTTP response");
      }
      try {
        return await response.json();
      } catch {
        throw new JOSEError("Failed to parse the JSON Web Key Set HTTP response as JSON");
      }
    };
    fetch_jwks_default = fetchJwks;
  }
});

// node_modules/jose/dist/browser/jwks/remote.js
function isCloudflareWorkers() {
  return typeof WebSocketPair !== "undefined" || typeof navigator !== "undefined" && navigator.userAgent === "Cloudflare-Workers" || typeof EdgeRuntime !== "undefined" && EdgeRuntime === "vercel";
}
function isFreshJwksCache(input, cacheMaxAge) {
  if (typeof input !== "object" || input === null) {
    return false;
  }
  if (!("uat" in input) || typeof input.uat !== "number" || Date.now() - input.uat >= cacheMaxAge) {
    return false;
  }
  if (!("jwks" in input) || !isObject(input.jwks) || !Array.isArray(input.jwks.keys) || !Array.prototype.every.call(input.jwks.keys, isObject)) {
    return false;
  }
  return true;
}
function createRemoteJWKSet(url, options) {
  const set = new RemoteJWKSet(url, options);
  const remoteJWKSet = async (protectedHeader, token) => set.getKey(protectedHeader, token);
  Object.defineProperties(remoteJWKSet, {
    coolingDown: {
      get: () => set.coolingDown(),
      enumerable: true,
      configurable: false
    },
    fresh: {
      get: () => set.fresh(),
      enumerable: true,
      configurable: false
    },
    reload: {
      value: () => set.reload(),
      enumerable: true,
      configurable: false,
      writable: false
    },
    reloading: {
      get: () => !!set._pendingFetch,
      enumerable: true,
      configurable: false
    },
    jwks: {
      value: () => set._local?.jwks(),
      enumerable: true,
      configurable: false,
      writable: false
    }
  });
  return remoteJWKSet;
}
var USER_AGENT, jwksCache, RemoteJWKSet, experimental_jwksCache;
var init_remote = __esm({
  "node_modules/jose/dist/browser/jwks/remote.js"() {
    init_fetch_jwks();
    init_errors();
    init_local();
    init_is_object();
    if (typeof navigator === "undefined" || !navigator.userAgent?.startsWith?.("Mozilla/5.0 ")) {
      const NAME = "jose";
      const VERSION = "v5.10.0";
      USER_AGENT = `${NAME}/${VERSION}`;
    }
    jwksCache = /* @__PURE__ */ Symbol();
    RemoteJWKSet = class {
      constructor(url, options) {
        if (!(url instanceof URL)) {
          throw new TypeError("url must be an instance of URL");
        }
        this._url = new URL(url.href);
        this._options = { agent: options?.agent, headers: options?.headers };
        this._timeoutDuration = typeof options?.timeoutDuration === "number" ? options?.timeoutDuration : 5e3;
        this._cooldownDuration = typeof options?.cooldownDuration === "number" ? options?.cooldownDuration : 3e4;
        this._cacheMaxAge = typeof options?.cacheMaxAge === "number" ? options?.cacheMaxAge : 6e5;
        if (options?.[jwksCache] !== void 0) {
          this._cache = options?.[jwksCache];
          if (isFreshJwksCache(options?.[jwksCache], this._cacheMaxAge)) {
            this._jwksTimestamp = this._cache.uat;
            this._local = createLocalJWKSet(this._cache.jwks);
          }
        }
      }
      coolingDown() {
        return typeof this._jwksTimestamp === "number" ? Date.now() < this._jwksTimestamp + this._cooldownDuration : false;
      }
      fresh() {
        return typeof this._jwksTimestamp === "number" ? Date.now() < this._jwksTimestamp + this._cacheMaxAge : false;
      }
      async getKey(protectedHeader, token) {
        if (!this._local || !this.fresh()) {
          await this.reload();
        }
        try {
          return await this._local(protectedHeader, token);
        } catch (err) {
          if (err instanceof JWKSNoMatchingKey) {
            if (this.coolingDown() === false) {
              await this.reload();
              return this._local(protectedHeader, token);
            }
          }
          throw err;
        }
      }
      async reload() {
        if (this._pendingFetch && isCloudflareWorkers()) {
          this._pendingFetch = void 0;
        }
        const headers = new Headers(this._options.headers);
        if (USER_AGENT && !headers.has("User-Agent")) {
          headers.set("User-Agent", USER_AGENT);
          this._options.headers = Object.fromEntries(headers.entries());
        }
        this._pendingFetch || (this._pendingFetch = fetch_jwks_default(this._url, this._timeoutDuration, this._options).then((json) => {
          this._local = createLocalJWKSet(json);
          if (this._cache) {
            this._cache.uat = Date.now();
            this._cache.jwks = json;
          }
          this._jwksTimestamp = Date.now();
          this._pendingFetch = void 0;
        }).catch((err) => {
          this._pendingFetch = void 0;
          throw err;
        }));
        await this._pendingFetch;
      }
    };
    experimental_jwksCache = jwksCache;
  }
});

// node_modules/jose/dist/browser/jwt/unsecured.js
var UnsecuredJWT;
var init_unsecured = __esm({
  "node_modules/jose/dist/browser/jwt/unsecured.js"() {
    init_base64url();
    init_buffer_utils();
    init_errors();
    init_jwt_claims_set();
    init_produce();
    UnsecuredJWT = class extends ProduceJWT {
      encode() {
        const header = encode(JSON.stringify({ alg: "none" }));
        const payload = encode(JSON.stringify(this._payload));
        return `${header}.${payload}.`;
      }
      static decode(jwt, options) {
        if (typeof jwt !== "string") {
          throw new JWTInvalid("Unsecured JWT must be a string");
        }
        const { 0: encodedHeader, 1: encodedPayload, 2: signature, length } = jwt.split(".");
        if (length !== 3 || signature !== "") {
          throw new JWTInvalid("Invalid Unsecured JWT");
        }
        let header;
        try {
          header = JSON.parse(decoder.decode(decode(encodedHeader)));
          if (header.alg !== "none")
            throw new Error();
        } catch {
          throw new JWTInvalid("Invalid Unsecured JWT");
        }
        const payload = jwt_claims_set_default(header, decode(encodedPayload), options);
        return { payload, header };
      }
    };
  }
});

// node_modules/jose/dist/browser/util/base64url.js
var base64url_exports2 = {};
__export(base64url_exports2, {
  decode: () => decode2,
  encode: () => encode2
});
var encode2, decode2;
var init_base64url2 = __esm({
  "node_modules/jose/dist/browser/util/base64url.js"() {
    init_base64url();
    encode2 = encode;
    decode2 = decode;
  }
});

// node_modules/jose/dist/browser/util/decode_protected_header.js
function decodeProtectedHeader(token) {
  let protectedB64u;
  if (typeof token === "string") {
    const parts = token.split(".");
    if (parts.length === 3 || parts.length === 5) {
      ;
      [protectedB64u] = parts;
    }
  } else if (typeof token === "object" && token) {
    if ("protected" in token) {
      protectedB64u = token.protected;
    } else {
      throw new TypeError("Token does not contain a Protected Header");
    }
  }
  try {
    if (typeof protectedB64u !== "string" || !protectedB64u) {
      throw new Error();
    }
    const result = JSON.parse(decoder.decode(decode2(protectedB64u)));
    if (!isObject(result)) {
      throw new Error();
    }
    return result;
  } catch {
    throw new TypeError("Invalid Token or Protected Header formatting");
  }
}
var init_decode_protected_header = __esm({
  "node_modules/jose/dist/browser/util/decode_protected_header.js"() {
    init_base64url2();
    init_buffer_utils();
    init_is_object();
  }
});

// node_modules/jose/dist/browser/util/decode_jwt.js
function decodeJwt(jwt) {
  if (typeof jwt !== "string")
    throw new JWTInvalid("JWTs must use Compact JWS serialization, JWT must be a string");
  const { 1: payload, length } = jwt.split(".");
  if (length === 5)
    throw new JWTInvalid("Only JWTs using Compact JWS serialization can be decoded");
  if (length !== 3)
    throw new JWTInvalid("Invalid JWT");
  if (!payload)
    throw new JWTInvalid("JWTs must contain a payload");
  let decoded;
  try {
    decoded = decode2(payload);
  } catch {
    throw new JWTInvalid("Failed to base64url decode the payload");
  }
  let result;
  try {
    result = JSON.parse(decoder.decode(decoded));
  } catch {
    throw new JWTInvalid("Failed to parse the decoded payload as JSON");
  }
  if (!isObject(result))
    throw new JWTInvalid("Invalid JWT Claims Set");
  return result;
}
var init_decode_jwt = __esm({
  "node_modules/jose/dist/browser/util/decode_jwt.js"() {
    init_base64url2();
    init_buffer_utils();
    init_is_object();
    init_errors();
  }
});

// node_modules/jose/dist/browser/runtime/generate.js
async function generateSecret(alg, options) {
  let length;
  let algorithm;
  let keyUsages;
  switch (alg) {
    case "HS256":
    case "HS384":
    case "HS512":
      length = parseInt(alg.slice(-3), 10);
      algorithm = { name: "HMAC", hash: `SHA-${length}`, length };
      keyUsages = ["sign", "verify"];
      break;
    case "A128CBC-HS256":
    case "A192CBC-HS384":
    case "A256CBC-HS512":
      length = parseInt(alg.slice(-3), 10);
      return random_default(new Uint8Array(length >> 3));
    case "A128KW":
    case "A192KW":
    case "A256KW":
      length = parseInt(alg.slice(1, 4), 10);
      algorithm = { name: "AES-KW", length };
      keyUsages = ["wrapKey", "unwrapKey"];
      break;
    case "A128GCMKW":
    case "A192GCMKW":
    case "A256GCMKW":
    case "A128GCM":
    case "A192GCM":
    case "A256GCM":
      length = parseInt(alg.slice(1, 4), 10);
      algorithm = { name: "AES-GCM", length };
      keyUsages = ["encrypt", "decrypt"];
      break;
    default:
      throw new JOSENotSupported('Invalid or unsupported JWK "alg" (Algorithm) Parameter value');
  }
  return webcrypto_default.subtle.generateKey(algorithm, options?.extractable ?? false, keyUsages);
}
function getModulusLengthOption(options) {
  const modulusLength = options?.modulusLength ?? 2048;
  if (typeof modulusLength !== "number" || modulusLength < 2048) {
    throw new JOSENotSupported("Invalid or unsupported modulusLength option provided, 2048 bits or larger keys must be used");
  }
  return modulusLength;
}
async function generateKeyPair(alg, options) {
  let algorithm;
  let keyUsages;
  switch (alg) {
    case "PS256":
    case "PS384":
    case "PS512":
      algorithm = {
        name: "RSA-PSS",
        hash: `SHA-${alg.slice(-3)}`,
        publicExponent: new Uint8Array([1, 0, 1]),
        modulusLength: getModulusLengthOption(options)
      };
      keyUsages = ["sign", "verify"];
      break;
    case "RS256":
    case "RS384":
    case "RS512":
      algorithm = {
        name: "RSASSA-PKCS1-v1_5",
        hash: `SHA-${alg.slice(-3)}`,
        publicExponent: new Uint8Array([1, 0, 1]),
        modulusLength: getModulusLengthOption(options)
      };
      keyUsages = ["sign", "verify"];
      break;
    case "RSA-OAEP":
    case "RSA-OAEP-256":
    case "RSA-OAEP-384":
    case "RSA-OAEP-512":
      algorithm = {
        name: "RSA-OAEP",
        hash: `SHA-${parseInt(alg.slice(-3), 10) || 1}`,
        publicExponent: new Uint8Array([1, 0, 1]),
        modulusLength: getModulusLengthOption(options)
      };
      keyUsages = ["decrypt", "unwrapKey", "encrypt", "wrapKey"];
      break;
    case "ES256":
      algorithm = { name: "ECDSA", namedCurve: "P-256" };
      keyUsages = ["sign", "verify"];
      break;
    case "ES384":
      algorithm = { name: "ECDSA", namedCurve: "P-384" };
      keyUsages = ["sign", "verify"];
      break;
    case "ES512":
      algorithm = { name: "ECDSA", namedCurve: "P-521" };
      keyUsages = ["sign", "verify"];
      break;
    case "Ed25519":
      algorithm = { name: "Ed25519" };
      keyUsages = ["sign", "verify"];
      break;
    case "EdDSA": {
      keyUsages = ["sign", "verify"];
      const crv = options?.crv ?? "Ed25519";
      switch (crv) {
        case "Ed25519":
        case "Ed448":
          algorithm = { name: crv };
          break;
        default:
          throw new JOSENotSupported("Invalid or unsupported crv option provided");
      }
      break;
    }
    case "ECDH-ES":
    case "ECDH-ES+A128KW":
    case "ECDH-ES+A192KW":
    case "ECDH-ES+A256KW": {
      keyUsages = ["deriveKey", "deriveBits"];
      const crv = options?.crv ?? "P-256";
      switch (crv) {
        case "P-256":
        case "P-384":
        case "P-521": {
          algorithm = { name: "ECDH", namedCurve: crv };
          break;
        }
        case "X25519":
        case "X448":
          algorithm = { name: crv };
          break;
        default:
          throw new JOSENotSupported("Invalid or unsupported crv option provided, supported values are P-256, P-384, P-521, X25519, and X448");
      }
      break;
    }
    default:
      throw new JOSENotSupported('Invalid or unsupported JWK "alg" (Algorithm) Parameter value');
  }
  return webcrypto_default.subtle.generateKey(algorithm, options?.extractable ?? false, keyUsages);
}
var init_generate = __esm({
  "node_modules/jose/dist/browser/runtime/generate.js"() {
    init_webcrypto();
    init_errors();
    init_random();
  }
});

// node_modules/jose/dist/browser/key/generate_key_pair.js
async function generateKeyPair2(alg, options) {
  return generateKeyPair(alg, options);
}
var init_generate_key_pair = __esm({
  "node_modules/jose/dist/browser/key/generate_key_pair.js"() {
    init_generate();
  }
});

// node_modules/jose/dist/browser/key/generate_secret.js
async function generateSecret2(alg, options) {
  return generateSecret(alg, options);
}
var init_generate_secret = __esm({
  "node_modules/jose/dist/browser/key/generate_secret.js"() {
    init_generate();
  }
});

// node_modules/jose/dist/browser/runtime/runtime.js
var runtime_default;
var init_runtime = __esm({
  "node_modules/jose/dist/browser/runtime/runtime.js"() {
    runtime_default = "WebCryptoAPI";
  }
});

// node_modules/jose/dist/browser/util/runtime.js
var runtime_default2;
var init_runtime2 = __esm({
  "node_modules/jose/dist/browser/util/runtime.js"() {
    init_runtime();
    runtime_default2 = runtime_default;
  }
});

// node_modules/jose/dist/browser/index.js
var browser_exports = {};
__export(browser_exports, {
  CompactEncrypt: () => CompactEncrypt,
  CompactSign: () => CompactSign,
  EmbeddedJWK: () => EmbeddedJWK,
  EncryptJWT: () => EncryptJWT,
  FlattenedEncrypt: () => FlattenedEncrypt,
  FlattenedSign: () => FlattenedSign,
  GeneralEncrypt: () => GeneralEncrypt,
  GeneralSign: () => GeneralSign,
  SignJWT: () => SignJWT,
  UnsecuredJWT: () => UnsecuredJWT,
  base64url: () => base64url_exports2,
  calculateJwkThumbprint: () => calculateJwkThumbprint,
  calculateJwkThumbprintUri: () => calculateJwkThumbprintUri,
  compactDecrypt: () => compactDecrypt,
  compactVerify: () => compactVerify,
  createLocalJWKSet: () => createLocalJWKSet,
  createRemoteJWKSet: () => createRemoteJWKSet,
  cryptoRuntime: () => runtime_default2,
  decodeJwt: () => decodeJwt,
  decodeProtectedHeader: () => decodeProtectedHeader,
  errors: () => errors_exports,
  experimental_jwksCache: () => experimental_jwksCache,
  exportJWK: () => exportJWK,
  exportPKCS8: () => exportPKCS8,
  exportSPKI: () => exportSPKI,
  flattenedDecrypt: () => flattenedDecrypt,
  flattenedVerify: () => flattenedVerify,
  generalDecrypt: () => generalDecrypt,
  generalVerify: () => generalVerify,
  generateKeyPair: () => generateKeyPair2,
  generateSecret: () => generateSecret2,
  importJWK: () => importJWK,
  importPKCS8: () => importPKCS8,
  importSPKI: () => importSPKI,
  importX509: () => importX509,
  jwksCache: () => jwksCache,
  jwtDecrypt: () => jwtDecrypt,
  jwtVerify: () => jwtVerify
});
var init_browser = __esm({
  "node_modules/jose/dist/browser/index.js"() {
    init_decrypt3();
    init_decrypt2();
    init_decrypt4();
    init_encrypt3();
    init_verify3();
    init_verify2();
    init_verify4();
    init_verify5();
    init_decrypt5();
    init_encrypt4();
    init_encrypt2();
    init_sign3();
    init_sign2();
    init_sign4();
    init_sign5();
    init_encrypt5();
    init_thumbprint();
    init_embedded();
    init_local();
    init_remote();
    init_unsecured();
    init_export();
    init_import();
    init_decode_protected_header();
    init_decode_jwt();
    init_errors();
    init_generate_key_pair();
    init_generate_secret();
    init_base64url2();
    init_runtime2();
  }
});

// node_modules/uuid/dist/cjs-browser/max.js
var require_max = __commonJS({
  "node_modules/uuid/dist/cjs-browser/max.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.default = "ffffffff-ffff-ffff-ffff-ffffffffffff";
  }
});

// node_modules/uuid/dist/cjs-browser/nil.js
var require_nil = __commonJS({
  "node_modules/uuid/dist/cjs-browser/nil.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.default = "00000000-0000-0000-0000-000000000000";
  }
});

// node_modules/uuid/dist/cjs-browser/regex.js
var require_regex = __commonJS({
  "node_modules/uuid/dist/cjs-browser/regex.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.default = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$/i;
  }
});

// node_modules/uuid/dist/cjs-browser/validate.js
var require_validate = __commonJS({
  "node_modules/uuid/dist/cjs-browser/validate.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var regex_js_1 = require_regex();
    function validate(uuid) {
      return typeof uuid === "string" && regex_js_1.default.test(uuid);
    }
    exports.default = validate;
  }
});

// node_modules/uuid/dist/cjs-browser/parse.js
var require_parse = __commonJS({
  "node_modules/uuid/dist/cjs-browser/parse.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var validate_js_1 = require_validate();
    function parse2(uuid) {
      if (!(0, validate_js_1.default)(uuid)) {
        throw TypeError("Invalid UUID");
      }
      let v;
      return Uint8Array.of((v = parseInt(uuid.slice(0, 8), 16)) >>> 24, v >>> 16 & 255, v >>> 8 & 255, v & 255, (v = parseInt(uuid.slice(9, 13), 16)) >>> 8, v & 255, (v = parseInt(uuid.slice(14, 18), 16)) >>> 8, v & 255, (v = parseInt(uuid.slice(19, 23), 16)) >>> 8, v & 255, (v = parseInt(uuid.slice(24, 36), 16)) / 1099511627776 & 255, v / 4294967296 & 255, v >>> 24 & 255, v >>> 16 & 255, v >>> 8 & 255, v & 255);
    }
    exports.default = parse2;
  }
});

// node_modules/uuid/dist/cjs-browser/stringify.js
var require_stringify = __commonJS({
  "node_modules/uuid/dist/cjs-browser/stringify.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.unsafeStringify = void 0;
    var validate_js_1 = require_validate();
    var byteToHex = [];
    for (let i = 0; i < 256; ++i) {
      byteToHex.push((i + 256).toString(16).slice(1));
    }
    function unsafeStringify(arr, offset = 0) {
      return (byteToHex[arr[offset + 0]] + byteToHex[arr[offset + 1]] + byteToHex[arr[offset + 2]] + byteToHex[arr[offset + 3]] + "-" + byteToHex[arr[offset + 4]] + byteToHex[arr[offset + 5]] + "-" + byteToHex[arr[offset + 6]] + byteToHex[arr[offset + 7]] + "-" + byteToHex[arr[offset + 8]] + byteToHex[arr[offset + 9]] + "-" + byteToHex[arr[offset + 10]] + byteToHex[arr[offset + 11]] + byteToHex[arr[offset + 12]] + byteToHex[arr[offset + 13]] + byteToHex[arr[offset + 14]] + byteToHex[arr[offset + 15]]).toLowerCase();
    }
    exports.unsafeStringify = unsafeStringify;
    function stringify(arr, offset = 0) {
      const uuid = unsafeStringify(arr, offset);
      if (!(0, validate_js_1.default)(uuid)) {
        throw TypeError("Stringified UUID is invalid");
      }
      return uuid;
    }
    exports.default = stringify;
  }
});

// node_modules/uuid/dist/cjs-browser/rng.js
var require_rng = __commonJS({
  "node_modules/uuid/dist/cjs-browser/rng.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var getRandomValues;
    var rnds8 = new Uint8Array(16);
    function rng() {
      if (!getRandomValues) {
        if (typeof crypto === "undefined" || !crypto.getRandomValues) {
          throw new Error("crypto.getRandomValues() not supported. See https://github.com/uuidjs/uuid#getrandomvalues-not-supported");
        }
        getRandomValues = crypto.getRandomValues.bind(crypto);
      }
      return getRandomValues(rnds8);
    }
    exports.default = rng;
  }
});

// node_modules/uuid/dist/cjs-browser/v1.js
var require_v1 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v1.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.updateV1State = void 0;
    var rng_js_1 = require_rng();
    var stringify_js_1 = require_stringify();
    var _state = {};
    function v1(options, buf, offset) {
      let bytes;
      const isV6 = options?._v6 ?? false;
      if (options) {
        const optionsKeys = Object.keys(options);
        if (optionsKeys.length === 1 && optionsKeys[0] === "_v6") {
          options = void 0;
        }
      }
      if (options) {
        bytes = v1Bytes(options.random ?? options.rng?.() ?? (0, rng_js_1.default)(), options.msecs, options.nsecs, options.clockseq, options.node, buf, offset);
      } else {
        const now = Date.now();
        const rnds = (0, rng_js_1.default)();
        updateV1State(_state, now, rnds);
        bytes = v1Bytes(rnds, _state.msecs, _state.nsecs, isV6 ? void 0 : _state.clockseq, isV6 ? void 0 : _state.node, buf, offset);
      }
      return buf ?? (0, stringify_js_1.unsafeStringify)(bytes);
    }
    function updateV1State(state, now, rnds) {
      state.msecs ??= -Infinity;
      state.nsecs ??= 0;
      if (now === state.msecs) {
        state.nsecs++;
        if (state.nsecs >= 1e4) {
          state.node = void 0;
          state.nsecs = 0;
        }
      } else if (now > state.msecs) {
        state.nsecs = 0;
      } else if (now < state.msecs) {
        state.node = void 0;
      }
      if (!state.node) {
        state.node = rnds.slice(10, 16);
        state.node[0] |= 1;
        state.clockseq = (rnds[8] << 8 | rnds[9]) & 16383;
      }
      state.msecs = now;
      return state;
    }
    exports.updateV1State = updateV1State;
    function v1Bytes(rnds, msecs, nsecs, clockseq, node, buf, offset = 0) {
      if (rnds.length < 16) {
        throw new Error("Random bytes length must be >= 16");
      }
      if (!buf) {
        buf = new Uint8Array(16);
        offset = 0;
      } else {
        if (offset < 0 || offset + 16 > buf.length) {
          throw new RangeError(`UUID byte range ${offset}:${offset + 15} is out of buffer bounds`);
        }
      }
      msecs ??= Date.now();
      nsecs ??= 0;
      clockseq ??= (rnds[8] << 8 | rnds[9]) & 16383;
      node ??= rnds.slice(10, 16);
      msecs += 122192928e5;
      const tl = ((msecs & 268435455) * 1e4 + nsecs) % 4294967296;
      buf[offset++] = tl >>> 24 & 255;
      buf[offset++] = tl >>> 16 & 255;
      buf[offset++] = tl >>> 8 & 255;
      buf[offset++] = tl & 255;
      const tmh = msecs / 4294967296 * 1e4 & 268435455;
      buf[offset++] = tmh >>> 8 & 255;
      buf[offset++] = tmh & 255;
      buf[offset++] = tmh >>> 24 & 15 | 16;
      buf[offset++] = tmh >>> 16 & 255;
      buf[offset++] = clockseq >>> 8 | 128;
      buf[offset++] = clockseq & 255;
      for (let n = 0; n < 6; ++n) {
        buf[offset++] = node[n];
      }
      return buf;
    }
    exports.default = v1;
  }
});

// node_modules/uuid/dist/cjs-browser/v1ToV6.js
var require_v1ToV6 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v1ToV6.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var parse_js_1 = require_parse();
    var stringify_js_1 = require_stringify();
    function v1ToV6(uuid) {
      const v1Bytes = typeof uuid === "string" ? (0, parse_js_1.default)(uuid) : uuid;
      const v6Bytes = _v1ToV6(v1Bytes);
      return typeof uuid === "string" ? (0, stringify_js_1.unsafeStringify)(v6Bytes) : v6Bytes;
    }
    exports.default = v1ToV6;
    function _v1ToV6(v1Bytes) {
      return Uint8Array.of((v1Bytes[6] & 15) << 4 | v1Bytes[7] >> 4 & 15, (v1Bytes[7] & 15) << 4 | (v1Bytes[4] & 240) >> 4, (v1Bytes[4] & 15) << 4 | (v1Bytes[5] & 240) >> 4, (v1Bytes[5] & 15) << 4 | (v1Bytes[0] & 240) >> 4, (v1Bytes[0] & 15) << 4 | (v1Bytes[1] & 240) >> 4, (v1Bytes[1] & 15) << 4 | (v1Bytes[2] & 240) >> 4, 96 | v1Bytes[2] & 15, v1Bytes[3], v1Bytes[8], v1Bytes[9], v1Bytes[10], v1Bytes[11], v1Bytes[12], v1Bytes[13], v1Bytes[14], v1Bytes[15]);
    }
  }
});

// node_modules/uuid/dist/cjs-browser/md5.js
var require_md5 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/md5.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    function md5(bytes) {
      const words = uint8ToUint32(bytes);
      const md5Bytes = wordsToMd5(words, bytes.length * 8);
      return uint32ToUint8(md5Bytes);
    }
    function uint32ToUint8(input) {
      const bytes = new Uint8Array(input.length * 4);
      for (let i = 0; i < input.length * 4; i++) {
        bytes[i] = input[i >> 2] >>> i % 4 * 8 & 255;
      }
      return bytes;
    }
    function getOutputLength(inputLength8) {
      return (inputLength8 + 64 >>> 9 << 4) + 14 + 1;
    }
    function wordsToMd5(x, len) {
      const xpad = new Uint32Array(getOutputLength(len)).fill(0);
      xpad.set(x);
      xpad[len >> 5] |= 128 << len % 32;
      xpad[xpad.length - 1] = len;
      x = xpad;
      let a = 1732584193;
      let b = -271733879;
      let c = -1732584194;
      let d = 271733878;
      for (let i = 0; i < x.length; i += 16) {
        const olda = a;
        const oldb = b;
        const oldc = c;
        const oldd = d;
        a = md5ff(a, b, c, d, x[i], 7, -680876936);
        d = md5ff(d, a, b, c, x[i + 1], 12, -389564586);
        c = md5ff(c, d, a, b, x[i + 2], 17, 606105819);
        b = md5ff(b, c, d, a, x[i + 3], 22, -1044525330);
        a = md5ff(a, b, c, d, x[i + 4], 7, -176418897);
        d = md5ff(d, a, b, c, x[i + 5], 12, 1200080426);
        c = md5ff(c, d, a, b, x[i + 6], 17, -1473231341);
        b = md5ff(b, c, d, a, x[i + 7], 22, -45705983);
        a = md5ff(a, b, c, d, x[i + 8], 7, 1770035416);
        d = md5ff(d, a, b, c, x[i + 9], 12, -1958414417);
        c = md5ff(c, d, a, b, x[i + 10], 17, -42063);
        b = md5ff(b, c, d, a, x[i + 11], 22, -1990404162);
        a = md5ff(a, b, c, d, x[i + 12], 7, 1804603682);
        d = md5ff(d, a, b, c, x[i + 13], 12, -40341101);
        c = md5ff(c, d, a, b, x[i + 14], 17, -1502002290);
        b = md5ff(b, c, d, a, x[i + 15], 22, 1236535329);
        a = md5gg(a, b, c, d, x[i + 1], 5, -165796510);
        d = md5gg(d, a, b, c, x[i + 6], 9, -1069501632);
        c = md5gg(c, d, a, b, x[i + 11], 14, 643717713);
        b = md5gg(b, c, d, a, x[i], 20, -373897302);
        a = md5gg(a, b, c, d, x[i + 5], 5, -701558691);
        d = md5gg(d, a, b, c, x[i + 10], 9, 38016083);
        c = md5gg(c, d, a, b, x[i + 15], 14, -660478335);
        b = md5gg(b, c, d, a, x[i + 4], 20, -405537848);
        a = md5gg(a, b, c, d, x[i + 9], 5, 568446438);
        d = md5gg(d, a, b, c, x[i + 14], 9, -1019803690);
        c = md5gg(c, d, a, b, x[i + 3], 14, -187363961);
        b = md5gg(b, c, d, a, x[i + 8], 20, 1163531501);
        a = md5gg(a, b, c, d, x[i + 13], 5, -1444681467);
        d = md5gg(d, a, b, c, x[i + 2], 9, -51403784);
        c = md5gg(c, d, a, b, x[i + 7], 14, 1735328473);
        b = md5gg(b, c, d, a, x[i + 12], 20, -1926607734);
        a = md5hh(a, b, c, d, x[i + 5], 4, -378558);
        d = md5hh(d, a, b, c, x[i + 8], 11, -2022574463);
        c = md5hh(c, d, a, b, x[i + 11], 16, 1839030562);
        b = md5hh(b, c, d, a, x[i + 14], 23, -35309556);
        a = md5hh(a, b, c, d, x[i + 1], 4, -1530992060);
        d = md5hh(d, a, b, c, x[i + 4], 11, 1272893353);
        c = md5hh(c, d, a, b, x[i + 7], 16, -155497632);
        b = md5hh(b, c, d, a, x[i + 10], 23, -1094730640);
        a = md5hh(a, b, c, d, x[i + 13], 4, 681279174);
        d = md5hh(d, a, b, c, x[i], 11, -358537222);
        c = md5hh(c, d, a, b, x[i + 3], 16, -722521979);
        b = md5hh(b, c, d, a, x[i + 6], 23, 76029189);
        a = md5hh(a, b, c, d, x[i + 9], 4, -640364487);
        d = md5hh(d, a, b, c, x[i + 12], 11, -421815835);
        c = md5hh(c, d, a, b, x[i + 15], 16, 530742520);
        b = md5hh(b, c, d, a, x[i + 2], 23, -995338651);
        a = md5ii(a, b, c, d, x[i], 6, -198630844);
        d = md5ii(d, a, b, c, x[i + 7], 10, 1126891415);
        c = md5ii(c, d, a, b, x[i + 14], 15, -1416354905);
        b = md5ii(b, c, d, a, x[i + 5], 21, -57434055);
        a = md5ii(a, b, c, d, x[i + 12], 6, 1700485571);
        d = md5ii(d, a, b, c, x[i + 3], 10, -1894986606);
        c = md5ii(c, d, a, b, x[i + 10], 15, -1051523);
        b = md5ii(b, c, d, a, x[i + 1], 21, -2054922799);
        a = md5ii(a, b, c, d, x[i + 8], 6, 1873313359);
        d = md5ii(d, a, b, c, x[i + 15], 10, -30611744);
        c = md5ii(c, d, a, b, x[i + 6], 15, -1560198380);
        b = md5ii(b, c, d, a, x[i + 13], 21, 1309151649);
        a = md5ii(a, b, c, d, x[i + 4], 6, -145523070);
        d = md5ii(d, a, b, c, x[i + 11], 10, -1120210379);
        c = md5ii(c, d, a, b, x[i + 2], 15, 718787259);
        b = md5ii(b, c, d, a, x[i + 9], 21, -343485551);
        a = safeAdd(a, olda);
        b = safeAdd(b, oldb);
        c = safeAdd(c, oldc);
        d = safeAdd(d, oldd);
      }
      return Uint32Array.of(a, b, c, d);
    }
    function uint8ToUint32(input) {
      if (input.length === 0) {
        return new Uint32Array();
      }
      const output = new Uint32Array(getOutputLength(input.length * 8)).fill(0);
      for (let i = 0; i < input.length; i++) {
        output[i >> 2] |= (input[i] & 255) << i % 4 * 8;
      }
      return output;
    }
    function safeAdd(x, y) {
      const lsw = (x & 65535) + (y & 65535);
      const msw = (x >> 16) + (y >> 16) + (lsw >> 16);
      return msw << 16 | lsw & 65535;
    }
    function bitRotateLeft(num, cnt) {
      return num << cnt | num >>> 32 - cnt;
    }
    function md5cmn(q, a, b, x, s, t) {
      return safeAdd(bitRotateLeft(safeAdd(safeAdd(a, q), safeAdd(x, t)), s), b);
    }
    function md5ff(a, b, c, d, x, s, t) {
      return md5cmn(b & c | ~b & d, a, b, x, s, t);
    }
    function md5gg(a, b, c, d, x, s, t) {
      return md5cmn(b & d | c & ~d, a, b, x, s, t);
    }
    function md5hh(a, b, c, d, x, s, t) {
      return md5cmn(b ^ c ^ d, a, b, x, s, t);
    }
    function md5ii(a, b, c, d, x, s, t) {
      return md5cmn(c ^ (b | ~d), a, b, x, s, t);
    }
    exports.default = md5;
  }
});

// node_modules/uuid/dist/cjs-browser/v35.js
var require_v35 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v35.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.URL = exports.DNS = exports.stringToBytes = void 0;
    var parse_js_1 = require_parse();
    var stringify_js_1 = require_stringify();
    function stringToBytes(str) {
      str = unescape(encodeURIComponent(str));
      const bytes = new Uint8Array(str.length);
      for (let i = 0; i < str.length; ++i) {
        bytes[i] = str.charCodeAt(i);
      }
      return bytes;
    }
    exports.stringToBytes = stringToBytes;
    exports.DNS = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
    exports.URL = "6ba7b811-9dad-11d1-80b4-00c04fd430c8";
    function v35(version, hash, value, namespace, buf, offset) {
      const valueBytes = typeof value === "string" ? stringToBytes(value) : value;
      const namespaceBytes = typeof namespace === "string" ? (0, parse_js_1.default)(namespace) : namespace;
      if (typeof namespace === "string") {
        namespace = (0, parse_js_1.default)(namespace);
      }
      if (namespace?.length !== 16) {
        throw TypeError("Namespace must be array-like (16 iterable integer values, 0-255)");
      }
      let bytes = new Uint8Array(16 + valueBytes.length);
      bytes.set(namespaceBytes);
      bytes.set(valueBytes, namespaceBytes.length);
      bytes = hash(bytes);
      bytes[6] = bytes[6] & 15 | version;
      bytes[8] = bytes[8] & 63 | 128;
      if (buf) {
        offset = offset || 0;
        for (let i = 0; i < 16; ++i) {
          buf[offset + i] = bytes[i];
        }
        return buf;
      }
      return (0, stringify_js_1.unsafeStringify)(bytes);
    }
    exports.default = v35;
  }
});

// node_modules/uuid/dist/cjs-browser/v3.js
var require_v3 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v3.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.URL = exports.DNS = void 0;
    var md5_js_1 = require_md5();
    var v35_js_1 = require_v35();
    var v35_js_2 = require_v35();
    Object.defineProperty(exports, "DNS", { enumerable: true, get: function() {
      return v35_js_2.DNS;
    } });
    Object.defineProperty(exports, "URL", { enumerable: true, get: function() {
      return v35_js_2.URL;
    } });
    function v3(value, namespace, buf, offset) {
      return (0, v35_js_1.default)(48, md5_js_1.default, value, namespace, buf, offset);
    }
    v3.DNS = v35_js_1.DNS;
    v3.URL = v35_js_1.URL;
    exports.default = v3;
  }
});

// node_modules/uuid/dist/cjs-browser/native.js
var require_native = __commonJS({
  "node_modules/uuid/dist/cjs-browser/native.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var randomUUID = typeof crypto !== "undefined" && crypto.randomUUID && crypto.randomUUID.bind(crypto);
    exports.default = { randomUUID };
  }
});

// node_modules/uuid/dist/cjs-browser/v4.js
var require_v4 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v4.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var native_js_1 = require_native();
    var rng_js_1 = require_rng();
    var stringify_js_1 = require_stringify();
    function v4(options, buf, offset) {
      if (native_js_1.default.randomUUID && !buf && !options) {
        return native_js_1.default.randomUUID();
      }
      options = options || {};
      const rnds = options.random ?? options.rng?.() ?? (0, rng_js_1.default)();
      if (rnds.length < 16) {
        throw new Error("Random bytes length must be >= 16");
      }
      rnds[6] = rnds[6] & 15 | 64;
      rnds[8] = rnds[8] & 63 | 128;
      if (buf) {
        offset = offset || 0;
        if (offset < 0 || offset + 16 > buf.length) {
          throw new RangeError(`UUID byte range ${offset}:${offset + 15} is out of buffer bounds`);
        }
        for (let i = 0; i < 16; ++i) {
          buf[offset + i] = rnds[i];
        }
        return buf;
      }
      return (0, stringify_js_1.unsafeStringify)(rnds);
    }
    exports.default = v4;
  }
});

// node_modules/uuid/dist/cjs-browser/sha1.js
var require_sha1 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/sha1.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    function f(s, x, y, z) {
      switch (s) {
        case 0:
          return x & y ^ ~x & z;
        case 1:
          return x ^ y ^ z;
        case 2:
          return x & y ^ x & z ^ y & z;
        case 3:
          return x ^ y ^ z;
      }
    }
    function ROTL(x, n) {
      return x << n | x >>> 32 - n;
    }
    function sha1(bytes) {
      const K = [1518500249, 1859775393, 2400959708, 3395469782];
      const H = [1732584193, 4023233417, 2562383102, 271733878, 3285377520];
      const newBytes = new Uint8Array(bytes.length + 1);
      newBytes.set(bytes);
      newBytes[bytes.length] = 128;
      bytes = newBytes;
      const l = bytes.length / 4 + 2;
      const N = Math.ceil(l / 16);
      const M = new Array(N);
      for (let i = 0; i < N; ++i) {
        const arr = new Uint32Array(16);
        for (let j = 0; j < 16; ++j) {
          arr[j] = bytes[i * 64 + j * 4] << 24 | bytes[i * 64 + j * 4 + 1] << 16 | bytes[i * 64 + j * 4 + 2] << 8 | bytes[i * 64 + j * 4 + 3];
        }
        M[i] = arr;
      }
      M[N - 1][14] = (bytes.length - 1) * 8 / Math.pow(2, 32);
      M[N - 1][14] = Math.floor(M[N - 1][14]);
      M[N - 1][15] = (bytes.length - 1) * 8 & 4294967295;
      for (let i = 0; i < N; ++i) {
        const W = new Uint32Array(80);
        for (let t = 0; t < 16; ++t) {
          W[t] = M[i][t];
        }
        for (let t = 16; t < 80; ++t) {
          W[t] = ROTL(W[t - 3] ^ W[t - 8] ^ W[t - 14] ^ W[t - 16], 1);
        }
        let a = H[0];
        let b = H[1];
        let c = H[2];
        let d = H[3];
        let e = H[4];
        for (let t = 0; t < 80; ++t) {
          const s = Math.floor(t / 20);
          const T = ROTL(a, 5) + f(s, b, c, d) + e + K[s] + W[t] >>> 0;
          e = d;
          d = c;
          c = ROTL(b, 30) >>> 0;
          b = a;
          a = T;
        }
        H[0] = H[0] + a >>> 0;
        H[1] = H[1] + b >>> 0;
        H[2] = H[2] + c >>> 0;
        H[3] = H[3] + d >>> 0;
        H[4] = H[4] + e >>> 0;
      }
      return Uint8Array.of(H[0] >> 24, H[0] >> 16, H[0] >> 8, H[0], H[1] >> 24, H[1] >> 16, H[1] >> 8, H[1], H[2] >> 24, H[2] >> 16, H[2] >> 8, H[2], H[3] >> 24, H[3] >> 16, H[3] >> 8, H[3], H[4] >> 24, H[4] >> 16, H[4] >> 8, H[4]);
    }
    exports.default = sha1;
  }
});

// node_modules/uuid/dist/cjs-browser/v5.js
var require_v5 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v5.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.URL = exports.DNS = void 0;
    var sha1_js_1 = require_sha1();
    var v35_js_1 = require_v35();
    var v35_js_2 = require_v35();
    Object.defineProperty(exports, "DNS", { enumerable: true, get: function() {
      return v35_js_2.DNS;
    } });
    Object.defineProperty(exports, "URL", { enumerable: true, get: function() {
      return v35_js_2.URL;
    } });
    function v5(value, namespace, buf, offset) {
      return (0, v35_js_1.default)(80, sha1_js_1.default, value, namespace, buf, offset);
    }
    v5.DNS = v35_js_1.DNS;
    v5.URL = v35_js_1.URL;
    exports.default = v5;
  }
});

// node_modules/uuid/dist/cjs-browser/v6.js
var require_v6 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v6.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var stringify_js_1 = require_stringify();
    var v1_js_1 = require_v1();
    var v1ToV6_js_1 = require_v1ToV6();
    function v6(options, buf, offset) {
      options ??= {};
      offset ??= 0;
      let bytes = (0, v1_js_1.default)({ ...options, _v6: true }, new Uint8Array(16));
      bytes = (0, v1ToV6_js_1.default)(bytes);
      if (buf) {
        for (let i = 0; i < 16; i++) {
          buf[offset + i] = bytes[i];
        }
        return buf;
      }
      return (0, stringify_js_1.unsafeStringify)(bytes);
    }
    exports.default = v6;
  }
});

// node_modules/uuid/dist/cjs-browser/v6ToV1.js
var require_v6ToV1 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v6ToV1.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var parse_js_1 = require_parse();
    var stringify_js_1 = require_stringify();
    function v6ToV1(uuid) {
      const v6Bytes = typeof uuid === "string" ? (0, parse_js_1.default)(uuid) : uuid;
      const v1Bytes = _v6ToV1(v6Bytes);
      return typeof uuid === "string" ? (0, stringify_js_1.unsafeStringify)(v1Bytes) : v1Bytes;
    }
    exports.default = v6ToV1;
    function _v6ToV1(v6Bytes) {
      return Uint8Array.of((v6Bytes[3] & 15) << 4 | v6Bytes[4] >> 4 & 15, (v6Bytes[4] & 15) << 4 | (v6Bytes[5] & 240) >> 4, (v6Bytes[5] & 15) << 4 | v6Bytes[6] & 15, v6Bytes[7], (v6Bytes[1] & 15) << 4 | (v6Bytes[2] & 240) >> 4, (v6Bytes[2] & 15) << 4 | (v6Bytes[3] & 240) >> 4, 16 | (v6Bytes[0] & 240) >> 4, (v6Bytes[0] & 15) << 4 | (v6Bytes[1] & 240) >> 4, v6Bytes[8], v6Bytes[9], v6Bytes[10], v6Bytes[11], v6Bytes[12], v6Bytes[13], v6Bytes[14], v6Bytes[15]);
    }
  }
});

// node_modules/uuid/dist/cjs-browser/v7.js
var require_v7 = __commonJS({
  "node_modules/uuid/dist/cjs-browser/v7.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.updateV7State = void 0;
    var rng_js_1 = require_rng();
    var stringify_js_1 = require_stringify();
    var _state = {};
    function v7(options, buf, offset) {
      let bytes;
      if (options) {
        bytes = v7Bytes(options.random ?? options.rng?.() ?? (0, rng_js_1.default)(), options.msecs, options.seq, buf, offset);
      } else {
        const now = Date.now();
        const rnds = (0, rng_js_1.default)();
        updateV7State(_state, now, rnds);
        bytes = v7Bytes(rnds, _state.msecs, _state.seq, buf, offset);
      }
      return buf ?? (0, stringify_js_1.unsafeStringify)(bytes);
    }
    function updateV7State(state, now, rnds) {
      state.msecs ??= -Infinity;
      state.seq ??= 0;
      if (now > state.msecs) {
        state.seq = rnds[6] << 23 | rnds[7] << 16 | rnds[8] << 8 | rnds[9];
        state.msecs = now;
      } else {
        state.seq = state.seq + 1 | 0;
        if (state.seq === 0) {
          state.msecs++;
        }
      }
      return state;
    }
    exports.updateV7State = updateV7State;
    function v7Bytes(rnds, msecs, seq, buf, offset = 0) {
      if (rnds.length < 16) {
        throw new Error("Random bytes length must be >= 16");
      }
      if (!buf) {
        buf = new Uint8Array(16);
        offset = 0;
      } else {
        if (offset < 0 || offset + 16 > buf.length) {
          throw new RangeError(`UUID byte range ${offset}:${offset + 15} is out of buffer bounds`);
        }
      }
      msecs ??= Date.now();
      seq ??= rnds[6] * 127 << 24 | rnds[7] << 16 | rnds[8] << 8 | rnds[9];
      buf[offset++] = msecs / 1099511627776 & 255;
      buf[offset++] = msecs / 4294967296 & 255;
      buf[offset++] = msecs / 16777216 & 255;
      buf[offset++] = msecs / 65536 & 255;
      buf[offset++] = msecs / 256 & 255;
      buf[offset++] = msecs & 255;
      buf[offset++] = 112 | seq >>> 28 & 15;
      buf[offset++] = seq >>> 20 & 255;
      buf[offset++] = 128 | seq >>> 14 & 63;
      buf[offset++] = seq >>> 6 & 255;
      buf[offset++] = seq << 2 & 255 | rnds[10] & 3;
      buf[offset++] = rnds[11];
      buf[offset++] = rnds[12];
      buf[offset++] = rnds[13];
      buf[offset++] = rnds[14];
      buf[offset++] = rnds[15];
      return buf;
    }
    exports.default = v7;
  }
});

// node_modules/uuid/dist/cjs-browser/version.js
var require_version = __commonJS({
  "node_modules/uuid/dist/cjs-browser/version.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    var validate_js_1 = require_validate();
    function version(uuid) {
      if (!(0, validate_js_1.default)(uuid)) {
        throw TypeError("Invalid UUID");
      }
      return parseInt(uuid.slice(14, 15), 16);
    }
    exports.default = version;
  }
});

// node_modules/uuid/dist/cjs-browser/index.js
var require_cjs_browser = __commonJS({
  "node_modules/uuid/dist/cjs-browser/index.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.version = exports.validate = exports.v7 = exports.v6ToV1 = exports.v6 = exports.v5 = exports.v4 = exports.v3 = exports.v1ToV6 = exports.v1 = exports.stringify = exports.parse = exports.NIL = exports.MAX = void 0;
    var max_js_1 = require_max();
    Object.defineProperty(exports, "MAX", { enumerable: true, get: function() {
      return max_js_1.default;
    } });
    var nil_js_1 = require_nil();
    Object.defineProperty(exports, "NIL", { enumerable: true, get: function() {
      return nil_js_1.default;
    } });
    var parse_js_1 = require_parse();
    Object.defineProperty(exports, "parse", { enumerable: true, get: function() {
      return parse_js_1.default;
    } });
    var stringify_js_1 = require_stringify();
    Object.defineProperty(exports, "stringify", { enumerable: true, get: function() {
      return stringify_js_1.default;
    } });
    var v1_js_1 = require_v1();
    Object.defineProperty(exports, "v1", { enumerable: true, get: function() {
      return v1_js_1.default;
    } });
    var v1ToV6_js_1 = require_v1ToV6();
    Object.defineProperty(exports, "v1ToV6", { enumerable: true, get: function() {
      return v1ToV6_js_1.default;
    } });
    var v3_js_1 = require_v3();
    Object.defineProperty(exports, "v3", { enumerable: true, get: function() {
      return v3_js_1.default;
    } });
    var v4_js_1 = require_v4();
    Object.defineProperty(exports, "v4", { enumerable: true, get: function() {
      return v4_js_1.default;
    } });
    var v5_js_1 = require_v5();
    Object.defineProperty(exports, "v5", { enumerable: true, get: function() {
      return v5_js_1.default;
    } });
    var v6_js_1 = require_v6();
    Object.defineProperty(exports, "v6", { enumerable: true, get: function() {
      return v6_js_1.default;
    } });
    var v6ToV1_js_1 = require_v6ToV1();
    Object.defineProperty(exports, "v6ToV1", { enumerable: true, get: function() {
      return v6ToV1_js_1.default;
    } });
    var v7_js_1 = require_v7();
    Object.defineProperty(exports, "v7", { enumerable: true, get: function() {
      return v7_js_1.default;
    } });
    var validate_js_1 = require_validate();
    Object.defineProperty(exports, "validate", { enumerable: true, get: function() {
      return validate_js_1.default;
    } });
    var version_js_1 = require_version();
    Object.defineProperty(exports, "version", { enumerable: true, get: function() {
      return version_js_1.default;
    } });
  }
});

// node_modules/@inrupt/solid-client-authn-core/dist/index.js
var require_dist = __commonJS({
  "node_modules/@inrupt/solid-client-authn-core/dist/index.js"(exports) {
    "use strict";
    var jose = (init_browser(), __toCommonJS(browser_exports));
    var uuid = require_cjs_browser();
    var SOLID_CLIENT_AUTHN_KEY_PREFIX = "solidClientAuthn:";
    var PREFERRED_SIGNING_ALG = ["ES256", "RS256"];
    var EVENTS = {
      // Note that an `error` events MUST be listened to: https://nodejs.org/dist/latest-v16.x/docs/api/events.html#error-events.
      ERROR: "error",
      LOGIN: "login",
      LOGOUT: "logout",
      NEW_REFRESH_TOKEN: "newRefreshToken",
      NEW_TOKENS: "newTokens",
      AUTHORIZATION_REQUEST: "authorizationRequest",
      SESSION_EXPIRED: "sessionExpired",
      SESSION_EXTENDED: "sessionExtended",
      SESSION_RESTORED: "sessionRestore",
      TIMEOUT_SET: "timeoutSet"
    };
    var REFRESH_BEFORE_EXPIRATION_SECONDS = 5;
    var SCOPE_OPENID = "openid";
    var SCOPE_OFFLINE = "offline_access";
    var SCOPE_WEBID = "webid";
    var DEFAULT_SCOPES = [SCOPE_OPENID, SCOPE_OFFLINE, SCOPE_WEBID];
    var AggregateHandler = class {
      handleables;
      constructor(handleables) {
        this.handleables = handleables;
        this.handleables = handleables;
      }
      /**
       * Helper function that will asynchronously determine the proper handler to use. If multiple
       * handlers can handle, it will choose the first one in the list
       * @param params Paramerters to feed to the handler
       */
      async getProperHandler(params) {
        const canHandleList = await Promise.all(this.handleables.map((handleable) => handleable.canHandle(...params)));
        for (let i = 0; i < canHandleList.length; i += 1) {
          if (canHandleList[i]) {
            return this.handleables[i];
          }
        }
        return null;
      }
      async canHandle(...params) {
        return await this.getProperHandler(params) !== null;
      }
      async handle(...params) {
        const handler = await this.getProperHandler(params);
        if (handler) {
          return handler.handle(...params);
        }
        throw new Error(`[${this.constructor.name}] cannot find a suitable handler for: ${params.map((param) => {
          try {
            return JSON.stringify(param);
          } catch (_err) {
            return param.toString();
          }
        }).join(", ")}`);
      }
    };
    async function getWebidFromTokenPayload(idToken, jwksIri, issuerIri, clientId) {
      let payload;
      let clientIdInPayload;
      try {
        const { payload: verifiedPayload } = await jose.jwtVerify(idToken, jose.createRemoteJWKSet(new URL(jwksIri)), {
          issuer: issuerIri,
          audience: clientId
        });
        payload = verifiedPayload;
      } catch (e) {
        throw new Error(`Token verification failed: ${e.stack}`);
      }
      if (typeof payload.azp === "string") {
        clientIdInPayload = payload.azp;
      }
      if (typeof payload.webid === "string") {
        return {
          webId: payload.webid,
          clientId: clientIdInPayload
        };
      }
      if (typeof payload.sub !== "string") {
        throw new Error(`The token ${JSON.stringify(payload)} is invalid: it has no 'webid' claim and no 'sub' claim.`);
      }
      try {
        new URL(payload.sub);
        return {
          webId: payload.sub,
          clientId: clientIdInPayload
        };
      } catch (e) {
        throw new Error(`The token has no 'webid' claim, and its 'sub' claim of [${payload.sub}] is invalid as a URL - error [${e}].`);
      }
    }
    function normalizeScopes(scopes) {
      if (!Array.isArray(scopes)) {
        return DEFAULT_SCOPES;
      }
      return Array.from(
        // De-dupe potentia conflicts if any.
        /* @__PURE__ */ new Set([
          ...DEFAULT_SCOPES,
          ...scopes.filter(
            // Remove user-provided scopes that are not strings or include spaces.
            (scope) => typeof scope === "string" && !scope.includes(" ")
          )
        ])
      );
    }
    function isValidRedirectUrl(redirectUrl) {
      try {
        const urlObject = new URL(redirectUrl);
        const noReservedQuery = !urlObject.searchParams.has("code") && !urlObject.searchParams.has("state");
        const noHash = urlObject.hash === "";
        return noReservedQuery && noHash;
      } catch (_e) {
        return false;
      }
    }
    function removeOpenIdParams(redirectUrl) {
      const cleanedUpUrl = new URL(redirectUrl);
      cleanedUpUrl.searchParams.delete("state");
      cleanedUpUrl.searchParams.delete("code");
      cleanedUpUrl.searchParams.delete("error");
      cleanedUpUrl.searchParams.delete("error_description");
      cleanedUpUrl.searchParams.delete("iss");
      return cleanedUpUrl;
    }
    function booleanWithFallback(value, fallback) {
      if (typeof value === "boolean") {
        return Boolean(value);
      }
      return Boolean(fallback);
    }
    var AuthorizationCodeWithPkceOidcHandlerBase = class {
      storageUtility;
      redirector;
      constructor(storageUtility, redirector) {
        this.storageUtility = storageUtility;
        this.redirector = redirector;
        this.storageUtility = storageUtility;
        this.redirector = redirector;
      }
      parametersGuard = (oidcLoginOptions) => {
        return oidcLoginOptions.issuerConfiguration.grantTypesSupported !== void 0 && oidcLoginOptions.issuerConfiguration.grantTypesSupported.indexOf("authorization_code") > -1 && oidcLoginOptions.redirectUrl !== void 0;
      };
      async canHandle(oidcLoginOptions) {
        return this.parametersGuard(oidcLoginOptions);
      }
      async setupRedirectHandler({ oidcLoginOptions, state, codeVerifier, targetUrl }) {
        if (!this.parametersGuard(oidcLoginOptions)) {
          throw new Error("The authorization code grant requires a redirectUrl.");
        }
        await Promise.all([
          // We use the OAuth 'state' value (which should be crypto-random) as
          // the key in our storage to store our actual SessionID. We do this
          // 'cos we'll need to lookup our session information again when the
          // browser is redirected back to us (i.e. the OAuth client
          // application) from the Authorization Server.
          // We don't want to use our session ID as the OAuth 'state' value, as
          // that session ID can be any developer-specified value, and therefore
          // may not be appropriate (since the OAuth 'state' value should really
          // be an unguessable crypto-random value).
          this.storageUtility.setForUser(state, {
            sessionId: oidcLoginOptions.sessionId
          }),
          // Store our login-process state using the session ID as the key.
          // Strictly speaking, this indirection from our OAuth state value to
          // our session ID is unnecessary, but it provides a slightly cleaner
          // separation of concerns.
          this.storageUtility.setForUser(oidcLoginOptions.sessionId, {
            codeVerifier,
            issuer: oidcLoginOptions.issuer.toString(),
            // The redirect URL is read after redirect, so it must be stored now.
            redirectUrl: oidcLoginOptions.redirectUrl,
            dpop: Boolean(oidcLoginOptions.dpop).toString(),
            keepAlive: booleanWithFallback(oidcLoginOptions.keepAlive, true).toString()
          })
        ]);
        this.redirector.redirect(targetUrl, {
          handleRedirect: oidcLoginOptions.handleRedirect
        });
        return void 0;
      }
    };
    var GeneralLogoutHandler = class {
      sessionInfoManager;
      constructor(sessionInfoManager) {
        this.sessionInfoManager = sessionInfoManager;
        this.sessionInfoManager = sessionInfoManager;
      }
      async canHandle() {
        return true;
      }
      async handle(userId) {
        await this.sessionInfoManager.clear(userId);
      }
    };
    var IRpLogoutHandler = class {
      redirector;
      constructor(redirector) {
        this.redirector = redirector;
        this.redirector = redirector;
      }
      async canHandle(userId, options) {
        return options?.logoutType === "idp";
      }
      async handle(userId, options) {
        if (options?.logoutType !== "idp") {
          throw new Error("Attempting to call idp logout handler to perform app logout");
        }
        if (options.toLogoutUrl === void 0) {
          throw new Error("Cannot perform IDP logout. Did you log in using the OIDC authentication flow?");
        }
        this.redirector.redirect(options.toLogoutUrl(options), {
          handleRedirect: options.handleRedirect
        });
      }
    };
    var IWaterfallLogoutHandler = class {
      handlers;
      constructor(sessionInfoManager, redirector) {
        this.handlers = [
          new GeneralLogoutHandler(sessionInfoManager),
          new IRpLogoutHandler(redirector)
        ];
      }
      async canHandle() {
        return true;
      }
      async handle(userId, options) {
        for (const handler of this.handlers) {
          if (await handler.canHandle(userId, options))
            await handler.handle(userId, options);
        }
      }
    };
    function getUnauthenticatedSession() {
      return {
        isLoggedIn: false,
        sessionId: uuid.v4(),
        fetch: (...args) => fetch(...args)
      };
    }
    async function clear(sessionId, storage) {
      await Promise.all([
        storage.deleteAllUserData(sessionId, { secure: false }),
        storage.deleteAllUserData(sessionId, { secure: true })
      ]);
    }
    var SessionInfoManagerBase = class {
      storageUtility;
      constructor(storageUtility) {
        this.storageUtility = storageUtility;
        this.storageUtility = storageUtility;
      }
      update(_sessionId, _options) {
        throw new Error("Not Implemented");
      }
      set(_sessionId, _sessionInfo) {
        throw new Error("Not Implemented");
      }
      get(_) {
        throw new Error("Not implemented");
      }
      async getAll() {
        throw new Error("Not implemented");
      }
      /**
       * This function removes all session-related information from storage.
       * @param sessionId the session identifier
       * @hidden
       */
      async clear(sessionId) {
        return clear(sessionId, this.storageUtility);
      }
      /**
       * Registers a new session, so that its ID can be retrieved.
       */
      async register(_sessionId) {
        throw new Error("Not implemented");
      }
      /**
       * Returns all the registered session IDs. Differs from getAll, which also
       * returns additional session information.
       */
      async getRegisteredSessionIdAll() {
        throw new Error("Not implemented");
      }
      /**
       * Deletes all information about all sessions, including their registrations.
       */
      async clearAll() {
        throw new Error("Not implemented");
      }
      /**
       * Sets authorization request state in storage for a given session ID.
       */
      async setOidcContext(_sessionId, _authorizationRequestState) {
        throw new Error("Not implemented");
      }
    };
    function getEndSessionUrl({ endSessionEndpoint, idTokenHint, postLogoutRedirectUri, state }) {
      const url = new URL(endSessionEndpoint);
      if (idTokenHint !== void 0)
        url.searchParams.append("id_token_hint", idTokenHint);
      if (postLogoutRedirectUri !== void 0) {
        url.searchParams.append("post_logout_redirect_uri", postLogoutRedirectUri);
        if (state !== void 0)
          url.searchParams.append("state", state);
      }
      return url.toString();
    }
    function maybeBuildRpInitiatedLogout({ endSessionEndpoint, idTokenHint }) {
      if (endSessionEndpoint === void 0)
        return void 0;
      return function logout({ state, postLogoutUrl }) {
        return getEndSessionUrl({
          endSessionEndpoint,
          idTokenHint,
          state,
          postLogoutRedirectUri: postLogoutUrl
        });
      };
    }
    function isSupportedTokenType(token) {
      return typeof token === "string" && ["DPoP", "Bearer"].includes(token);
    }
    var USER_SESSION_PREFIX = "solidClientAuthenticationUser";
    function isValidUrl(url) {
      try {
        new URL(url);
        return true;
      } catch {
        return false;
      }
    }
    function determineSigningAlg(supported, preferred) {
      return preferred.find((signingAlg) => {
        return supported.includes(signingAlg);
      }) ?? null;
    }
    function isStaticClient(options) {
      return options.clientId !== void 0 && !isValidUrl(options.clientId);
    }
    function isSolidOidcClient(options, issuerConfig) {
      return issuerConfig.scopesSupported.includes("webid") && options.clientId !== void 0 && isValidUrl(options.clientId);
    }
    function isKnownClientType(clientType) {
      return typeof clientType === "string" && ["dynamic", "static", "solid-oidc"].includes(clientType);
    }
    async function handleRegistration(options, issuerConfig, storageUtility, clientRegistrar) {
      let clientInfo;
      if (isSolidOidcClient(options, issuerConfig)) {
        clientInfo = {
          clientId: options.clientId,
          clientName: options.clientName,
          clientType: "solid-oidc"
        };
      } else if (isStaticClient(options)) {
        clientInfo = {
          clientId: options.clientId,
          clientSecret: options.clientSecret,
          clientName: options.clientName,
          clientType: "static"
        };
      } else {
        return clientRegistrar.getClient({
          sessionId: options.sessionId,
          clientName: options.clientName,
          redirectUrl: options.redirectUrl
        }, issuerConfig);
      }
      const infoToSave = {
        clientId: clientInfo.clientId,
        clientType: clientInfo.clientType
      };
      if (clientInfo.clientType === "static") {
        infoToSave.clientSecret = clientInfo.clientSecret;
      }
      if (clientInfo.clientName) {
        infoToSave.clientName = clientInfo.clientName;
      }
      await storageUtility.setForUser(options.sessionId, infoToSave);
      return clientInfo;
    }
    var boundFetch = (request, init) => fetch(request, init);
    var ClientAuthentication = class {
      loginHandler;
      redirectHandler;
      logoutHandler;
      sessionInfoManager;
      issuerConfigFetcher;
      boundLogout;
      constructor(loginHandler, redirectHandler, logoutHandler, sessionInfoManager, issuerConfigFetcher) {
        this.loginHandler = loginHandler;
        this.redirectHandler = redirectHandler;
        this.logoutHandler = logoutHandler;
        this.sessionInfoManager = sessionInfoManager;
        this.issuerConfigFetcher = issuerConfigFetcher;
        this.loginHandler = loginHandler;
        this.redirectHandler = redirectHandler;
        this.logoutHandler = logoutHandler;
        this.sessionInfoManager = sessionInfoManager;
        this.issuerConfigFetcher = issuerConfigFetcher;
      }
      // By default, our fetch() resolves to the environment fetch() function.
      fetch = boundFetch;
      logout = async (sessionId, options) => {
        await this.logoutHandler.handle(sessionId, options?.logoutType === "idp" ? {
          ...options,
          toLogoutUrl: this.boundLogout
        } : options);
        this.fetch = boundFetch;
        delete this.boundLogout;
      };
      getSessionInfo = async (sessionId) => {
        return this.sessionInfoManager.get(sessionId);
      };
      getAllSessionInfo = async () => {
        return this.sessionInfoManager.getAll();
      };
    };
    async function getSessionIdFromOauthState(storageUtility, oauthState) {
      return storageUtility.getForUser(oauthState, "sessionId");
    }
    async function loadOidcContextFromStorage(sessionId, storageUtility, configFetcher) {
      try {
        const [issuerIri, codeVerifier, storedRedirectIri, dpop, keepAlive] = await Promise.all([
          storageUtility.getForUser(sessionId, "issuer", {
            errorIfNull: true
          }),
          storageUtility.getForUser(sessionId, "codeVerifier"),
          storageUtility.getForUser(sessionId, "redirectUrl"),
          storageUtility.getForUser(sessionId, "dpop", { errorIfNull: true }),
          storageUtility.getForUser(sessionId, "keepAlive")
        ]);
        await storageUtility.deleteForUser(sessionId, "codeVerifier");
        const issuerConfig = await configFetcher.fetchConfig(issuerIri);
        return {
          codeVerifier,
          redirectUrl: storedRedirectIri,
          issuerConfig,
          dpop: dpop === "true",
          // Default keepAlive to true if not found in storage.
          keepAlive: typeof keepAlive === "string" ? keepAlive === "true" : true
        };
      } catch (e) {
        throw new Error(`Failed to retrieve OIDC context from storage associated with session [${sessionId}]: ${e}`);
      }
    }
    async function saveSessionInfoToStorage(storageUtility, sessionId, webId, clientId, isLoggedIn, refreshToken, secure, dpopKey) {
      if (refreshToken !== void 0) {
        await storageUtility.setForUser(sessionId, { refreshToken }, { secure });
      }
      if (webId !== void 0) {
        await storageUtility.setForUser(sessionId, { webId }, { secure });
      }
      if (clientId !== void 0) {
        await storageUtility.setForUser(sessionId, { clientId }, { secure });
      }
      if (isLoggedIn !== void 0) {
        await storageUtility.setForUser(sessionId, { isLoggedIn }, { secure });
      }
      if (dpopKey !== void 0) {
        await storageUtility.setForUser(sessionId, {
          publicKey: JSON.stringify(dpopKey.publicKey),
          privateKey: JSON.stringify(await jose.exportJWK(dpopKey.privateKey))
        }, { secure });
      }
    }
    var StorageUtility = class {
      secureStorage;
      insecureStorage;
      constructor(secureStorage, insecureStorage) {
        this.secureStorage = secureStorage;
        this.insecureStorage = insecureStorage;
        this.secureStorage = secureStorage;
        this.insecureStorage = insecureStorage;
      }
      getKey(userId) {
        return `solidClientAuthenticationUser:${userId}`;
      }
      async getUserData(userId, secure) {
        const stored = await (secure ? this.secureStorage : this.insecureStorage).get(this.getKey(userId));
        if (stored === void 0) {
          return {};
        }
        try {
          return JSON.parse(stored);
        } catch (_err) {
          throw new Error(`Data for user [${userId}] in [${secure ? "secure" : "unsecure"}] storage is corrupted - expected valid JSON, but got: ${stored}`);
        }
      }
      async setUserData(userId, data, secure) {
        await (secure ? this.secureStorage : this.insecureStorage).set(this.getKey(userId), JSON.stringify(data));
      }
      async get(key, options) {
        const value = await (options?.secure ? this.secureStorage : this.insecureStorage).get(key);
        if (value === void 0 && options?.errorIfNull) {
          throw new Error(`[${key}] is not stored`);
        }
        return value;
      }
      async set(key, value, options) {
        return (options?.secure ? this.secureStorage : this.insecureStorage).set(key, value);
      }
      async delete(key, options) {
        return (options?.secure ? this.secureStorage : this.insecureStorage).delete(key);
      }
      async getForUser(userId, key, options) {
        const userData = await this.getUserData(userId, options?.secure);
        let value;
        if (!userData || !userData[key]) {
          value = void 0;
        }
        value = userData[key];
        if (value === void 0 && options?.errorIfNull) {
          throw new Error(`Field [${key}] for user [${userId}] is not stored`);
        }
        return value || void 0;
      }
      async setForUser(userId, values, options) {
        let userData;
        try {
          userData = await this.getUserData(userId, options?.secure);
        } catch {
          userData = {};
        }
        await this.setUserData(userId, { ...userData, ...values }, options?.secure);
      }
      async deleteForUser(userId, key, options) {
        const userData = await this.getUserData(userId, options?.secure);
        delete userData[key];
        await this.setUserData(userId, userData, options?.secure);
      }
      async deleteAllUserData(userId, options) {
        await (options?.secure ? this.secureStorage : this.insecureStorage).delete(this.getKey(userId));
      }
    };
    var InMemoryStorage = class {
      map = {};
      async get(key) {
        return this.map[key] || void 0;
      }
      async set(key, value) {
        this.map[key] = value;
      }
      async delete(key) {
        delete this.map[key];
      }
    };
    var ConfigurationError = class extends Error {
      /* istanbul ignore next */
      constructor(message2) {
        super(message2);
      }
    };
    var NotImplementedError = class extends Error {
      /* istanbul ignore next */
      constructor(methodName) {
        super(`[${methodName}] is not implemented`);
      }
    };
    var InvalidResponseError = class extends Error {
      missingFields;
      /* istanbul ignore next */
      constructor(missingFields) {
        super(`Invalid response from OIDC provider: missing fields ${missingFields}`);
        this.missingFields = missingFields;
      }
    };
    var OidcProviderError = class extends Error {
      error;
      errorDescription;
      /* istanbul ignore next */
      constructor(message2, error, errorDescription) {
        super(message2);
        this.error = error;
        this.errorDescription = errorDescription;
      }
    };
    function normalizeHTU(audience) {
      const audienceUrl = new URL(audience);
      return new URL(audienceUrl.pathname, audienceUrl.origin).toString();
    }
    async function createDpopHeader(audience, method, dpopKey) {
      return new jose.SignJWT({
        htu: normalizeHTU(audience),
        htm: method.toUpperCase(),
        jti: uuid.v4()
      }).setProtectedHeader({
        alg: PREFERRED_SIGNING_ALG[0],
        jwk: dpopKey.publicKey,
        typ: "dpop+jwt"
      }).setIssuedAt().sign(dpopKey.privateKey, {});
    }
    async function generateDpopKeyPair() {
      const { privateKey, publicKey } = await jose.generateKeyPair(PREFERRED_SIGNING_ALG[0], { extractable: true });
      const dpopKeyPair = {
        privateKey,
        publicKey: await jose.exportJWK(publicKey)
      };
      [dpopKeyPair.publicKey.alg] = PREFERRED_SIGNING_ALG;
      return dpopKeyPair;
    }
    var DEFAULT_EXPIRATION_TIME_SECONDS = 600;
    function isExpectedAuthError(statusCode) {
      return [401, 403].includes(statusCode);
    }
    async function buildDpopFetchOptions(targetUrl, authToken, dpopKey, defaultOptions) {
      const headers = new Headers(defaultOptions?.headers);
      headers.set("Authorization", `DPoP ${authToken}`);
      headers.set("DPoP", await createDpopHeader(targetUrl, defaultOptions?.method ?? "get", dpopKey));
      return {
        ...defaultOptions,
        headers
      };
    }
    async function buildAuthenticatedHeaders(targetUrl, authToken, dpopKey, defaultOptions) {
      if (dpopKey !== void 0) {
        return buildDpopFetchOptions(targetUrl, authToken, dpopKey, defaultOptions);
      }
      const headers = new Headers(defaultOptions?.headers);
      headers.set("Authorization", `Bearer ${authToken}`);
      return {
        ...defaultOptions,
        headers
      };
    }
    async function makeAuthenticatedRequest(accessToken, url, defaultRequestInit, dpopKey, unauthFetch = fetch) {
      return unauthFetch(url, await buildAuthenticatedHeaders(url.toString(), accessToken, dpopKey, defaultRequestInit));
    }
    async function refreshAccessToken(refreshOptions, dpopKey, eventEmitter) {
      const tokenSet = await refreshOptions.tokenRefresher.refresh(refreshOptions.sessionId, refreshOptions.refreshToken, dpopKey);
      eventEmitter?.emit(EVENTS.SESSION_EXTENDED, tokenSet.expiresIn ?? DEFAULT_EXPIRATION_TIME_SECONDS);
      return {
        accessToken: tokenSet.accessToken,
        refreshToken: tokenSet.refreshToken,
        expiresIn: tokenSet.expiresIn
      };
    }
    var computeRefreshDelay = (expiresIn) => {
      if (expiresIn !== void 0) {
        return expiresIn - REFRESH_BEFORE_EXPIRATION_SECONDS > 0 ? (
          // We want to refresh the token 5 seconds before they actually expire.
          expiresIn - REFRESH_BEFORE_EXPIRATION_SECONDS
        ) : expiresIn;
      }
      return DEFAULT_EXPIRATION_TIME_SECONDS;
    };
    function buildAuthenticatedFetch(accessToken, options) {
      let currentAccessToken = accessToken;
      let latestTimeout;
      const currentRefreshOptions = options?.refreshOptions;
      const emitter = options?.eventEmitter;
      if (options !== void 0 && currentRefreshOptions !== void 0) {
        const proactivelyRefreshToken = async () => {
          try {
            const { accessToken: refreshedAccessToken, refreshToken, expiresIn } = await refreshAccessToken(currentRefreshOptions, options.dpopKey, emitter);
            currentAccessToken = refreshedAccessToken;
            if (refreshToken !== void 0) {
              currentRefreshOptions.refreshToken = refreshToken;
            }
            clearTimeout(latestTimeout);
            latestTimeout = setTimeout(proactivelyRefreshToken, computeRefreshDelay(expiresIn) * 1e3);
            options.eventEmitter?.emit(EVENTS.TIMEOUT_SET, latestTimeout);
          } catch (e) {
            if (e instanceof OidcProviderError) {
              emitter?.emit(EVENTS.ERROR, e.error, e.errorDescription);
              emitter?.emit(EVENTS.SESSION_EXPIRED);
            }
            if (e instanceof InvalidResponseError && e.missingFields.includes("access_token")) {
              emitter?.emit(EVENTS.SESSION_EXPIRED);
            }
          }
        };
        latestTimeout = setTimeout(
          proactivelyRefreshToken,
          // If currentRefreshOptions is defined, options is necessarily defined too.
          computeRefreshDelay(options.expiresIn) * 1e3
        );
        emitter?.emit(EVENTS.TIMEOUT_SET, latestTimeout);
      } else if (emitter !== void 0) {
        const expirationTimeout = setTimeout(() => {
          emitter.emit(EVENTS.SESSION_EXPIRED);
        }, computeRefreshDelay(options?.expiresIn) * 1e3);
        emitter.emit(EVENTS.TIMEOUT_SET, expirationTimeout);
      }
      return async (url, requestInit) => {
        let response = await makeAuthenticatedRequest(currentAccessToken, url, requestInit, options?.dpopKey, options?.fetch);
        const failedButNotExpectedAuthError = !response.ok && !isExpectedAuthError(response.status);
        if (response.ok || failedButNotExpectedAuthError) {
          return response;
        }
        const hasBeenRedirected = response.url !== url;
        if (hasBeenRedirected && options?.dpopKey !== void 0) {
          response = await makeAuthenticatedRequest(
            currentAccessToken,
            // Replace the original target IRI (`url`) by the redirection target
            response.url,
            requestInit,
            options.dpopKey,
            options.fetch
          );
        }
        return response;
      };
    }
    var StorageUtilityGetResponse = "getResponse";
    var StorageUtilityMock = {
      /* eslint-disable @typescript-eslint/no-unused-vars */
      get: async (key, options) => StorageUtilityGetResponse,
      set: async (key, value) => {
      },
      delete: async (key) => {
      },
      getForUser: async (userId, key, options) => StorageUtilityGetResponse,
      setForUser: async (userId, values, options) => {
      },
      deleteForUser: async (userId, key, options) => {
      },
      deleteAllUserData: async (userId, options) => {
      }
    };
    var mockStorage = (stored) => {
      const store = stored;
      return {
        get: async (key) => {
          if (store[key] === void 0) {
            return void 0;
          }
          if (typeof store[key] === "string") {
            return store[key];
          }
          return JSON.stringify(store[key]);
        },
        set: async (key, value) => {
          store[key] = value;
        },
        delete: async (key) => {
          delete store[key];
        }
      };
    };
    var mockStorageUtility = (stored, isSecure = false) => {
      if (isSecure) {
        return new StorageUtility(mockStorage(stored), mockStorage({}));
      }
      return new StorageUtility(mockStorage({}), mockStorage(stored));
    };
    exports.AggregateHandler = AggregateHandler;
    exports.AuthorizationCodeWithPkceOidcHandlerBase = AuthorizationCodeWithPkceOidcHandlerBase;
    exports.ClientAuthentication = ClientAuthentication;
    exports.ConfigurationError = ConfigurationError;
    exports.DEFAULT_SCOPES = DEFAULT_SCOPES;
    exports.EVENTS = EVENTS;
    exports.GeneralLogoutHandler = GeneralLogoutHandler;
    exports.IRpLogoutHandler = IRpLogoutHandler;
    exports.IWaterfallLogoutHandler = IWaterfallLogoutHandler;
    exports.InMemoryStorage = InMemoryStorage;
    exports.InvalidResponseError = InvalidResponseError;
    exports.NotImplementedError = NotImplementedError;
    exports.OidcProviderError = OidcProviderError;
    exports.PREFERRED_SIGNING_ALG = PREFERRED_SIGNING_ALG;
    exports.REFRESH_BEFORE_EXPIRATION_SECONDS = REFRESH_BEFORE_EXPIRATION_SECONDS;
    exports.SOLID_CLIENT_AUTHN_KEY_PREFIX = SOLID_CLIENT_AUTHN_KEY_PREFIX;
    exports.SessionInfoManagerBase = SessionInfoManagerBase;
    exports.StorageUtility = StorageUtility;
    exports.StorageUtilityGetResponse = StorageUtilityGetResponse;
    exports.StorageUtilityMock = StorageUtilityMock;
    exports.USER_SESSION_PREFIX = USER_SESSION_PREFIX;
    exports.buildAuthenticatedFetch = buildAuthenticatedFetch;
    exports.clear = clear;
    exports.createDpopHeader = createDpopHeader;
    exports.determineSigningAlg = determineSigningAlg;
    exports.generateDpopKeyPair = generateDpopKeyPair;
    exports.getEndSessionUrl = getEndSessionUrl;
    exports.getSessionIdFromOauthState = getSessionIdFromOauthState;
    exports.getUnauthenticatedSession = getUnauthenticatedSession;
    exports.getWebidFromTokenPayload = getWebidFromTokenPayload;
    exports.handleRegistration = handleRegistration;
    exports.isKnownClientType = isKnownClientType;
    exports.isSupportedTokenType = isSupportedTokenType;
    exports.isValidRedirectUrl = isValidRedirectUrl;
    exports.loadOidcContextFromStorage = loadOidcContextFromStorage;
    exports.maybeBuildRpInitiatedLogout = maybeBuildRpInitiatedLogout;
    exports.mockStorage = mockStorage;
    exports.mockStorageUtility = mockStorageUtility;
    exports.normalizeScopes = normalizeScopes;
    exports.removeOpenIdParams = removeOpenIdParams;
    exports.saveSessionInfoToStorage = saveSessionInfoToStorage;
  }
});

// node_modules/events/events.js
var require_events = __commonJS({
  "node_modules/events/events.js"(exports, module) {
    "use strict";
    var R = typeof Reflect === "object" ? Reflect : null;
    var ReflectApply = R && typeof R.apply === "function" ? R.apply : function ReflectApply2(target, receiver, args) {
      return Function.prototype.apply.call(target, receiver, args);
    };
    var ReflectOwnKeys;
    if (R && typeof R.ownKeys === "function") {
      ReflectOwnKeys = R.ownKeys;
    } else if (Object.getOwnPropertySymbols) {
      ReflectOwnKeys = function ReflectOwnKeys2(target) {
        return Object.getOwnPropertyNames(target).concat(Object.getOwnPropertySymbols(target));
      };
    } else {
      ReflectOwnKeys = function ReflectOwnKeys2(target) {
        return Object.getOwnPropertyNames(target);
      };
    }
    function ProcessEmitWarning(warning) {
      if (console && console.warn) console.warn(warning);
    }
    var NumberIsNaN = Number.isNaN || function NumberIsNaN2(value) {
      return value !== value;
    };
    function EventEmitter() {
      EventEmitter.init.call(this);
    }
    module.exports = EventEmitter;
    module.exports.once = once;
    EventEmitter.EventEmitter = EventEmitter;
    EventEmitter.prototype._events = void 0;
    EventEmitter.prototype._eventsCount = 0;
    EventEmitter.prototype._maxListeners = void 0;
    var defaultMaxListeners = 10;
    function checkListener(listener) {
      if (typeof listener !== "function") {
        throw new TypeError('The "listener" argument must be of type Function. Received type ' + typeof listener);
      }
    }
    Object.defineProperty(EventEmitter, "defaultMaxListeners", {
      enumerable: true,
      get: function() {
        return defaultMaxListeners;
      },
      set: function(arg) {
        if (typeof arg !== "number" || arg < 0 || NumberIsNaN(arg)) {
          throw new RangeError('The value of "defaultMaxListeners" is out of range. It must be a non-negative number. Received ' + arg + ".");
        }
        defaultMaxListeners = arg;
      }
    });
    EventEmitter.init = function() {
      if (this._events === void 0 || this._events === Object.getPrototypeOf(this)._events) {
        this._events = /* @__PURE__ */ Object.create(null);
        this._eventsCount = 0;
      }
      this._maxListeners = this._maxListeners || void 0;
    };
    EventEmitter.prototype.setMaxListeners = function setMaxListeners(n) {
      if (typeof n !== "number" || n < 0 || NumberIsNaN(n)) {
        throw new RangeError('The value of "n" is out of range. It must be a non-negative number. Received ' + n + ".");
      }
      this._maxListeners = n;
      return this;
    };
    function _getMaxListeners(that) {
      if (that._maxListeners === void 0)
        return EventEmitter.defaultMaxListeners;
      return that._maxListeners;
    }
    EventEmitter.prototype.getMaxListeners = function getMaxListeners() {
      return _getMaxListeners(this);
    };
    EventEmitter.prototype.emit = function emit(type) {
      var args = [];
      for (var i = 1; i < arguments.length; i++) args.push(arguments[i]);
      var doError = type === "error";
      var events = this._events;
      if (events !== void 0)
        doError = doError && events.error === void 0;
      else if (!doError)
        return false;
      if (doError) {
        var er;
        if (args.length > 0)
          er = args[0];
        if (er instanceof Error) {
          throw er;
        }
        var err = new Error("Unhandled error." + (er ? " (" + er.message + ")" : ""));
        err.context = er;
        throw err;
      }
      var handler = events[type];
      if (handler === void 0)
        return false;
      if (typeof handler === "function") {
        ReflectApply(handler, this, args);
      } else {
        var len = handler.length;
        var listeners = arrayClone(handler, len);
        for (var i = 0; i < len; ++i)
          ReflectApply(listeners[i], this, args);
      }
      return true;
    };
    function _addListener(target, type, listener, prepend) {
      var m;
      var events;
      var existing;
      checkListener(listener);
      events = target._events;
      if (events === void 0) {
        events = target._events = /* @__PURE__ */ Object.create(null);
        target._eventsCount = 0;
      } else {
        if (events.newListener !== void 0) {
          target.emit(
            "newListener",
            type,
            listener.listener ? listener.listener : listener
          );
          events = target._events;
        }
        existing = events[type];
      }
      if (existing === void 0) {
        existing = events[type] = listener;
        ++target._eventsCount;
      } else {
        if (typeof existing === "function") {
          existing = events[type] = prepend ? [listener, existing] : [existing, listener];
        } else if (prepend) {
          existing.unshift(listener);
        } else {
          existing.push(listener);
        }
        m = _getMaxListeners(target);
        if (m > 0 && existing.length > m && !existing.warned) {
          existing.warned = true;
          var w = new Error("Possible EventEmitter memory leak detected. " + existing.length + " " + String(type) + " listeners added. Use emitter.setMaxListeners() to increase limit");
          w.name = "MaxListenersExceededWarning";
          w.emitter = target;
          w.type = type;
          w.count = existing.length;
          ProcessEmitWarning(w);
        }
      }
      return target;
    }
    EventEmitter.prototype.addListener = function addListener(type, listener) {
      return _addListener(this, type, listener, false);
    };
    EventEmitter.prototype.on = EventEmitter.prototype.addListener;
    EventEmitter.prototype.prependListener = function prependListener(type, listener) {
      return _addListener(this, type, listener, true);
    };
    function onceWrapper() {
      if (!this.fired) {
        this.target.removeListener(this.type, this.wrapFn);
        this.fired = true;
        if (arguments.length === 0)
          return this.listener.call(this.target);
        return this.listener.apply(this.target, arguments);
      }
    }
    function _onceWrap(target, type, listener) {
      var state = { fired: false, wrapFn: void 0, target, type, listener };
      var wrapped = onceWrapper.bind(state);
      wrapped.listener = listener;
      state.wrapFn = wrapped;
      return wrapped;
    }
    EventEmitter.prototype.once = function once2(type, listener) {
      checkListener(listener);
      this.on(type, _onceWrap(this, type, listener));
      return this;
    };
    EventEmitter.prototype.prependOnceListener = function prependOnceListener(type, listener) {
      checkListener(listener);
      this.prependListener(type, _onceWrap(this, type, listener));
      return this;
    };
    EventEmitter.prototype.removeListener = function removeListener(type, listener) {
      var list, events, position, i, originalListener;
      checkListener(listener);
      events = this._events;
      if (events === void 0)
        return this;
      list = events[type];
      if (list === void 0)
        return this;
      if (list === listener || list.listener === listener) {
        if (--this._eventsCount === 0)
          this._events = /* @__PURE__ */ Object.create(null);
        else {
          delete events[type];
          if (events.removeListener)
            this.emit("removeListener", type, list.listener || listener);
        }
      } else if (typeof list !== "function") {
        position = -1;
        for (i = list.length - 1; i >= 0; i--) {
          if (list[i] === listener || list[i].listener === listener) {
            originalListener = list[i].listener;
            position = i;
            break;
          }
        }
        if (position < 0)
          return this;
        if (position === 0)
          list.shift();
        else {
          spliceOne(list, position);
        }
        if (list.length === 1)
          events[type] = list[0];
        if (events.removeListener !== void 0)
          this.emit("removeListener", type, originalListener || listener);
      }
      return this;
    };
    EventEmitter.prototype.off = EventEmitter.prototype.removeListener;
    EventEmitter.prototype.removeAllListeners = function removeAllListeners(type) {
      var listeners, events, i;
      events = this._events;
      if (events === void 0)
        return this;
      if (events.removeListener === void 0) {
        if (arguments.length === 0) {
          this._events = /* @__PURE__ */ Object.create(null);
          this._eventsCount = 0;
        } else if (events[type] !== void 0) {
          if (--this._eventsCount === 0)
            this._events = /* @__PURE__ */ Object.create(null);
          else
            delete events[type];
        }
        return this;
      }
      if (arguments.length === 0) {
        var keys = Object.keys(events);
        var key;
        for (i = 0; i < keys.length; ++i) {
          key = keys[i];
          if (key === "removeListener") continue;
          this.removeAllListeners(key);
        }
        this.removeAllListeners("removeListener");
        this._events = /* @__PURE__ */ Object.create(null);
        this._eventsCount = 0;
        return this;
      }
      listeners = events[type];
      if (typeof listeners === "function") {
        this.removeListener(type, listeners);
      } else if (listeners !== void 0) {
        for (i = listeners.length - 1; i >= 0; i--) {
          this.removeListener(type, listeners[i]);
        }
      }
      return this;
    };
    function _listeners(target, type, unwrap3) {
      var events = target._events;
      if (events === void 0)
        return [];
      var evlistener = events[type];
      if (evlistener === void 0)
        return [];
      if (typeof evlistener === "function")
        return unwrap3 ? [evlistener.listener || evlistener] : [evlistener];
      return unwrap3 ? unwrapListeners(evlistener) : arrayClone(evlistener, evlistener.length);
    }
    EventEmitter.prototype.listeners = function listeners(type) {
      return _listeners(this, type, true);
    };
    EventEmitter.prototype.rawListeners = function rawListeners(type) {
      return _listeners(this, type, false);
    };
    EventEmitter.listenerCount = function(emitter, type) {
      if (typeof emitter.listenerCount === "function") {
        return emitter.listenerCount(type);
      } else {
        return listenerCount.call(emitter, type);
      }
    };
    EventEmitter.prototype.listenerCount = listenerCount;
    function listenerCount(type) {
      var events = this._events;
      if (events !== void 0) {
        var evlistener = events[type];
        if (typeof evlistener === "function") {
          return 1;
        } else if (evlistener !== void 0) {
          return evlistener.length;
        }
      }
      return 0;
    }
    EventEmitter.prototype.eventNames = function eventNames() {
      return this._eventsCount > 0 ? ReflectOwnKeys(this._events) : [];
    };
    function arrayClone(arr, n) {
      var copy = new Array(n);
      for (var i = 0; i < n; ++i)
        copy[i] = arr[i];
      return copy;
    }
    function spliceOne(list, index) {
      for (; index + 1 < list.length; index++)
        list[index] = list[index + 1];
      list.pop();
    }
    function unwrapListeners(arr) {
      var ret = new Array(arr.length);
      for (var i = 0; i < ret.length; ++i) {
        ret[i] = arr[i].listener || arr[i];
      }
      return ret;
    }
    function once(emitter, name) {
      return new Promise(function(resolve, reject) {
        function errorListener(err) {
          emitter.removeListener(name, resolver);
          reject(err);
        }
        function resolver() {
          if (typeof emitter.removeListener === "function") {
            emitter.removeListener("error", errorListener);
          }
          resolve([].slice.call(arguments));
        }
        ;
        eventTargetAgnosticAddListener(emitter, name, resolver, { once: true });
        if (name !== "error") {
          addErrorHandlerIfEventEmitter(emitter, errorListener, { once: true });
        }
      });
    }
    function addErrorHandlerIfEventEmitter(emitter, handler, flags) {
      if (typeof emitter.on === "function") {
        eventTargetAgnosticAddListener(emitter, "error", handler, flags);
      }
    }
    function eventTargetAgnosticAddListener(emitter, name, listener, flags) {
      if (typeof emitter.on === "function") {
        if (flags.once) {
          emitter.once(name, listener);
        } else {
          emitter.on(name, listener);
        }
      } else if (typeof emitter.addEventListener === "function") {
        emitter.addEventListener(name, function wrapListener(arg) {
          if (flags.once) {
            emitter.removeEventListener(name, wrapListener);
          }
          listener(arg);
        });
      } else {
        throw new TypeError('The "emitter" argument must be of type EventEmitter. Received type ' + typeof emitter);
      }
    }
  }
});

// node_modules/jwt-decode/build/cjs/index.js
var require_cjs = __commonJS({
  "node_modules/jwt-decode/build/cjs/index.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.jwtDecode = exports.InvalidTokenError = void 0;
    var InvalidTokenError = class extends Error {
    };
    exports.InvalidTokenError = InvalidTokenError;
    InvalidTokenError.prototype.name = "InvalidTokenError";
    function b64DecodeUnicode(str) {
      return decodeURIComponent(atob(str).replace(/(.)/g, (m, p) => {
        let code = p.charCodeAt(0).toString(16).toUpperCase();
        if (code.length < 2) {
          code = "0" + code;
        }
        return "%" + code;
      }));
    }
    function base64UrlDecode(str) {
      let output = str.replace(/-/g, "+").replace(/_/g, "/");
      switch (output.length % 4) {
        case 0:
          break;
        case 2:
          output += "==";
          break;
        case 3:
          output += "=";
          break;
        default:
          throw new Error("base64 string is not of the correct length");
      }
      try {
        return b64DecodeUnicode(output);
      } catch (err) {
        return atob(output);
      }
    }
    function jwtDecode(token, options) {
      if (typeof token !== "string") {
        throw new InvalidTokenError("Invalid token specified: must be a string");
      }
      options || (options = {});
      const pos = options.header === true ? 0 : 1;
      const part = token.split(".")[pos];
      if (typeof part !== "string") {
        throw new InvalidTokenError(`Invalid token specified: missing part #${pos + 1}`);
      }
      let decoded;
      try {
        decoded = base64UrlDecode(part);
      } catch (e) {
        throw new InvalidTokenError(`Invalid token specified: invalid base64 for part #${pos + 1} (${e.message})`);
      }
      try {
        return JSON.parse(decoded);
      } catch (e) {
        throw new InvalidTokenError(`Invalid token specified: invalid json for part #${pos + 1} (${e.message})`);
      }
    }
    exports.jwtDecode = jwtDecode;
  }
});

// node_modules/oidc-client-ts/dist/umd/oidc-client-ts.js
var require_oidc_client_ts = __commonJS({
  "node_modules/oidc-client-ts/dist/umd/oidc-client-ts.js"(exports, module) {
    "use strict";
    var __defProp2 = Object.defineProperty;
    var __getOwnPropDesc2 = Object.getOwnPropertyDescriptor;
    var __getOwnPropNames2 = Object.getOwnPropertyNames;
    var __hasOwnProp2 = Object.prototype.hasOwnProperty;
    var __export2 = (target, all) => {
      for (var name in all)
        __defProp2(target, name, { get: all[name], enumerable: true });
    };
    var __copyProps2 = (to, from, except, desc) => {
      if (from && typeof from === "object" || typeof from === "function") {
        for (let key of __getOwnPropNames2(from))
          if (!__hasOwnProp2.call(to, key) && key !== except)
            __defProp2(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc2(from, key)) || desc.enumerable });
      }
      return to;
    };
    var __toCommonJS2 = (mod) => __copyProps2(__defProp2({}, "__esModule", { value: true }), mod);
    var index_exports = {};
    __export2(index_exports, {
      AccessTokenEvents: () => AccessTokenEvents,
      CheckSessionIFrame: () => CheckSessionIFrame,
      DPoPState: () => DPoPState,
      ErrorResponse: () => ErrorResponse,
      ErrorTimeout: () => ErrorTimeout,
      InMemoryWebStorage: () => InMemoryWebStorage,
      IndexedDbDPoPStore: () => IndexedDbDPoPStore,
      Log: () => Log,
      Logger: () => Logger,
      MetadataService: () => MetadataService,
      OidcClient: () => OidcClient,
      OidcClientSettingsStore: () => OidcClientSettingsStore,
      SessionMonitor: () => SessionMonitor,
      SigninResponse: () => SigninResponse,
      SigninState: () => SigninState,
      SignoutResponse: () => SignoutResponse,
      State: () => State,
      User: () => User,
      UserManager: () => UserManager,
      UserManagerSettingsStore: () => UserManagerSettingsStore,
      Version: () => Version,
      WebStorageStateStore: () => WebStorageStateStore
    });
    module.exports = __toCommonJS2(index_exports);
    var nopLogger = {
      debug: () => void 0,
      info: () => void 0,
      warn: () => void 0,
      error: () => void 0
    };
    var level;
    var logger;
    var Log = /* @__PURE__ */ ((Log2) => {
      Log2[Log2["NONE"] = 0] = "NONE";
      Log2[Log2["ERROR"] = 1] = "ERROR";
      Log2[Log2["WARN"] = 2] = "WARN";
      Log2[Log2["INFO"] = 3] = "INFO";
      Log2[Log2["DEBUG"] = 4] = "DEBUG";
      return Log2;
    })(Log || {});
    ((Log2) => {
      function reset() {
        level = 3;
        logger = nopLogger;
      }
      Log2.reset = reset;
      function setLevel(value) {
        if (!(0 <= value && value <= 4)) {
          throw new Error("Invalid log level");
        }
        level = value;
      }
      Log2.setLevel = setLevel;
      function setLogger(value) {
        logger = value;
      }
      Log2.setLogger = setLogger;
    })(Log || (Log = {}));
    var Logger = class _Logger {
      constructor(_name) {
        this._name = _name;
      }
      /* eslint-disable @typescript-eslint/no-unsafe-enum-comparison */
      debug(...args) {
        if (level >= 4) {
          logger.debug(_Logger._format(this._name, this._method), ...args);
        }
      }
      info(...args) {
        if (level >= 3) {
          logger.info(_Logger._format(this._name, this._method), ...args);
        }
      }
      warn(...args) {
        if (level >= 2) {
          logger.warn(_Logger._format(this._name, this._method), ...args);
        }
      }
      error(...args) {
        if (level >= 1) {
          logger.error(_Logger._format(this._name, this._method), ...args);
        }
      }
      /* eslint-enable @typescript-eslint/no-unsafe-enum-comparison */
      throw(err) {
        this.error(err);
        throw err;
      }
      create(method) {
        const methodLogger = Object.create(this);
        methodLogger._method = method;
        methodLogger.debug("begin");
        return methodLogger;
      }
      static createStatic(name, staticMethod) {
        const staticLogger = new _Logger(`${name}.${staticMethod}`);
        staticLogger.debug("begin");
        return staticLogger;
      }
      static _format(name, method) {
        const prefix = `[${name}]`;
        return method ? `${prefix} ${method}:` : prefix;
      }
      /* eslint-disable @typescript-eslint/no-unsafe-enum-comparison */
      // helpers for static class methods
      static debug(name, ...args) {
        if (level >= 4) {
          logger.debug(_Logger._format(name), ...args);
        }
      }
      static info(name, ...args) {
        if (level >= 3) {
          logger.info(_Logger._format(name), ...args);
        }
      }
      static warn(name, ...args) {
        if (level >= 2) {
          logger.warn(_Logger._format(name), ...args);
        }
      }
      static error(name, ...args) {
        if (level >= 1) {
          logger.error(_Logger._format(name), ...args);
        }
      }
      /* eslint-enable @typescript-eslint/no-unsafe-enum-comparison */
    };
    Log.reset();
    var import_jwt_decode = require_cjs();
    var JwtUtils = class {
      // IMPORTANT: doesn't validate the token
      static decode(token) {
        try {
          return (0, import_jwt_decode.jwtDecode)(token);
        } catch (err) {
          Logger.error("JwtUtils.decode", err);
          throw err;
        }
      }
      static async generateSignedJwt(header, payload, privateKey) {
        const encodedHeader = CryptoUtils.encodeBase64Url(new TextEncoder().encode(JSON.stringify(header)));
        const encodedPayload = CryptoUtils.encodeBase64Url(new TextEncoder().encode(JSON.stringify(payload)));
        const encodedToken = `${encodedHeader}.${encodedPayload}`;
        const signature = await window.crypto.subtle.sign(
          {
            name: "ECDSA",
            hash: { name: "SHA-256" }
          },
          privateKey,
          new TextEncoder().encode(encodedToken)
        );
        const encodedSignature = CryptoUtils.encodeBase64Url(new Uint8Array(signature));
        return `${encodedToken}.${encodedSignature}`;
      }
      static async generateSignedJwtWithHmac(header, payload, secretKey) {
        const encodedHeader = CryptoUtils.encodeBase64Url(new TextEncoder().encode(JSON.stringify(header)));
        const encodedPayload = CryptoUtils.encodeBase64Url(new TextEncoder().encode(JSON.stringify(payload)));
        const encodedToken = `${encodedHeader}.${encodedPayload}`;
        const signature = await window.crypto.subtle.sign(
          "HMAC",
          secretKey,
          new TextEncoder().encode(encodedToken)
        );
        const encodedSignature = CryptoUtils.encodeBase64Url(new Uint8Array(signature));
        return `${encodedToken}.${encodedSignature}`;
      }
    };
    var UUID_V4_TEMPLATE = "10000000-1000-4000-8000-100000000000";
    var toBase64 = (val) => btoa([...new Uint8Array(val)].map((chr) => String.fromCharCode(chr)).join(""));
    var _CryptoUtils = class _CryptoUtils2 {
      static _randomWord() {
        const arr = new Uint32Array(1);
        crypto.getRandomValues(arr);
        return arr[0];
      }
      /**
       * Generates RFC4122 version 4 guid
       */
      static generateUUIDv4() {
        const uuid = UUID_V4_TEMPLATE.replace(
          /[018]/g,
          (c) => (+c ^ _CryptoUtils2._randomWord() & 15 >> +c / 4).toString(16)
        );
        return uuid.replace(/-/g, "");
      }
      /**
       * PKCE: Generate a code verifier
       */
      static generateCodeVerifier() {
        return _CryptoUtils2.generateUUIDv4() + _CryptoUtils2.generateUUIDv4() + _CryptoUtils2.generateUUIDv4();
      }
      /**
       * PKCE: Generate a code challenge
       */
      static async generateCodeChallenge(code_verifier) {
        if (!crypto.subtle) {
          throw new Error("Crypto.subtle is available only in secure contexts (HTTPS).");
        }
        try {
          const encoder2 = new TextEncoder();
          const data = encoder2.encode(code_verifier);
          const hashed = await crypto.subtle.digest("SHA-256", data);
          return toBase64(hashed).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
        } catch (err) {
          Logger.error("CryptoUtils.generateCodeChallenge", err);
          throw err;
        }
      }
      /**
       * Generates a base64-encoded string for a basic auth header
       */
      static generateBasicAuth(client_id, client_secret) {
        const encoder2 = new TextEncoder();
        const data = encoder2.encode([client_id, client_secret].join(":"));
        return toBase64(data);
      }
      /**
       * Generates a hash of a string using a given algorithm
       * @param alg
       * @param message
       */
      static async hash(alg, message2) {
        const msgUint8 = new TextEncoder().encode(message2);
        const hashBuffer = await crypto.subtle.digest(alg, msgUint8);
        return new Uint8Array(hashBuffer);
      }
      /**
       * Generates a rfc7638 compliant jwk thumbprint
       * @param jwk
       */
      static async customCalculateJwkThumbprint(jwk) {
        let jsonObject;
        switch (jwk.kty) {
          case "RSA":
            jsonObject = {
              "e": jwk.e,
              "kty": jwk.kty,
              "n": jwk.n
            };
            break;
          case "EC":
            jsonObject = {
              "crv": jwk.crv,
              "kty": jwk.kty,
              "x": jwk.x,
              "y": jwk.y
            };
            break;
          case "OKP":
            jsonObject = {
              "crv": jwk.crv,
              "kty": jwk.kty,
              "x": jwk.x
            };
            break;
          case "oct":
            jsonObject = {
              "crv": jwk.k,
              "kty": jwk.kty
            };
            break;
          default:
            throw new Error("Unknown jwk type");
        }
        const utf8encodedAndHashed = await _CryptoUtils2.hash("SHA-256", JSON.stringify(jsonObject));
        return _CryptoUtils2.encodeBase64Url(utf8encodedAndHashed);
      }
      static async generateDPoPProof({
        url,
        accessToken,
        httpMethod,
        keyPair,
        nonce
      }) {
        let hashedToken;
        let encodedHash;
        const payload = {
          "jti": window.crypto.randomUUID(),
          "htm": httpMethod != null ? httpMethod : "GET",
          "htu": url,
          "iat": Math.floor(Date.now() / 1e3)
        };
        if (accessToken) {
          hashedToken = await _CryptoUtils2.hash("SHA-256", accessToken);
          encodedHash = _CryptoUtils2.encodeBase64Url(hashedToken);
          payload.ath = encodedHash;
        }
        if (nonce) {
          payload.nonce = nonce;
        }
        try {
          const publicJwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
          const header = {
            "alg": "ES256",
            "typ": "dpop+jwt",
            "jwk": {
              "crv": publicJwk.crv,
              "kty": publicJwk.kty,
              "x": publicJwk.x,
              "y": publicJwk.y
            }
          };
          return await JwtUtils.generateSignedJwt(header, payload, keyPair.privateKey);
        } catch (err) {
          if (err instanceof TypeError) {
            throw new Error(`Error exporting dpop public key: ${err.message}`);
          } else {
            throw err;
          }
        }
      }
      static async generateDPoPJkt(keyPair) {
        try {
          const publicJwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
          return await _CryptoUtils2.customCalculateJwkThumbprint(publicJwk);
        } catch (err) {
          if (err instanceof TypeError) {
            throw new Error(`Could not retrieve dpop keys from storage: ${err.message}`);
          } else {
            throw err;
          }
        }
      }
      static async generateDPoPKeys() {
        return await window.crypto.subtle.generateKey(
          {
            name: "ECDSA",
            namedCurve: "P-256"
          },
          false,
          ["sign", "verify"]
        );
      }
      /**
       * Generates a client assertion JWT for client_secret_jwt authentication
       * @param client_id The client identifier
       * @param client_secret The client secret
       * @param audience The token endpoint URL (audience)
       * @param algorithm The HMAC algorithm to use (HS256, HS384, HS512). Defaults to HS256
       */
      static async generateClientAssertionJwt(client_id, client_secret, audience, algorithm = "HS256") {
        const now = Math.floor(Date.now() / 1e3);
        const header = {
          "alg": algorithm,
          "typ": "JWT"
        };
        const payload = {
          "iss": client_id,
          "sub": client_id,
          "aud": audience,
          "jti": _CryptoUtils2.generateUUIDv4(),
          "exp": now + 300,
          // 5 minutes
          "iat": now
        };
        const hashMap = {
          "HS256": "SHA-256",
          "HS384": "SHA-384",
          "HS512": "SHA-512"
        };
        const hashFunction = hashMap[algorithm];
        if (!hashFunction) {
          throw new Error(`Unsupported algorithm: ${algorithm}. Supported algorithms are: HS256, HS384, HS512`);
        }
        const encoder2 = new TextEncoder();
        const secretKey = await crypto.subtle.importKey(
          "raw",
          encoder2.encode(client_secret),
          { name: "HMAC", hash: hashFunction },
          false,
          ["sign"]
        );
        return await JwtUtils.generateSignedJwtWithHmac(header, payload, secretKey);
      }
    };
    _CryptoUtils.encodeBase64Url = (input) => {
      return toBase64(input).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
    };
    var CryptoUtils = _CryptoUtils;
    var Event = class {
      constructor(_name) {
        this._name = _name;
        this._callbacks = [];
        this._logger = new Logger(`Event('${this._name}')`);
      }
      addHandler(cb) {
        this._callbacks.push(cb);
        return () => this.removeHandler(cb);
      }
      removeHandler(cb) {
        const idx = this._callbacks.lastIndexOf(cb);
        if (idx >= 0) {
          this._callbacks.splice(idx, 1);
        }
      }
      async raise(...ev) {
        this._logger.debug("raise:", ...ev);
        for (const cb of this._callbacks) {
          await cb(...ev);
        }
      }
    };
    var PopupUtils = class {
      /**
       * Populates a map of window features with a placement centered in front of
       * the current window. If no explicit width is given, a default value is
       * binned into [800, 720, 600, 480, 360] based on the current window's width.
       */
      static center({ ...features }) {
        var _a, _b, _c;
        if (features.width == null)
          features.width = (_a = [800, 720, 600, 480].find((width) => width <= window.outerWidth / 1.618)) != null ? _a : 360;
        (_b = features.left) != null ? _b : features.left = Math.max(0, Math.round(window.screenX + (window.outerWidth - features.width) / 2));
        if (features.height != null)
          (_c = features.top) != null ? _c : features.top = Math.max(0, Math.round(window.screenY + (window.outerHeight - features.height) / 2));
        return features;
      }
      static serialize(features) {
        return Object.entries(features).filter(([, value]) => value != null).map(([key, value]) => `${key}=${typeof value !== "boolean" ? value : value ? "yes" : "no"}`).join(",");
      }
    };
    var Timer = class _Timer extends Event {
      constructor() {
        super(...arguments);
        this._logger = new Logger(`Timer('${this._name}')`);
        this._timerHandle = null;
        this._expiration = 0;
        this._callback = () => {
          const diff = this._expiration - _Timer.getEpochTime();
          this._logger.debug("timer completes in", diff);
          if (this._expiration <= _Timer.getEpochTime()) {
            this.cancel();
            void super.raise();
          }
        };
      }
      // get the time
      static getEpochTime() {
        return Math.floor(Date.now() / 1e3);
      }
      init(durationInSeconds) {
        const logger2 = this._logger.create("init");
        durationInSeconds = Math.max(Math.floor(durationInSeconds), 1);
        const expiration = _Timer.getEpochTime() + durationInSeconds;
        if (this.expiration === expiration && this._timerHandle) {
          logger2.debug("skipping since already initialized for expiration at", this.expiration);
          return;
        }
        this.cancel();
        logger2.debug("using duration", durationInSeconds);
        this._expiration = expiration;
        const timerDurationInSeconds = Math.min(durationInSeconds, 5);
        this._timerHandle = setInterval(this._callback, timerDurationInSeconds * 1e3);
      }
      get expiration() {
        return this._expiration;
      }
      cancel() {
        this._logger.create("cancel");
        if (this._timerHandle) {
          clearInterval(this._timerHandle);
          this._timerHandle = null;
        }
      }
    };
    var UrlUtils = class {
      static readParams(url, responseMode = "query") {
        if (!url) throw new TypeError("Invalid URL");
        const parsedUrl = new URL(url, "http://127.0.0.1");
        const params = parsedUrl[responseMode === "fragment" ? "hash" : "search"];
        return new URLSearchParams(params.slice(1));
      }
    };
    var URL_STATE_DELIMITER = ";";
    var ErrorResponse = class extends Error {
      constructor(args, form) {
        var _a, _b, _c;
        super(args.error_description || args.error || "");
        this.form = form;
        this.name = "ErrorResponse";
        if (!args.error) {
          Logger.error("ErrorResponse", "No error passed");
          throw new Error("No error passed");
        }
        this.error = args.error;
        this.error_description = (_a = args.error_description) != null ? _a : null;
        this.error_uri = (_b = args.error_uri) != null ? _b : null;
        this.state = args.userState;
        this.session_state = (_c = args.session_state) != null ? _c : null;
        this.url_state = args.url_state;
      }
    };
    var ErrorTimeout = class extends Error {
      constructor(message2) {
        super(message2);
        this.name = "ErrorTimeout";
      }
    };
    var AccessTokenEvents = class {
      constructor(args) {
        this._logger = new Logger("AccessTokenEvents");
        this._expiringTimer = new Timer("Access token expiring");
        this._expiredTimer = new Timer("Access token expired");
        this._expiringNotificationTimeInSeconds = args.expiringNotificationTimeInSeconds;
      }
      async load(container) {
        const logger2 = this._logger.create("load");
        if (container.access_token && container.expires_in !== void 0) {
          const duration = container.expires_in;
          logger2.debug("access token present, remaining duration:", duration);
          if (duration > 0) {
            let expiring = duration - this._expiringNotificationTimeInSeconds;
            if (expiring <= 0) {
              expiring = 1;
            }
            logger2.debug("registering expiring timer, raising in", expiring, "seconds");
            this._expiringTimer.init(expiring);
          } else {
            logger2.debug("canceling existing expiring timer because we're past expiration.");
            this._expiringTimer.cancel();
          }
          const expired = duration + 1;
          logger2.debug("registering expired timer, raising in", expired, "seconds");
          this._expiredTimer.init(expired);
        } else {
          this._expiringTimer.cancel();
          this._expiredTimer.cancel();
        }
      }
      async unload() {
        this._logger.debug("unload: canceling existing access token timers");
        this._expiringTimer.cancel();
        this._expiredTimer.cancel();
      }
      /**
       * Add callback: Raised prior to the access token expiring.
       */
      addAccessTokenExpiring(cb) {
        return this._expiringTimer.addHandler(cb);
      }
      /**
       * Remove callback: Raised prior to the access token expiring.
       */
      removeAccessTokenExpiring(cb) {
        this._expiringTimer.removeHandler(cb);
      }
      /**
       * Add callback: Raised after the access token has expired.
       */
      addAccessTokenExpired(cb) {
        return this._expiredTimer.addHandler(cb);
      }
      /**
       * Remove callback: Raised after the access token has expired.
       */
      removeAccessTokenExpired(cb) {
        this._expiredTimer.removeHandler(cb);
      }
    };
    var CheckSessionIFrame = class {
      constructor(_callback, _client_id, url, _intervalInSeconds, _stopOnError) {
        this._callback = _callback;
        this._client_id = _client_id;
        this._intervalInSeconds = _intervalInSeconds;
        this._stopOnError = _stopOnError;
        this._logger = new Logger("CheckSessionIFrame");
        this._timer = null;
        this._session_state = null;
        this._message = (e) => {
          if (e.origin === this._frame_origin && e.source === this._frame.contentWindow) {
            if (e.data === "error") {
              this._logger.error("error message from check session op iframe");
              if (this._stopOnError) {
                this.stop();
              }
            } else if (e.data === "changed") {
              this._logger.debug("changed message from check session op iframe");
              this.stop();
              void this._callback();
            } else {
              this._logger.debug(e.data + " message from check session op iframe");
            }
          }
        };
        const parsedUrl = new URL(url);
        this._frame_origin = parsedUrl.origin;
        this._frame = window.document.createElement("iframe");
        this._frame.style.visibility = "hidden";
        this._frame.style.position = "fixed";
        this._frame.style.left = "-1000px";
        this._frame.style.top = "0";
        this._frame.width = "0";
        this._frame.height = "0";
        this._frame.src = parsedUrl.href;
      }
      load() {
        return new Promise((resolve) => {
          this._frame.onload = () => {
            resolve();
          };
          window.document.body.appendChild(this._frame);
          window.addEventListener("message", this._message, false);
        });
      }
      start(session_state) {
        if (this._session_state === session_state) {
          return;
        }
        this._logger.create("start");
        this.stop();
        this._session_state = session_state;
        const send = () => {
          if (!this._frame.contentWindow || !this._session_state) {
            return;
          }
          this._frame.contentWindow.postMessage(this._client_id + " " + this._session_state, this._frame_origin);
        };
        send();
        this._timer = setInterval(send, this._intervalInSeconds * 1e3);
      }
      stop() {
        this._logger.create("stop");
        this._session_state = null;
        if (this._timer) {
          clearInterval(this._timer);
          this._timer = null;
        }
      }
    };
    var InMemoryWebStorage = class {
      constructor() {
        this._logger = new Logger("InMemoryWebStorage");
        this._data = {};
      }
      clear() {
        this._logger.create("clear");
        this._data = {};
      }
      getItem(key) {
        this._logger.create(`getItem('${key}')`);
        return this._data[key];
      }
      setItem(key, value) {
        this._logger.create(`setItem('${key}')`);
        this._data[key] = value;
      }
      removeItem(key) {
        this._logger.create(`removeItem('${key}')`);
        delete this._data[key];
      }
      get length() {
        return Object.getOwnPropertyNames(this._data).length;
      }
      key(index) {
        return Object.getOwnPropertyNames(this._data)[index];
      }
    };
    var ErrorDPoPNonce = class extends Error {
      constructor(nonce, message2) {
        super(message2);
        this.name = "ErrorDPoPNonce";
        this.nonce = nonce;
      }
    };
    var JsonService = class {
      constructor(additionalContentTypes = [], _jwtHandler = null, _extraHeaders = {}) {
        this._jwtHandler = _jwtHandler;
        this._extraHeaders = _extraHeaders;
        this._logger = new Logger("JsonService");
        this._contentTypes = [];
        this._contentTypes.push(...additionalContentTypes, "application/json");
        if (_jwtHandler) {
          this._contentTypes.push("application/jwt");
        }
      }
      async fetchWithTimeout(input, init = {}) {
        const { timeoutInSeconds, ...initFetch } = init;
        if (!timeoutInSeconds) {
          return await fetch(input, initFetch);
        }
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutInSeconds * 1e3);
        try {
          const response = await fetch(input, {
            ...init,
            signal: controller.signal
          });
          return response;
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") {
            throw new ErrorTimeout("Network timed out");
          }
          throw err;
        } finally {
          clearTimeout(timeoutId);
        }
      }
      async getJson(url, {
        token,
        credentials,
        timeoutInSeconds
      } = {}) {
        const logger2 = this._logger.create("getJson");
        const headers = {
          "Accept": this._contentTypes.join(", ")
        };
        if (token) {
          logger2.debug("token passed, setting Authorization header");
          headers["Authorization"] = "Bearer " + token;
        }
        this._appendExtraHeaders(headers);
        let response;
        try {
          logger2.debug("url:", url);
          response = await this.fetchWithTimeout(url, { method: "GET", headers, timeoutInSeconds, credentials });
        } catch (err) {
          logger2.error("Network Error");
          throw err;
        }
        logger2.debug("HTTP response received, status", response.status);
        const contentType = response.headers.get("Content-Type");
        if (contentType && !this._contentTypes.find((item) => contentType.startsWith(item))) {
          logger2.throw(new Error(`Invalid response Content-Type: ${contentType != null ? contentType : "undefined"}, from URL: ${url}`));
        }
        if (response.ok && this._jwtHandler && (contentType == null ? void 0 : contentType.startsWith("application/jwt"))) {
          return await this._jwtHandler(await response.text());
        }
        let json;
        try {
          json = await response.json();
        } catch (err) {
          logger2.error("Error parsing JSON response", err);
          if (response.ok) throw err;
          throw new Error(`${response.statusText} (${response.status})`);
        }
        if (!response.ok) {
          logger2.error("Error from server:", json);
          if (json.error) {
            throw new ErrorResponse(json);
          }
          throw new Error(`${response.statusText} (${response.status}): ${JSON.stringify(json)}`);
        }
        return json;
      }
      async postForm(url, {
        body,
        basicAuth,
        timeoutInSeconds,
        initCredentials,
        extraHeaders
      }) {
        const logger2 = this._logger.create("postForm");
        const headers = {
          "Accept": this._contentTypes.join(", "),
          "Content-Type": "application/x-www-form-urlencoded",
          ...extraHeaders
        };
        if (basicAuth !== void 0) {
          headers["Authorization"] = "Basic " + basicAuth;
        }
        this._appendExtraHeaders(headers);
        let response;
        try {
          logger2.debug("url:", url);
          response = await this.fetchWithTimeout(url, { method: "POST", headers, body, timeoutInSeconds, credentials: initCredentials });
        } catch (err) {
          logger2.error("Network error");
          throw err;
        }
        logger2.debug("HTTP response received, status", response.status);
        const contentType = response.headers.get("Content-Type");
        if (contentType && !this._contentTypes.find((item) => contentType.startsWith(item))) {
          throw new Error(`Invalid response Content-Type: ${contentType != null ? contentType : "undefined"}, from URL: ${url}`);
        }
        const responseText = await response.text();
        let json = {};
        if (responseText) {
          try {
            json = JSON.parse(responseText);
          } catch (err) {
            logger2.error("Error parsing JSON response", err);
            if (response.ok) throw err;
            throw new Error(`${response.statusText} (${response.status})`);
          }
        }
        if (!response.ok) {
          logger2.error("Error from server:", json);
          if (response.headers.has("dpop-nonce")) {
            const nonce = response.headers.get("dpop-nonce");
            throw new ErrorDPoPNonce(nonce, `${JSON.stringify(json)}`);
          }
          if (json.error) {
            throw new ErrorResponse(json, body);
          }
          throw new Error(`${response.statusText} (${response.status}): ${JSON.stringify(json)}`);
        }
        return json;
      }
      _appendExtraHeaders(headers) {
        const logger2 = this._logger.create("appendExtraHeaders");
        const customKeys = Object.keys(this._extraHeaders);
        const protectedHeaders = [
          "accept",
          "content-type"
        ];
        const preventOverride = [
          "authorization"
        ];
        if (customKeys.length === 0) {
          return;
        }
        customKeys.forEach((headerName) => {
          if (protectedHeaders.includes(headerName.toLocaleLowerCase())) {
            logger2.warn("Protected header could not be set", headerName, protectedHeaders);
            return;
          }
          if (preventOverride.includes(headerName.toLocaleLowerCase()) && Object.keys(headers).includes(headerName)) {
            logger2.warn("Header could not be overridden", headerName, preventOverride);
            return;
          }
          const content = typeof this._extraHeaders[headerName] === "function" ? this._extraHeaders[headerName]() : this._extraHeaders[headerName];
          if (content && content !== "") {
            headers[headerName] = content;
          }
        });
      }
    };
    var MetadataService = class {
      constructor(_settings) {
        this._settings = _settings;
        this._logger = new Logger("MetadataService");
        this._signingKeys = null;
        this._metadata = null;
        this._metadataUrl = this._settings.metadataUrl;
        this._jsonService = new JsonService(
          ["application/jwk-set+json"],
          null,
          this._settings.extraHeaders
        );
        if (this._settings.signingKeys) {
          this._logger.debug("using signingKeys from settings");
          this._signingKeys = this._settings.signingKeys;
        }
        if (this._settings.metadata) {
          this._logger.debug("using metadata from settings");
          this._metadata = this._settings.metadata;
        }
        if (this._settings.fetchRequestCredentials) {
          this._logger.debug("using fetchRequestCredentials from settings");
          this._fetchRequestCredentials = this._settings.fetchRequestCredentials;
        }
      }
      resetSigningKeys() {
        this._signingKeys = null;
      }
      async getMetadata() {
        const logger2 = this._logger.create("getMetadata");
        if (this._metadata) {
          logger2.debug("using cached values");
          return this._metadata;
        }
        if (!this._metadataUrl) {
          logger2.throw(new Error("No authority or metadataUrl configured on settings"));
          throw null;
        }
        logger2.debug("getting metadata from", this._metadataUrl);
        const metadata = await this._jsonService.getJson(this._metadataUrl, { credentials: this._fetchRequestCredentials, timeoutInSeconds: this._settings.requestTimeoutInSeconds });
        logger2.debug("merging remote JSON with seed metadata");
        this._metadata = Object.assign({}, metadata, this._settings.metadataSeed);
        return this._metadata;
      }
      getIssuer() {
        return this._getMetadataProperty("issuer");
      }
      getAuthorizationEndpoint() {
        return this._getMetadataProperty("authorization_endpoint");
      }
      getUserInfoEndpoint() {
        return this._getMetadataProperty("userinfo_endpoint");
      }
      getTokenEndpoint(optional = true) {
        return this._getMetadataProperty("token_endpoint", optional);
      }
      getCheckSessionIframe() {
        return this._getMetadataProperty("check_session_iframe", true);
      }
      getEndSessionEndpoint() {
        return this._getMetadataProperty("end_session_endpoint", true);
      }
      getRevocationEndpoint(optional = true) {
        return this._getMetadataProperty("revocation_endpoint", optional);
      }
      getKeysEndpoint(optional = true) {
        return this._getMetadataProperty("jwks_uri", optional);
      }
      async _getMetadataProperty(name, optional = false) {
        const logger2 = this._logger.create(`_getMetadataProperty('${name}')`);
        const metadata = await this.getMetadata();
        logger2.debug("resolved");
        if (metadata[name] === void 0) {
          if (optional === true) {
            logger2.warn("Metadata does not contain optional property");
            return void 0;
          }
          logger2.throw(new Error("Metadata does not contain property " + name));
        }
        return metadata[name];
      }
      async getSigningKeys() {
        const logger2 = this._logger.create("getSigningKeys");
        if (this._signingKeys) {
          logger2.debug("returning signingKeys from cache");
          return this._signingKeys;
        }
        const jwks_uri = await this.getKeysEndpoint(false);
        logger2.debug("got jwks_uri", jwks_uri);
        const keySet = await this._jsonService.getJson(jwks_uri, { timeoutInSeconds: this._settings.requestTimeoutInSeconds });
        logger2.debug("got key set", keySet);
        if (!Array.isArray(keySet.keys)) {
          logger2.throw(new Error("Missing keys on keyset"));
          throw null;
        }
        this._signingKeys = keySet.keys;
        return this._signingKeys;
      }
    };
    var WebStorageStateStore = class {
      constructor({
        prefix = "oidc.",
        store = localStorage
      } = {}) {
        this._logger = new Logger("WebStorageStateStore");
        this._store = store;
        this._prefix = prefix;
      }
      async set(key, value) {
        this._logger.create(`set('${key}')`);
        key = this._prefix + key;
        await this._store.setItem(key, value);
      }
      async get(key) {
        this._logger.create(`get('${key}')`);
        key = this._prefix + key;
        const item = await this._store.getItem(key);
        return item;
      }
      async remove(key) {
        this._logger.create(`remove('${key}')`);
        key = this._prefix + key;
        const item = await this._store.getItem(key);
        await this._store.removeItem(key);
        return item;
      }
      async getAllKeys() {
        this._logger.create("getAllKeys");
        const len = await this._store.length;
        const keys = [];
        for (let index = 0; index < len; index++) {
          const key = await this._store.key(index);
          if (key && key.indexOf(this._prefix) === 0) {
            keys.push(key.substr(this._prefix.length));
          }
        }
        return keys;
      }
    };
    var DefaultResponseType = "code";
    var DefaultScope = "openid";
    var DefaultClientAuthentication = "client_secret_post";
    var DefaultStaleStateAgeInSeconds = 60 * 15;
    var OidcClientSettingsStore = class {
      constructor({
        // metadata related
        authority,
        metadataUrl,
        metadata,
        signingKeys,
        metadataSeed,
        // client related
        client_id,
        client_secret,
        response_type = DefaultResponseType,
        scope = DefaultScope,
        redirect_uri,
        post_logout_redirect_uri,
        client_authentication = DefaultClientAuthentication,
        token_endpoint_auth_signing_alg = "HS256",
        // optional protocol
        prompt,
        display,
        max_age,
        ui_locales,
        acr_values,
        resource,
        response_mode,
        // behavior flags
        filterProtocolClaims = true,
        loadUserInfo = false,
        requestTimeoutInSeconds,
        staleStateAgeInSeconds = DefaultStaleStateAgeInSeconds,
        mergeClaimsStrategy = { array: "replace" },
        disablePKCE = false,
        // other behavior
        stateStore,
        revokeTokenAdditionalContentTypes,
        fetchRequestCredentials,
        refreshTokenAllowedScope,
        // extra
        extraQueryParams = {},
        extraTokenParams = {},
        extraHeaders = {},
        dpop,
        omitScopeWhenRequesting = false
      }) {
        var _a;
        this.authority = authority;
        if (metadataUrl) {
          this.metadataUrl = metadataUrl;
        } else {
          this.metadataUrl = authority;
          if (authority) {
            if (!this.metadataUrl.endsWith("/")) {
              this.metadataUrl += "/";
            }
            this.metadataUrl += ".well-known/openid-configuration";
          }
        }
        this.metadata = metadata;
        this.metadataSeed = metadataSeed;
        this.signingKeys = signingKeys;
        this.client_id = client_id;
        this.client_secret = client_secret;
        this.response_type = response_type;
        this.scope = scope;
        this.redirect_uri = redirect_uri;
        this.post_logout_redirect_uri = post_logout_redirect_uri;
        this.client_authentication = client_authentication;
        this.token_endpoint_auth_signing_alg = token_endpoint_auth_signing_alg;
        this.prompt = prompt;
        this.display = display;
        this.max_age = max_age;
        this.ui_locales = ui_locales;
        this.acr_values = acr_values;
        this.resource = resource;
        this.response_mode = response_mode;
        this.filterProtocolClaims = filterProtocolClaims != null ? filterProtocolClaims : true;
        this.loadUserInfo = !!loadUserInfo;
        this.staleStateAgeInSeconds = staleStateAgeInSeconds;
        this.mergeClaimsStrategy = mergeClaimsStrategy;
        this.omitScopeWhenRequesting = omitScopeWhenRequesting;
        this.disablePKCE = !!disablePKCE;
        this.revokeTokenAdditionalContentTypes = revokeTokenAdditionalContentTypes;
        this.fetchRequestCredentials = fetchRequestCredentials ? fetchRequestCredentials : "same-origin";
        this.requestTimeoutInSeconds = requestTimeoutInSeconds;
        if (stateStore) {
          this.stateStore = stateStore;
        } else {
          const store = typeof window !== "undefined" ? window.localStorage : new InMemoryWebStorage();
          this.stateStore = new WebStorageStateStore({ store });
        }
        this.refreshTokenAllowedScope = refreshTokenAllowedScope;
        this.extraQueryParams = extraQueryParams;
        this.extraTokenParams = extraTokenParams;
        this.extraHeaders = extraHeaders;
        this.dpop = dpop;
        if (this.dpop && !((_a = this.dpop) == null ? void 0 : _a.store)) {
          throw new Error("A DPoPStore is required when dpop is enabled");
        }
      }
    };
    var UserInfoService = class {
      constructor(_settings, _metadataService) {
        this._settings = _settings;
        this._metadataService = _metadataService;
        this._logger = new Logger("UserInfoService");
        this._getClaimsFromJwt = async (responseText) => {
          const logger2 = this._logger.create("_getClaimsFromJwt");
          try {
            const payload = JwtUtils.decode(responseText);
            logger2.debug("JWT decoding successful");
            return payload;
          } catch (err) {
            logger2.error("Error parsing JWT response");
            throw err;
          }
        };
        this._jsonService = new JsonService(
          void 0,
          this._getClaimsFromJwt,
          this._settings.extraHeaders
        );
      }
      async getClaims(token) {
        const logger2 = this._logger.create("getClaims");
        if (!token) {
          this._logger.throw(new Error("No token passed"));
        }
        const url = await this._metadataService.getUserInfoEndpoint();
        logger2.debug("got userinfo url", url);
        const claims = await this._jsonService.getJson(url, {
          token,
          credentials: this._settings.fetchRequestCredentials,
          timeoutInSeconds: this._settings.requestTimeoutInSeconds
        });
        logger2.debug("got claims", claims);
        return claims;
      }
    };
    var TokenClient = class {
      constructor(_settings, _metadataService) {
        this._settings = _settings;
        this._metadataService = _metadataService;
        this._logger = new Logger("TokenClient");
        this._jsonService = new JsonService(
          this._settings.revokeTokenAdditionalContentTypes,
          null,
          this._settings.extraHeaders
        );
      }
      /**
       * Exchange code.
       *
       * @see https://www.rfc-editor.org/rfc/rfc6749#section-4.1.3
       */
      async exchangeCode({
        grant_type = "authorization_code",
        redirect_uri = this._settings.redirect_uri,
        client_id = this._settings.client_id,
        client_secret = this._settings.client_secret,
        extraHeaders,
        ...args
      }) {
        const logger2 = this._logger.create("exchangeCode");
        if (!client_id) {
          logger2.throw(new Error("A client_id is required"));
        }
        if (!redirect_uri) {
          logger2.throw(new Error("A redirect_uri is required"));
        }
        if (!args.code) {
          logger2.throw(new Error("A code is required"));
        }
        const params = new URLSearchParams({ grant_type, redirect_uri });
        for (const [key, value] of Object.entries(args)) {
          if (value != null) {
            params.set(key, value);
          }
        }
        if ((this._settings.client_authentication === "client_secret_basic" || this._settings.client_authentication === "client_secret_jwt") && (client_secret === void 0 || client_secret === null)) {
          logger2.throw(new Error("A client_secret is required"));
          throw null;
        }
        let basicAuth;
        const url = await this._metadataService.getTokenEndpoint(false);
        switch (this._settings.client_authentication) {
          case "client_secret_basic":
            basicAuth = CryptoUtils.generateBasicAuth(client_id, client_secret);
            break;
          case "client_secret_post":
            params.append("client_id", client_id);
            if (client_secret) {
              params.append("client_secret", client_secret);
            }
            break;
          case "client_secret_jwt": {
            const clientAssertion = await CryptoUtils.generateClientAssertionJwt(client_id, client_secret, url, this._settings.token_endpoint_auth_signing_alg);
            params.append("client_id", client_id);
            params.append("client_assertion_type", "urn:ietf:params:oauth:client-assertion-type:jwt-bearer");
            params.append("client_assertion", clientAssertion);
            break;
          }
        }
        logger2.debug("got token endpoint");
        const response = await this._jsonService.postForm(url, {
          body: params,
          basicAuth,
          timeoutInSeconds: this._settings.requestTimeoutInSeconds,
          initCredentials: this._settings.fetchRequestCredentials,
          extraHeaders
        });
        logger2.debug("got response");
        return response;
      }
      /**
       * Exchange credentials.
       *
       * @see https://www.rfc-editor.org/rfc/rfc6749#section-4.3.2
       */
      async exchangeCredentials({
        grant_type = "password",
        client_id = this._settings.client_id,
        client_secret = this._settings.client_secret,
        scope = this._settings.scope,
        ...args
      }) {
        const logger2 = this._logger.create("exchangeCredentials");
        if (!client_id) {
          logger2.throw(new Error("A client_id is required"));
        }
        const params = new URLSearchParams({ grant_type });
        if (!this._settings.omitScopeWhenRequesting) {
          params.set("scope", scope);
        }
        for (const [key, value] of Object.entries(args)) {
          if (value != null) {
            params.set(key, value);
          }
        }
        if ((this._settings.client_authentication === "client_secret_basic" || this._settings.client_authentication === "client_secret_jwt") && (client_secret === void 0 || client_secret === null)) {
          logger2.throw(new Error("A client_secret is required"));
          throw null;
        }
        let basicAuth;
        const url = await this._metadataService.getTokenEndpoint(false);
        switch (this._settings.client_authentication) {
          case "client_secret_basic":
            basicAuth = CryptoUtils.generateBasicAuth(client_id, client_secret);
            break;
          case "client_secret_post":
            params.append("client_id", client_id);
            if (client_secret) {
              params.append("client_secret", client_secret);
            }
            break;
          case "client_secret_jwt": {
            const clientAssertion = await CryptoUtils.generateClientAssertionJwt(client_id, client_secret, url, this._settings.token_endpoint_auth_signing_alg);
            params.append("client_id", client_id);
            params.append("client_assertion_type", "urn:ietf:params:oauth:client-assertion-type:jwt-bearer");
            params.append("client_assertion", clientAssertion);
            break;
          }
        }
        logger2.debug("got token endpoint");
        const response = await this._jsonService.postForm(url, { body: params, basicAuth, timeoutInSeconds: this._settings.requestTimeoutInSeconds, initCredentials: this._settings.fetchRequestCredentials });
        logger2.debug("got response");
        return response;
      }
      /**
       * Exchange a refresh token.
       *
       * @see https://www.rfc-editor.org/rfc/rfc6749#section-6
       */
      async exchangeRefreshToken({
        grant_type = "refresh_token",
        client_id = this._settings.client_id,
        client_secret = this._settings.client_secret,
        timeoutInSeconds,
        extraHeaders,
        ...args
      }) {
        const logger2 = this._logger.create("exchangeRefreshToken");
        if (!client_id) {
          logger2.throw(new Error("A client_id is required"));
        }
        if (!args.refresh_token) {
          logger2.throw(new Error("A refresh_token is required"));
        }
        const params = new URLSearchParams({ grant_type });
        for (const [key, value] of Object.entries(args)) {
          if (Array.isArray(value)) {
            value.forEach((param) => params.append(key, param));
          } else if (value != null) {
            params.set(key, value);
          }
        }
        if ((this._settings.client_authentication === "client_secret_basic" || this._settings.client_authentication === "client_secret_jwt") && (client_secret === void 0 || client_secret === null)) {
          logger2.throw(new Error("A client_secret is required"));
          throw null;
        }
        let basicAuth;
        const url = await this._metadataService.getTokenEndpoint(false);
        switch (this._settings.client_authentication) {
          case "client_secret_basic":
            basicAuth = CryptoUtils.generateBasicAuth(client_id, client_secret);
            break;
          case "client_secret_post":
            params.append("client_id", client_id);
            if (client_secret) {
              params.append("client_secret", client_secret);
            }
            break;
          case "client_secret_jwt": {
            const clientAssertion = await CryptoUtils.generateClientAssertionJwt(client_id, client_secret, url, this._settings.token_endpoint_auth_signing_alg);
            params.append("client_id", client_id);
            params.append("client_assertion_type", "urn:ietf:params:oauth:client-assertion-type:jwt-bearer");
            params.append("client_assertion", clientAssertion);
            break;
          }
        }
        logger2.debug("got token endpoint");
        const response = await this._jsonService.postForm(url, { body: params, basicAuth, timeoutInSeconds, initCredentials: this._settings.fetchRequestCredentials, extraHeaders });
        logger2.debug("got response");
        return response;
      }
      /**
       * Revoke an access or refresh token.
       *
       * @see https://datatracker.ietf.org/doc/html/rfc7009#section-2.1
       */
      async revoke(args) {
        var _a;
        const logger2 = this._logger.create("revoke");
        if (!args.token) {
          logger2.throw(new Error("A token is required"));
        }
        const url = await this._metadataService.getRevocationEndpoint(false);
        logger2.debug(`got revocation endpoint, revoking ${(_a = args.token_type_hint) != null ? _a : "default token type"}`);
        const params = new URLSearchParams();
        for (const [key, value] of Object.entries(args)) {
          if (value != null) {
            params.set(key, value);
          }
        }
        params.set("client_id", this._settings.client_id);
        if (this._settings.client_secret) {
          params.set("client_secret", this._settings.client_secret);
        }
        await this._jsonService.postForm(url, { body: params, timeoutInSeconds: this._settings.requestTimeoutInSeconds });
        logger2.debug("got response");
      }
    };
    var ResponseValidator = class {
      constructor(_settings, _metadataService, _claimsService) {
        this._settings = _settings;
        this._metadataService = _metadataService;
        this._claimsService = _claimsService;
        this._logger = new Logger("ResponseValidator");
        this._userInfoService = new UserInfoService(this._settings, this._metadataService);
        this._tokenClient = new TokenClient(this._settings, this._metadataService);
      }
      async validateSigninResponse(response, state, extraHeaders) {
        const logger2 = this._logger.create("validateSigninResponse");
        this._processSigninState(response, state);
        logger2.debug("state processed");
        await this._processCode(response, state, extraHeaders);
        logger2.debug("code processed");
        if (response.isOpenId) {
          this._validateIdTokenAttributes(response, "", state.nonce);
        }
        logger2.debug("tokens validated");
        await this._processClaims(response, state == null ? void 0 : state.skipUserInfo, response.isOpenId);
        logger2.debug("claims processed");
      }
      async validateCredentialsResponse(response, skipUserInfo) {
        const logger2 = this._logger.create("validateCredentialsResponse");
        const shouldValidateSubClaim = response.isOpenId && !!response.id_token;
        if (shouldValidateSubClaim) {
          this._validateIdTokenAttributes(response);
        }
        logger2.debug("tokens validated");
        await this._processClaims(response, skipUserInfo, shouldValidateSubClaim);
        logger2.debug("claims processed");
      }
      async validateRefreshResponse(response, state) {
        var _a, _b;
        const logger2 = this._logger.create("validateRefreshResponse");
        response.userState = state.data;
        (_a = response.session_state) != null ? _a : response.session_state = state.session_state;
        (_b = response.scope) != null ? _b : response.scope = state.scope;
        if (response.isOpenId && !!response.id_token) {
          this._validateIdTokenAttributes(response, state.id_token);
          logger2.debug("ID Token validated");
        }
        if (!response.id_token) {
          response.id_token = state.id_token;
          response.profile = state.profile;
        }
        const hasIdToken = response.isOpenId && !!response.id_token;
        await this._processClaims(response, false, hasIdToken);
        logger2.debug("claims processed");
      }
      validateSignoutResponse(response, state) {
        const logger2 = this._logger.create("validateSignoutResponse");
        if (state.id !== response.state) {
          logger2.throw(new Error("State does not match"));
        }
        logger2.debug("state validated");
        response.userState = state.data;
        if (response.error) {
          logger2.warn("Response was error", response.error);
          throw new ErrorResponse(response);
        }
      }
      _processSigninState(response, state) {
        var _a;
        const logger2 = this._logger.create("_processSigninState");
        if (state.id !== response.state) {
          logger2.throw(new Error("State does not match"));
        }
        if (!state.client_id) {
          logger2.throw(new Error("No client_id on state"));
        }
        if (!state.authority) {
          logger2.throw(new Error("No authority on state"));
        }
        if (this._settings.authority !== state.authority) {
          logger2.throw(new Error("authority mismatch on settings vs. signin state"));
        }
        if (this._settings.client_id && this._settings.client_id !== state.client_id) {
          logger2.throw(new Error("client_id mismatch on settings vs. signin state"));
        }
        logger2.debug("state validated");
        response.userState = state.data;
        response.url_state = state.url_state;
        (_a = response.scope) != null ? _a : response.scope = state.scope;
        if (response.error) {
          logger2.warn("Response was error", response.error);
          throw new ErrorResponse(response);
        }
        if (state.code_verifier && !response.code) {
          logger2.throw(new Error("Expected code in response"));
        }
      }
      async _processClaims(response, skipUserInfo = false, validateSub = true) {
        const logger2 = this._logger.create("_processClaims");
        response.profile = this._claimsService.filterProtocolClaims(response.profile);
        if (skipUserInfo || !this._settings.loadUserInfo || !response.access_token) {
          logger2.debug("not loading user info");
          return;
        }
        logger2.debug("loading user info");
        const claims = await this._userInfoService.getClaims(response.access_token);
        logger2.debug("user info claims received from user info endpoint");
        if (validateSub && claims.sub !== response.profile.sub) {
          logger2.throw(new Error("subject from UserInfo response does not match subject in ID Token"));
        }
        response.profile = this._claimsService.mergeClaims(response.profile, this._claimsService.filterProtocolClaims(claims));
        logger2.debug("user info claims received, updated profile:", response.profile);
      }
      async _processCode(response, state, extraHeaders) {
        const logger2 = this._logger.create("_processCode");
        if (response.code) {
          logger2.debug("Validating code");
          const tokenResponse = await this._tokenClient.exchangeCode({
            client_id: state.client_id,
            client_secret: state.client_secret,
            code: response.code,
            redirect_uri: state.redirect_uri,
            code_verifier: state.code_verifier,
            extraHeaders,
            ...state.extraTokenParams
          });
          Object.assign(response, tokenResponse);
        } else {
          logger2.debug("No code to process");
        }
      }
      _validateIdTokenAttributes(response, existingToken, nonce) {
        var _a;
        const logger2 = this._logger.create("_validateIdTokenAttributes");
        logger2.debug("decoding ID Token JWT");
        const incoming = JwtUtils.decode((_a = response.id_token) != null ? _a : "");
        if (!incoming.sub) {
          logger2.throw(new Error("ID Token is missing a subject claim"));
        }
        if (nonce && incoming.nonce !== nonce) {
          logger2.throw(new Error("nonce in id_token does not match nonce in client storage"));
        }
        if (existingToken) {
          const existing = JwtUtils.decode(existingToken);
          if (incoming.sub !== existing.sub) {
            logger2.throw(new Error("sub in id_token does not match current sub"));
          }
          if (incoming.auth_time && incoming.auth_time !== existing.auth_time) {
            logger2.throw(new Error("auth_time in id_token does not match original auth_time"));
          }
          if (incoming.azp && incoming.azp !== existing.azp) {
            logger2.throw(new Error("azp in id_token does not match original azp"));
          }
          if (!incoming.azp && existing.azp) {
            logger2.throw(new Error("azp not in id_token, but present in original id_token"));
          }
        }
        response.profile = incoming;
      }
    };
    var State = class _State {
      constructor(args) {
        this.id = args.id || CryptoUtils.generateUUIDv4();
        this.data = args.data;
        if (args.created && args.created > 0) {
          this.created = args.created;
        } else {
          this.created = Timer.getEpochTime();
        }
        this.request_type = args.request_type;
        this.url_state = args.url_state;
      }
      toStorageString() {
        new Logger("State").create("toStorageString");
        return JSON.stringify({
          id: this.id,
          data: this.data,
          created: this.created,
          request_type: this.request_type,
          url_state: this.url_state
        });
      }
      static fromStorageString(storageString) {
        Logger.createStatic("State", "fromStorageString");
        return Promise.resolve(new _State(JSON.parse(storageString)));
      }
      static async clearStaleState(storage, age) {
        const logger2 = Logger.createStatic("State", "clearStaleState");
        const cutoff = Timer.getEpochTime() - age;
        const keys = await storage.getAllKeys();
        logger2.debug("got keys", keys);
        for (let i = 0; i < keys.length; i++) {
          const key = keys[i];
          const item = await storage.get(key);
          let remove = false;
          if (item) {
            try {
              const state = await _State.fromStorageString(item);
              logger2.debug("got item from key:", key, state.created);
              if (state.created <= cutoff) {
                remove = true;
              }
            } catch (err) {
              logger2.error("Error parsing state for key:", key, err);
              remove = true;
            }
          } else {
            logger2.debug("no item in storage for key:", key);
            remove = true;
          }
          if (remove) {
            logger2.debug("removed item for key:", key);
            void storage.remove(key);
          }
        }
      }
    };
    var SigninState = class _SigninState extends State {
      constructor(args) {
        super(args);
        this.code_verifier = args.code_verifier;
        this.code_challenge = args.code_challenge;
        this.authority = args.authority;
        this.client_id = args.client_id;
        this.redirect_uri = args.redirect_uri;
        this.scope = args.scope;
        this.client_secret = args.client_secret;
        this.extraTokenParams = args.extraTokenParams;
        this.response_mode = args.response_mode;
        this.skipUserInfo = args.skipUserInfo;
        this.nonce = args.nonce;
      }
      static async create(args) {
        const code_verifier = args.code_verifier === true ? CryptoUtils.generateCodeVerifier() : args.code_verifier || void 0;
        const code_challenge = code_verifier ? await CryptoUtils.generateCodeChallenge(code_verifier) : void 0;
        return new _SigninState({
          ...args,
          code_verifier,
          code_challenge
        });
      }
      toStorageString() {
        new Logger("SigninState").create("toStorageString");
        return JSON.stringify({
          id: this.id,
          data: this.data,
          created: this.created,
          request_type: this.request_type,
          url_state: this.url_state,
          code_verifier: this.code_verifier,
          authority: this.authority,
          client_id: this.client_id,
          redirect_uri: this.redirect_uri,
          scope: this.scope,
          client_secret: this.client_secret,
          extraTokenParams: this.extraTokenParams,
          response_mode: this.response_mode,
          skipUserInfo: this.skipUserInfo,
          nonce: this.nonce
        });
      }
      static fromStorageString(storageString) {
        Logger.createStatic("SigninState", "fromStorageString");
        const data = JSON.parse(storageString);
        return _SigninState.create(data);
      }
    };
    var _SigninRequest = class _SigninRequest2 {
      constructor(args) {
        this.url = args.url;
        this.state = args.state;
      }
      static async create({
        // mandatory
        url,
        authority,
        client_id,
        redirect_uri,
        response_type,
        scope,
        // optional
        state_data,
        response_mode,
        request_type,
        client_secret,
        nonce,
        url_state,
        resource,
        skipUserInfo,
        extraQueryParams,
        extraTokenParams,
        disablePKCE,
        dpopJkt,
        omitScopeWhenRequesting,
        ...optionalParams
      }) {
        if (!url) {
          this._logger.error("create: No url passed");
          throw new Error("url");
        }
        if (!client_id) {
          this._logger.error("create: No client_id passed");
          throw new Error("client_id");
        }
        if (!redirect_uri) {
          this._logger.error("create: No redirect_uri passed");
          throw new Error("redirect_uri");
        }
        if (!response_type) {
          this._logger.error("create: No response_type passed");
          throw new Error("response_type");
        }
        if (!scope) {
          this._logger.error("create: No scope passed");
          throw new Error("scope");
        }
        if (!authority) {
          this._logger.error("create: No authority passed");
          throw new Error("authority");
        }
        const state = await SigninState.create({
          data: state_data,
          request_type,
          url_state,
          code_verifier: !disablePKCE,
          client_id,
          authority,
          redirect_uri,
          response_mode,
          client_secret,
          scope,
          extraTokenParams,
          skipUserInfo,
          nonce
        });
        const parsedUrl = new URL(url);
        parsedUrl.searchParams.append("client_id", client_id);
        parsedUrl.searchParams.append("redirect_uri", redirect_uri);
        parsedUrl.searchParams.append("response_type", response_type);
        if (!omitScopeWhenRequesting) {
          parsedUrl.searchParams.append("scope", scope);
        }
        if (nonce) {
          parsedUrl.searchParams.append("nonce", nonce);
        }
        if (dpopJkt) {
          parsedUrl.searchParams.append("dpop_jkt", dpopJkt);
        }
        let stateParam = state.id;
        if (url_state) {
          stateParam = `${stateParam}${URL_STATE_DELIMITER}${url_state}`;
        }
        parsedUrl.searchParams.append("state", stateParam);
        if (state.code_challenge) {
          parsedUrl.searchParams.append("code_challenge", state.code_challenge);
          parsedUrl.searchParams.append("code_challenge_method", "S256");
        }
        if (resource) {
          const resources = Array.isArray(resource) ? resource : [resource];
          resources.forEach((r) => parsedUrl.searchParams.append("resource", r));
        }
        for (const [key, value] of Object.entries({ response_mode, ...optionalParams, ...extraQueryParams })) {
          if (value != null) {
            parsedUrl.searchParams.append(key, value.toString());
          }
        }
        return new _SigninRequest2({
          url: parsedUrl.href,
          state
        });
      }
    };
    _SigninRequest._logger = new Logger("SigninRequest");
    var SigninRequest = _SigninRequest;
    var OidcScope = "openid";
    var SigninResponse = class {
      constructor(params) {
        this.access_token = "";
        this.token_type = "";
        this.profile = {};
        this.state = params.get("state");
        this.session_state = params.get("session_state");
        if (this.state) {
          const splitState = decodeURIComponent(this.state).split(URL_STATE_DELIMITER);
          this.state = splitState[0];
          if (splitState.length > 1) {
            this.url_state = splitState.slice(1).join(URL_STATE_DELIMITER);
          }
        }
        this.error = params.get("error");
        this.error_description = params.get("error_description");
        this.error_uri = params.get("error_uri");
        this.code = params.get("code");
      }
      get expires_in() {
        if (this.expires_at === void 0) {
          return void 0;
        }
        return this.expires_at - Timer.getEpochTime();
      }
      set expires_in(value) {
        if (typeof value === "string") value = Number(value);
        if (value !== void 0 && value >= 0) {
          this.expires_at = Math.floor(value) + Timer.getEpochTime();
        }
      }
      get isOpenId() {
        var _a;
        return ((_a = this.scope) == null ? void 0 : _a.split(" ").includes(OidcScope)) || !!this.id_token;
      }
    };
    var SignoutRequest = class {
      constructor({
        url,
        state_data,
        id_token_hint,
        post_logout_redirect_uri,
        extraQueryParams,
        request_type,
        client_id,
        url_state
      }) {
        this._logger = new Logger("SignoutRequest");
        if (!url) {
          this._logger.error("ctor: No url passed");
          throw new Error("url");
        }
        const parsedUrl = new URL(url);
        if (id_token_hint) {
          parsedUrl.searchParams.append("id_token_hint", id_token_hint);
        }
        if (client_id) {
          parsedUrl.searchParams.append("client_id", client_id);
        }
        if (post_logout_redirect_uri) {
          parsedUrl.searchParams.append("post_logout_redirect_uri", post_logout_redirect_uri);
          if (state_data || url_state) {
            this.state = new State({ data: state_data, request_type, url_state });
            let stateParam = this.state.id;
            if (url_state) {
              stateParam = `${stateParam}${URL_STATE_DELIMITER}${url_state}`;
            }
            parsedUrl.searchParams.append("state", stateParam);
          }
        }
        for (const [key, value] of Object.entries({ ...extraQueryParams })) {
          if (value != null) {
            parsedUrl.searchParams.append(key, value.toString());
          }
        }
        this.url = parsedUrl.href;
      }
    };
    var SignoutResponse = class {
      constructor(params) {
        this.state = params.get("state");
        if (this.state) {
          const splitState = decodeURIComponent(this.state).split(URL_STATE_DELIMITER);
          this.state = splitState[0];
          if (splitState.length > 1) {
            this.url_state = splitState.slice(1).join(URL_STATE_DELIMITER);
          }
        }
        this.error = params.get("error");
        this.error_description = params.get("error_description");
        this.error_uri = params.get("error_uri");
      }
    };
    var DefaultProtocolClaims = [
      "nbf",
      "jti",
      "auth_time",
      "nonce",
      "acr",
      "amr",
      "azp",
      "at_hash"
      // https://openid.net/specs/openid-connect-core-1_0.html#CodeIDToken
    ];
    var InternalRequiredProtocolClaims = ["sub", "iss", "aud", "exp", "iat"];
    var ClaimsService = class {
      constructor(_settings) {
        this._settings = _settings;
        this._logger = new Logger("ClaimsService");
      }
      filterProtocolClaims(claims) {
        const result = { ...claims };
        if (this._settings.filterProtocolClaims) {
          let protocolClaims;
          if (Array.isArray(this._settings.filterProtocolClaims)) {
            protocolClaims = this._settings.filterProtocolClaims;
          } else {
            protocolClaims = DefaultProtocolClaims;
          }
          for (const claim of protocolClaims) {
            if (!InternalRequiredProtocolClaims.includes(claim)) {
              delete result[claim];
            }
          }
        }
        return result;
      }
      mergeClaims(claims1, claims2) {
        const result = { ...claims1 };
        for (const [claim, values] of Object.entries(claims2)) {
          if (result[claim] !== values) {
            if (Array.isArray(result[claim]) || Array.isArray(values)) {
              if (this._settings.mergeClaimsStrategy.array == "replace") {
                result[claim] = values;
              } else {
                const mergedValues = Array.isArray(result[claim]) ? result[claim] : [result[claim]];
                for (const value of Array.isArray(values) ? values : [values]) {
                  if (!mergedValues.includes(value)) {
                    mergedValues.push(value);
                  }
                }
                result[claim] = mergedValues;
              }
            } else if (typeof result[claim] === "object" && typeof values === "object") {
              result[claim] = this.mergeClaims(result[claim], values);
            } else {
              result[claim] = values;
            }
          }
        }
        return result;
      }
    };
    var DPoPState = class {
      constructor(keys, nonce) {
        this.keys = keys;
        this.nonce = nonce;
      }
    };
    var OidcClient = class {
      constructor(settings, metadataService) {
        this._logger = new Logger("OidcClient");
        this.settings = settings instanceof OidcClientSettingsStore ? settings : new OidcClientSettingsStore(settings);
        this.metadataService = metadataService != null ? metadataService : new MetadataService(this.settings);
        this._claimsService = new ClaimsService(this.settings);
        this._validator = new ResponseValidator(this.settings, this.metadataService, this._claimsService);
        this._tokenClient = new TokenClient(this.settings, this.metadataService);
      }
      async createSigninRequest({
        state,
        request,
        request_uri,
        request_type,
        id_token_hint,
        login_hint,
        skipUserInfo,
        nonce,
        url_state,
        response_type = this.settings.response_type,
        scope = this.settings.scope,
        redirect_uri = this.settings.redirect_uri,
        prompt = this.settings.prompt,
        display = this.settings.display,
        max_age = this.settings.max_age,
        ui_locales = this.settings.ui_locales,
        acr_values = this.settings.acr_values,
        resource = this.settings.resource,
        response_mode = this.settings.response_mode,
        extraQueryParams = this.settings.extraQueryParams,
        extraTokenParams = this.settings.extraTokenParams,
        dpopJkt,
        omitScopeWhenRequesting = this.settings.omitScopeWhenRequesting
      }) {
        const logger2 = this._logger.create("createSigninRequest");
        if (response_type !== "code") {
          throw new Error("Only the Authorization Code flow (with PKCE) is supported");
        }
        const url = await this.metadataService.getAuthorizationEndpoint();
        logger2.debug("Received authorization endpoint", url);
        const signinRequest = await SigninRequest.create({
          url,
          authority: this.settings.authority,
          client_id: this.settings.client_id,
          redirect_uri,
          response_type,
          scope,
          state_data: state,
          url_state,
          prompt,
          display,
          max_age,
          ui_locales,
          id_token_hint,
          login_hint,
          acr_values,
          dpopJkt,
          resource,
          request,
          request_uri,
          extraQueryParams,
          extraTokenParams,
          request_type,
          response_mode,
          client_secret: this.settings.client_secret,
          skipUserInfo,
          nonce,
          disablePKCE: this.settings.disablePKCE,
          omitScopeWhenRequesting
        });
        await this.clearStaleState();
        const signinState = signinRequest.state;
        await this.settings.stateStore.set(signinState.id, signinState.toStorageString());
        return signinRequest;
      }
      async readSigninResponseState(url, removeState = false) {
        const logger2 = this._logger.create("readSigninResponseState");
        const response = new SigninResponse(UrlUtils.readParams(url, this.settings.response_mode));
        if (!response.state) {
          logger2.throw(new Error("No state in response"));
          throw null;
        }
        const storedStateString = await this.settings.stateStore[removeState ? "remove" : "get"](response.state);
        if (!storedStateString) {
          logger2.throw(new Error("No matching state found in storage"));
          throw null;
        }
        const state = await SigninState.fromStorageString(storedStateString);
        return { state, response };
      }
      async processSigninResponse(url, extraHeaders, removeState = true) {
        const logger2 = this._logger.create("processSigninResponse");
        const { state, response } = await this.readSigninResponseState(url, removeState);
        logger2.debug("received state from storage; validating response");
        if (this.settings.dpop && this.settings.dpop.store) {
          const dpopProof = await this.getDpopProof(this.settings.dpop.store);
          extraHeaders = { ...extraHeaders, "DPoP": dpopProof };
        }
        try {
          await this._validator.validateSigninResponse(response, state, extraHeaders);
        } catch (err) {
          if (err instanceof ErrorDPoPNonce && this.settings.dpop) {
            const dpopProof = await this.getDpopProof(this.settings.dpop.store, err.nonce);
            extraHeaders["DPoP"] = dpopProof;
            await this._validator.validateSigninResponse(response, state, extraHeaders);
          } else {
            throw err;
          }
        }
        return response;
      }
      async getDpopProof(dpopStore, nonce) {
        let keyPair;
        let dpopState;
        if (!(await dpopStore.getAllKeys()).includes(this.settings.client_id)) {
          keyPair = await CryptoUtils.generateDPoPKeys();
          dpopState = new DPoPState(keyPair, nonce);
          await dpopStore.set(this.settings.client_id, dpopState);
        } else {
          dpopState = await dpopStore.get(this.settings.client_id);
          if (dpopState.nonce !== nonce && nonce) {
            dpopState.nonce = nonce;
            await dpopStore.set(this.settings.client_id, dpopState);
          }
        }
        return await CryptoUtils.generateDPoPProof({
          url: await this.metadataService.getTokenEndpoint(false),
          httpMethod: "POST",
          keyPair: dpopState.keys,
          nonce: dpopState.nonce
        });
      }
      async processResourceOwnerPasswordCredentials({
        username,
        password,
        skipUserInfo = false,
        extraTokenParams = {}
      }) {
        const tokenResponse = await this._tokenClient.exchangeCredentials({ username, password, ...extraTokenParams });
        const signinResponse = new SigninResponse(new URLSearchParams());
        Object.assign(signinResponse, tokenResponse);
        await this._validator.validateCredentialsResponse(signinResponse, skipUserInfo);
        return signinResponse;
      }
      async useRefreshToken({
        state,
        redirect_uri,
        resource,
        timeoutInSeconds,
        extraHeaders,
        extraTokenParams
      }) {
        var _a;
        const logger2 = this._logger.create("useRefreshToken");
        let scope;
        if (this.settings.refreshTokenAllowedScope === void 0) {
          scope = state.scope;
        } else {
          const allowableScopes = this.settings.refreshTokenAllowedScope.split(" ");
          const providedScopes = ((_a = state.scope) == null ? void 0 : _a.split(" ")) || [];
          scope = providedScopes.filter((s) => allowableScopes.includes(s)).join(" ");
        }
        if (this.settings.dpop && this.settings.dpop.store) {
          const dpopProof = await this.getDpopProof(this.settings.dpop.store);
          extraHeaders = { ...extraHeaders, "DPoP": dpopProof };
        }
        let result;
        try {
          result = await this._tokenClient.exchangeRefreshToken({
            refresh_token: state.refresh_token,
            // provide the (possible filtered) scope list
            scope,
            redirect_uri,
            resource,
            timeoutInSeconds,
            extraHeaders,
            ...extraTokenParams
          });
        } catch (err) {
          if (err instanceof ErrorDPoPNonce && this.settings.dpop) {
            extraHeaders["DPoP"] = await this.getDpopProof(this.settings.dpop.store, err.nonce);
            result = await this._tokenClient.exchangeRefreshToken({
              refresh_token: state.refresh_token,
              // provide the (possible filtered) scope list
              scope,
              redirect_uri,
              resource,
              timeoutInSeconds,
              extraHeaders,
              ...extraTokenParams
            });
          } else {
            throw err;
          }
        }
        const response = new SigninResponse(new URLSearchParams());
        Object.assign(response, result);
        logger2.debug("validating response", response);
        await this._validator.validateRefreshResponse(response, {
          ...state,
          // override the scope in the state handed over to the validator
          // so it can set the granted scope to the requested scope in case none is included in the response
          scope
        });
        return response;
      }
      async createSignoutRequest({
        state,
        id_token_hint,
        client_id,
        request_type,
        url_state,
        post_logout_redirect_uri = this.settings.post_logout_redirect_uri,
        extraQueryParams = this.settings.extraQueryParams
      } = {}) {
        const logger2 = this._logger.create("createSignoutRequest");
        const url = await this.metadataService.getEndSessionEndpoint();
        if (!url) {
          logger2.throw(new Error("No end session endpoint"));
          throw null;
        }
        logger2.debug("Received end session endpoint", url);
        if (!client_id && post_logout_redirect_uri && !id_token_hint) {
          client_id = this.settings.client_id;
        }
        const request = new SignoutRequest({
          url,
          id_token_hint,
          client_id,
          post_logout_redirect_uri,
          state_data: state,
          extraQueryParams,
          request_type,
          url_state
        });
        await this.clearStaleState();
        const signoutState = request.state;
        if (signoutState) {
          logger2.debug("Signout request has state to persist");
          await this.settings.stateStore.set(signoutState.id, signoutState.toStorageString());
        }
        return request;
      }
      async readSignoutResponseState(url, removeState = false) {
        const logger2 = this._logger.create("readSignoutResponseState");
        const response = new SignoutResponse(UrlUtils.readParams(url, this.settings.response_mode));
        if (!response.state) {
          logger2.debug("No state in response");
          if (response.error) {
            logger2.warn("Response was error:", response.error);
            throw new ErrorResponse(response);
          }
          return { state: void 0, response };
        }
        const storedStateString = await this.settings.stateStore[removeState ? "remove" : "get"](response.state);
        if (!storedStateString) {
          logger2.throw(new Error("No matching state found in storage"));
          throw null;
        }
        const state = await State.fromStorageString(storedStateString);
        return { state, response };
      }
      async processSignoutResponse(url) {
        const logger2 = this._logger.create("processSignoutResponse");
        const { state, response } = await this.readSignoutResponseState(url, true);
        if (state) {
          logger2.debug("Received state from storage; validating response");
          this._validator.validateSignoutResponse(response, state);
        } else {
          logger2.debug("No state from storage; skipping response validation");
        }
        return response;
      }
      clearStaleState() {
        this._logger.create("clearStaleState");
        return State.clearStaleState(this.settings.stateStore, this.settings.staleStateAgeInSeconds);
      }
      async revokeToken(token, type) {
        this._logger.create("revokeToken");
        return await this._tokenClient.revoke({
          token,
          token_type_hint: type
        });
      }
    };
    var SessionMonitor = class {
      constructor(_userManager) {
        this._userManager = _userManager;
        this._logger = new Logger("SessionMonitor");
        this._start = async (user) => {
          const session_state = user.session_state;
          if (!session_state) {
            return;
          }
          const logger2 = this._logger.create("_start");
          if (user.profile) {
            this._sub = user.profile.sub;
            logger2.debug("session_state", session_state, ", sub", this._sub);
          } else {
            this._sub = void 0;
            logger2.debug("session_state", session_state, ", anonymous user");
          }
          if (this._checkSessionIFrame) {
            this._checkSessionIFrame.start(session_state);
            return;
          }
          try {
            const url = await this._userManager.metadataService.getCheckSessionIframe();
            if (url) {
              logger2.debug("initializing check session iframe");
              const client_id = this._userManager.settings.client_id;
              const intervalInSeconds = this._userManager.settings.checkSessionIntervalInSeconds;
              const stopOnError = this._userManager.settings.stopCheckSessionOnError;
              const checkSessionIFrame = new CheckSessionIFrame(this._callback, client_id, url, intervalInSeconds, stopOnError);
              await checkSessionIFrame.load();
              this._checkSessionIFrame = checkSessionIFrame;
              checkSessionIFrame.start(session_state);
            } else {
              logger2.warn("no check session iframe found in the metadata");
            }
          } catch (err) {
            logger2.error("Error from getCheckSessionIframe:", err instanceof Error ? err.message : err);
          }
        };
        this._stop = () => {
          const logger2 = this._logger.create("_stop");
          this._sub = void 0;
          if (this._checkSessionIFrame) {
            this._checkSessionIFrame.stop();
          }
          if (this._userManager.settings.monitorAnonymousSession) {
            const timerHandle = setInterval(async () => {
              clearInterval(timerHandle);
              try {
                const session = await this._userManager.querySessionStatus();
                if (session) {
                  const tmpUser = {
                    session_state: session.session_state,
                    profile: session.sub ? {
                      sub: session.sub
                    } : null
                  };
                  void this._start(tmpUser);
                }
              } catch (err) {
                logger2.error("error from querySessionStatus", err instanceof Error ? err.message : err);
              }
            }, 1e3);
          }
        };
        this._callback = async () => {
          const logger2 = this._logger.create("_callback");
          try {
            const session = await this._userManager.querySessionStatus();
            let raiseEvent = true;
            if (session && this._checkSessionIFrame) {
              if (session.sub === this._sub) {
                raiseEvent = false;
                this._checkSessionIFrame.start(session.session_state);
                logger2.debug("same sub still logged in at OP, session state has changed, restarting check session iframe; session_state", session.session_state);
                await this._userManager.events._raiseUserSessionChanged();
              } else {
                logger2.debug("different subject signed into OP", session.sub);
              }
            } else {
              logger2.debug("subject no longer signed into OP");
            }
            if (raiseEvent) {
              if (this._sub) {
                await this._userManager.events._raiseUserSignedOut();
              } else {
                await this._userManager.events._raiseUserSignedIn();
              }
            } else {
              logger2.debug("no change in session detected, no event to raise");
            }
          } catch (err) {
            if (this._sub) {
              logger2.debug("Error calling queryCurrentSigninSession; raising signed out event", err);
              await this._userManager.events._raiseUserSignedOut();
            }
          }
        };
        if (!_userManager) {
          this._logger.throw(new Error("No user manager passed"));
        }
        this._userManager.events.addUserLoaded(this._start);
        this._userManager.events.addUserUnloaded(this._stop);
        this._init().catch((err) => {
          this._logger.error(err);
        });
      }
      async _init() {
        this._logger.create("_init");
        const user = await this._userManager.getUser();
        if (user) {
          void this._start(user);
        } else if (this._userManager.settings.monitorAnonymousSession) {
          const session = await this._userManager.querySessionStatus();
          if (session) {
            const tmpUser = {
              session_state: session.session_state,
              profile: session.sub ? {
                sub: session.sub
              } : null
            };
            void this._start(tmpUser);
          }
        }
      }
    };
    var User = class _User {
      constructor(args) {
        var _a;
        this.id_token = args.id_token;
        this.session_state = (_a = args.session_state) != null ? _a : null;
        this.access_token = args.access_token;
        this.refresh_token = args.refresh_token;
        this.token_type = args.token_type;
        this.scope = args.scope;
        this.profile = args.profile;
        this.expires_at = args.expires_at;
        this.state = args.userState;
        this.url_state = args.url_state;
      }
      /** Computed number of seconds the access token has remaining. */
      get expires_in() {
        if (this.expires_at === void 0) {
          return void 0;
        }
        return this.expires_at - Timer.getEpochTime();
      }
      set expires_in(value) {
        if (value !== void 0) {
          this.expires_at = Math.floor(value) + Timer.getEpochTime();
        }
      }
      /** Computed value indicating if the access token is expired. */
      get expired() {
        const expires_in = this.expires_in;
        if (expires_in === void 0) {
          return void 0;
        }
        return expires_in <= 0;
      }
      /** Array representing the parsed values from the `scope`. */
      get scopes() {
        var _a, _b;
        return (_b = (_a = this.scope) == null ? void 0 : _a.split(" ")) != null ? _b : [];
      }
      toStorageString() {
        new Logger("User").create("toStorageString");
        return JSON.stringify({
          id_token: this.id_token,
          session_state: this.session_state,
          access_token: this.access_token,
          refresh_token: this.refresh_token,
          token_type: this.token_type,
          scope: this.scope,
          profile: this.profile,
          expires_at: this.expires_at
        });
      }
      static fromStorageString(storageString) {
        Logger.createStatic("User", "fromStorageString");
        return new _User(JSON.parse(storageString));
      }
    };
    var messageSource = "oidc-client";
    var AbstractChildWindow = class {
      constructor() {
        this._abort = new Event("Window navigation aborted");
        this._disposeHandlers = /* @__PURE__ */ new Set();
        this._window = null;
      }
      async navigate(params) {
        const logger2 = this._logger.create("navigate");
        if (!this._window) {
          throw new Error("Attempted to navigate on a disposed window");
        }
        logger2.debug("setting URL in window");
        this._window.location.replace(params.url);
        const { url, keepOpen } = await new Promise((resolve, reject) => {
          const listener = (e) => {
            var _a;
            const data = e.data;
            const origin = (_a = params.scriptOrigin) != null ? _a : window.location.origin;
            if (e.origin !== origin || (data == null ? void 0 : data.source) !== messageSource) {
              return;
            }
            try {
              const state = UrlUtils.readParams(data.url, params.response_mode).get("state");
              if (!state) {
                logger2.warn("no state found in response url");
              }
              if (e.source !== this._window && state !== params.state) {
                return;
              }
            } catch {
              this._dispose();
              reject(new Error("Invalid response from window"));
            }
            resolve(data);
          };
          window.addEventListener("message", listener, false);
          this._disposeHandlers.add(() => window.removeEventListener("message", listener, false));
          const channel = new BroadcastChannel(`oidc-client-popup-${params.state}`);
          channel.addEventListener("message", listener, false);
          this._disposeHandlers.add(() => channel.close());
          this._disposeHandlers.add(this._abort.addHandler((reason) => {
            this._dispose();
            reject(reason);
          }));
        });
        logger2.debug("got response from window");
        this._dispose();
        if (!keepOpen) {
          this.close();
        }
        return { url };
      }
      _dispose() {
        this._logger.create("_dispose");
        for (const dispose of this._disposeHandlers) {
          dispose();
        }
        this._disposeHandlers.clear();
      }
      static _notifyParent(parent, url, keepOpen = false, targetOrigin = window.location.origin) {
        const msgData = {
          source: messageSource,
          url,
          keepOpen
        };
        const logger2 = new Logger("_notifyParent");
        if (parent) {
          logger2.debug("With parent. Using parent.postMessage.");
          parent.postMessage(msgData, targetOrigin);
        } else {
          logger2.debug("No parent. Using BroadcastChannel.");
          const state = new URL(url).searchParams.get("state");
          if (!state) {
            throw new Error("No parent and no state in URL. Can't complete notification.");
          }
          const channel = new BroadcastChannel(`oidc-client-popup-${state}`);
          channel.postMessage(msgData);
          channel.close();
        }
      }
    };
    var DefaultPopupWindowFeatures = {
      location: false,
      toolbar: false,
      height: 640,
      closePopupWindowAfterInSeconds: -1
    };
    var DefaultPopupTarget = "_blank";
    var DefaultAccessTokenExpiringNotificationTimeInSeconds = 60;
    var DefaultCheckSessionIntervalInSeconds = 2;
    var DefaultSilentRequestTimeoutInSeconds = 10;
    var UserManagerSettingsStore = class extends OidcClientSettingsStore {
      constructor(args) {
        const {
          popup_redirect_uri = args.redirect_uri,
          popup_post_logout_redirect_uri = args.post_logout_redirect_uri,
          popupWindowFeatures = DefaultPopupWindowFeatures,
          popupWindowTarget = DefaultPopupTarget,
          redirectMethod = "assign",
          redirectTarget = "self",
          iframeNotifyParentOrigin = args.iframeNotifyParentOrigin,
          iframeScriptOrigin = args.iframeScriptOrigin,
          requestTimeoutInSeconds,
          silent_redirect_uri = args.redirect_uri,
          silentRequestTimeoutInSeconds,
          automaticSilentRenew = true,
          validateSubOnSilentRenew = true,
          includeIdTokenInSilentRenew = false,
          monitorSession = false,
          monitorAnonymousSession = false,
          checkSessionIntervalInSeconds = DefaultCheckSessionIntervalInSeconds,
          query_status_response_type = "code",
          stopCheckSessionOnError = true,
          revokeTokenTypes = ["access_token", "refresh_token"],
          revokeTokensOnSignout = false,
          includeIdTokenInSilentSignout = false,
          accessTokenExpiringNotificationTimeInSeconds = DefaultAccessTokenExpiringNotificationTimeInSeconds,
          maxSilentRenewTimeoutRetries,
          userStore
        } = args;
        super(args);
        this.popup_redirect_uri = popup_redirect_uri;
        this.popup_post_logout_redirect_uri = popup_post_logout_redirect_uri;
        this.popupWindowFeatures = popupWindowFeatures;
        this.popupWindowTarget = popupWindowTarget;
        this.redirectMethod = redirectMethod;
        this.redirectTarget = redirectTarget;
        this.iframeNotifyParentOrigin = iframeNotifyParentOrigin;
        this.iframeScriptOrigin = iframeScriptOrigin;
        this.silent_redirect_uri = silent_redirect_uri;
        this.silentRequestTimeoutInSeconds = silentRequestTimeoutInSeconds || requestTimeoutInSeconds || DefaultSilentRequestTimeoutInSeconds;
        this.automaticSilentRenew = automaticSilentRenew;
        this.validateSubOnSilentRenew = validateSubOnSilentRenew;
        this.includeIdTokenInSilentRenew = includeIdTokenInSilentRenew;
        this.monitorSession = monitorSession;
        this.monitorAnonymousSession = monitorAnonymousSession;
        this.checkSessionIntervalInSeconds = checkSessionIntervalInSeconds;
        this.stopCheckSessionOnError = stopCheckSessionOnError;
        this.query_status_response_type = query_status_response_type;
        this.revokeTokenTypes = revokeTokenTypes;
        this.revokeTokensOnSignout = revokeTokensOnSignout;
        this.includeIdTokenInSilentSignout = includeIdTokenInSilentSignout;
        this.accessTokenExpiringNotificationTimeInSeconds = accessTokenExpiringNotificationTimeInSeconds;
        this.maxSilentRenewTimeoutRetries = maxSilentRenewTimeoutRetries;
        if (userStore) {
          this.userStore = userStore;
        } else {
          const store = typeof window !== "undefined" ? window.sessionStorage : new InMemoryWebStorage();
          this.userStore = new WebStorageStateStore({ store });
        }
      }
    };
    var IFrameWindow = class _IFrameWindow extends AbstractChildWindow {
      constructor({
        silentRequestTimeoutInSeconds = DefaultSilentRequestTimeoutInSeconds
      }) {
        super();
        this._logger = new Logger("IFrameWindow");
        this._timeoutInSeconds = silentRequestTimeoutInSeconds;
        this._frame = _IFrameWindow.createHiddenIframe();
        this._window = this._frame.contentWindow;
      }
      static createHiddenIframe() {
        const iframe = window.document.createElement("iframe");
        iframe.style.visibility = "hidden";
        iframe.style.position = "fixed";
        iframe.style.left = "-1000px";
        iframe.style.top = "0";
        iframe.width = "0";
        iframe.height = "0";
        window.document.body.appendChild(iframe);
        return iframe;
      }
      async navigate(params) {
        this._logger.debug("navigate: Using timeout of:", this._timeoutInSeconds);
        const timer = setTimeout(() => void this._abort.raise(new ErrorTimeout("IFrame timed out without a response")), this._timeoutInSeconds * 1e3);
        this._disposeHandlers.add(() => clearTimeout(timer));
        return await super.navigate(params);
      }
      close() {
        var _a;
        if (this._frame) {
          if (this._frame.parentNode) {
            this._frame.addEventListener("load", (ev) => {
              var _a2;
              const frame = ev.target;
              (_a2 = frame.parentNode) == null ? void 0 : _a2.removeChild(frame);
              void this._abort.raise(new Error("IFrame removed from DOM"));
            }, true);
            (_a = this._frame.contentWindow) == null ? void 0 : _a.location.replace("about:blank");
          }
          this._frame = null;
        }
        this._window = null;
      }
      static notifyParent(url, targetOrigin) {
        return super._notifyParent(window.parent, url, false, targetOrigin);
      }
    };
    var IFrameNavigator = class {
      constructor(_settings) {
        this._settings = _settings;
        this._logger = new Logger("IFrameNavigator");
      }
      async prepare({
        silentRequestTimeoutInSeconds = this._settings.silentRequestTimeoutInSeconds
      }) {
        return new IFrameWindow({ silentRequestTimeoutInSeconds });
      }
      async callback(url) {
        this._logger.create("callback");
        IFrameWindow.notifyParent(url, this._settings.iframeNotifyParentOrigin);
      }
    };
    var checkForPopupClosedInterval = 500;
    var second = 1e3;
    var PopupWindow = class extends AbstractChildWindow {
      constructor({
        popupWindowTarget = DefaultPopupTarget,
        popupWindowFeatures = {},
        popupSignal,
        popupAbortOnClose
      }) {
        super();
        this._logger = new Logger("PopupWindow");
        const centeredPopup = PopupUtils.center({ ...DefaultPopupWindowFeatures, ...popupWindowFeatures });
        this._window = window.open(void 0, popupWindowTarget, PopupUtils.serialize(centeredPopup));
        this.abortOnClose = Boolean(popupAbortOnClose);
        if (popupSignal) {
          popupSignal.addEventListener("abort", () => {
            var _a;
            void this._abort.raise(new Error((_a = popupSignal.reason) != null ? _a : "Popup aborted"));
          });
        }
        if (popupWindowFeatures.closePopupWindowAfterInSeconds && popupWindowFeatures.closePopupWindowAfterInSeconds > 0) {
          setTimeout(() => {
            if (!this._window || typeof this._window.closed !== "boolean" || this._window.closed) {
              void this._abort.raise(new Error("Popup blocked by user"));
              return;
            }
            this.close();
          }, popupWindowFeatures.closePopupWindowAfterInSeconds * second);
        }
      }
      async navigate(params) {
        var _a;
        (_a = this._window) == null ? void 0 : _a.focus();
        const popupClosedInterval = setInterval(() => {
          if (!this._window || this._window.closed) {
            this._logger.debug("Popup closed by user or isolated by redirect");
            clearPopupClosedInterval();
            this._disposeHandlers.delete(clearPopupClosedInterval);
            if (this.abortOnClose) {
              void this._abort.raise(new Error("Popup closed by user"));
            }
          }
        }, checkForPopupClosedInterval);
        const clearPopupClosedInterval = () => clearInterval(popupClosedInterval);
        this._disposeHandlers.add(clearPopupClosedInterval);
        return await super.navigate(params);
      }
      close() {
        if (this._window) {
          if (!this._window.closed) {
            this._window.close();
            void this._abort.raise(new Error("Popup closed"));
          }
        }
        this._window = null;
      }
      static notifyOpener(url, keepOpen) {
        super._notifyParent(window.opener, url, keepOpen);
        if (!keepOpen && !window.opener) {
          window.close();
        }
      }
    };
    var PopupNavigator = class {
      constructor(_settings) {
        this._settings = _settings;
        this._logger = new Logger("PopupNavigator");
      }
      async prepare({
        popupWindowFeatures = this._settings.popupWindowFeatures,
        popupWindowTarget = this._settings.popupWindowTarget,
        popupSignal,
        popupAbortOnClose
      }) {
        return new PopupWindow({
          popupWindowFeatures,
          popupWindowTarget,
          popupSignal,
          popupAbortOnClose
        });
      }
      async callback(url, { keepOpen = false }) {
        this._logger.create("callback");
        PopupWindow.notifyOpener(url, keepOpen);
      }
    };
    var RedirectNavigator = class {
      constructor(_settings) {
        this._settings = _settings;
        this._logger = new Logger("RedirectNavigator");
      }
      async prepare({
        redirectMethod = this._settings.redirectMethod,
        redirectTarget = this._settings.redirectTarget
      }) {
        var _a;
        this._logger.create("prepare");
        let targetWindow = window.self;
        if (redirectTarget === "top") {
          targetWindow = (_a = window.top) != null ? _a : window.self;
        }
        const redirect = targetWindow.location[redirectMethod].bind(targetWindow.location);
        let abort;
        return {
          navigate: async (params) => {
            this._logger.create("navigate");
            const promise = new Promise((resolve, reject) => {
              abort = reject;
              window.addEventListener("pageshow", () => resolve(window.location.href));
              redirect(params.url);
            });
            return await promise;
          },
          close: () => {
            this._logger.create("close");
            abort == null ? void 0 : abort(new Error("Redirect aborted"));
            targetWindow.stop();
          }
        };
      }
      async callback() {
        return;
      }
    };
    var UserManagerEvents = class extends AccessTokenEvents {
      constructor(settings) {
        super({ expiringNotificationTimeInSeconds: settings.accessTokenExpiringNotificationTimeInSeconds });
        this._logger = new Logger("UserManagerEvents");
        this._userLoaded = new Event("User loaded");
        this._userUnloaded = new Event("User unloaded");
        this._silentRenewError = new Event("Silent renew error");
        this._userSignedIn = new Event("User signed in");
        this._userSignedOut = new Event("User signed out");
        this._userSessionChanged = new Event("User session changed");
      }
      async load(user, raiseEvent = true) {
        await super.load(user);
        if (raiseEvent) {
          await this._userLoaded.raise(user);
        }
      }
      async unload() {
        await super.unload();
        await this._userUnloaded.raise();
      }
      /**
       * Add callback: Raised when a user session has been established (or re-established).
       */
      addUserLoaded(cb) {
        return this._userLoaded.addHandler(cb);
      }
      /**
       * Remove callback: Raised when a user session has been established (or re-established).
       */
      removeUserLoaded(cb) {
        return this._userLoaded.removeHandler(cb);
      }
      /**
       * Add callback: Raised when a user session has been terminated.
       */
      addUserUnloaded(cb) {
        return this._userUnloaded.addHandler(cb);
      }
      /**
       * Remove callback: Raised when a user session has been terminated.
       */
      removeUserUnloaded(cb) {
        return this._userUnloaded.removeHandler(cb);
      }
      /**
       * Add callback: Raised when the automatic silent renew has failed.
       */
      addSilentRenewError(cb) {
        return this._silentRenewError.addHandler(cb);
      }
      /**
       * Remove callback: Raised when the automatic silent renew has failed.
       */
      removeSilentRenewError(cb) {
        return this._silentRenewError.removeHandler(cb);
      }
      /**
       * @internal
       */
      async _raiseSilentRenewError(e) {
        await this._silentRenewError.raise(e);
      }
      /**
       * Add callback: Raised when the user is signed in (when `monitorSession` is set).
       * @see {@link UserManagerSettings.monitorSession}
       */
      addUserSignedIn(cb) {
        return this._userSignedIn.addHandler(cb);
      }
      /**
       * Remove callback: Raised when the user is signed in (when `monitorSession` is set).
       */
      removeUserSignedIn(cb) {
        this._userSignedIn.removeHandler(cb);
      }
      /**
       * @internal
       */
      async _raiseUserSignedIn() {
        await this._userSignedIn.raise();
      }
      /**
       * Add callback: Raised when the user's sign-in status at the OP has changed (when `monitorSession` is set).
       * @see {@link UserManagerSettings.monitorSession}
       */
      addUserSignedOut(cb) {
        return this._userSignedOut.addHandler(cb);
      }
      /**
       * Remove callback: Raised when the user's sign-in status at the OP has changed (when `monitorSession` is set).
       */
      removeUserSignedOut(cb) {
        this._userSignedOut.removeHandler(cb);
      }
      /**
       * @internal
       */
      async _raiseUserSignedOut() {
        await this._userSignedOut.raise();
      }
      /**
       * Add callback: Raised when the user session changed (when `monitorSession` is set).
       * @see {@link UserManagerSettings.monitorSession}
       */
      addUserSessionChanged(cb) {
        return this._userSessionChanged.addHandler(cb);
      }
      /**
       * Remove callback: Raised when the user session changed (when `monitorSession` is set).
       */
      removeUserSessionChanged(cb) {
        this._userSessionChanged.removeHandler(cb);
      }
      /**
       * @internal
       */
      async _raiseUserSessionChanged() {
        await this._userSessionChanged.raise();
      }
    };
    var SilentRenewService = class {
      constructor(_userManager) {
        this._userManager = _userManager;
        this._logger = new Logger("SilentRenewService");
        this._isStarted = false;
        this._retryTimer = new Timer("Retry Silent Renew");
        this._timeoutRetryCount = 0;
        this._tokenExpiring = async () => {
          const logger2 = this._logger.create("_tokenExpiring");
          try {
            await this._userManager.signinSilent();
            this._timeoutRetryCount = 0;
            logger2.debug("silent token renewal successful");
          } catch (err) {
            if (err instanceof ErrorTimeout) {
              this._timeoutRetryCount++;
              const maxRetries = this._userManager.settings.maxSilentRenewTimeoutRetries;
              const hasReachedLimit = maxRetries !== void 0 && this._timeoutRetryCount > maxRetries;
              if (hasReachedLimit) {
                logger2.error(
                  `Timeout retry limit reached (${this._timeoutRetryCount} > ${maxRetries}), raising silentRenewError:`,
                  err
                );
                this._timeoutRetryCount = 0;
                await this._userManager.events._raiseSilentRenewError(err);
                return;
              }
              logger2.warn(
                `ErrorTimeout from signinSilent (attempt ${this._timeoutRetryCount}), retry in 5s:`,
                err
              );
              this._retryTimer.init(5);
              return;
            }
            logger2.error("Error from signinSilent:", err);
            this._timeoutRetryCount = 0;
            await this._userManager.events._raiseSilentRenewError(err);
          }
        };
      }
      async start() {
        const logger2 = this._logger.create("start");
        if (!this._isStarted) {
          this._isStarted = true;
          this._userManager.events.addAccessTokenExpiring(this._tokenExpiring);
          this._retryTimer.addHandler(this._tokenExpiring);
          try {
            await this._userManager.getUser();
          } catch (err) {
            logger2.error("getUser error", err);
          }
        }
      }
      stop() {
        if (this._isStarted) {
          this._retryTimer.cancel();
          this._retryTimer.removeHandler(this._tokenExpiring);
          this._userManager.events.removeAccessTokenExpiring(this._tokenExpiring);
          this._isStarted = false;
        }
      }
    };
    var RefreshState = class {
      constructor(args) {
        this.refresh_token = args.refresh_token;
        this.id_token = args.id_token;
        this.session_state = args.session_state;
        this.scope = args.scope;
        this.profile = args.profile;
        this.data = args.state;
      }
    };
    var UserManager = class {
      constructor(settings, redirectNavigator, popupNavigator, iframeNavigator) {
        this._logger = new Logger("UserManager");
        this.settings = new UserManagerSettingsStore(settings);
        this._client = new OidcClient(settings);
        this._redirectNavigator = redirectNavigator != null ? redirectNavigator : new RedirectNavigator(this.settings);
        this._popupNavigator = popupNavigator != null ? popupNavigator : new PopupNavigator(this.settings);
        this._iframeNavigator = iframeNavigator != null ? iframeNavigator : new IFrameNavigator(this.settings);
        this._events = new UserManagerEvents(this.settings);
        this._silentRenewService = new SilentRenewService(this);
        if (this.settings.automaticSilentRenew) {
          this.startSilentRenew();
        }
        this._sessionMonitor = null;
        if (this.settings.monitorSession) {
          this._sessionMonitor = new SessionMonitor(this);
        }
      }
      /**
       * Get object used to register for events raised by the `UserManager`.
       */
      get events() {
        return this._events;
      }
      /**
       * Get object used to access the metadata configuration of the identity provider.
       */
      get metadataService() {
        return this._client.metadataService;
      }
      /**
       * Load the `User` object for the currently authenticated user.
       *
       * @param raiseEvent - If `true`, the `UserLoaded` event will be raised. Defaults to false.
       * @returns A promise
       */
      async getUser(raiseEvent = false) {
        const logger2 = this._logger.create("getUser");
        const user = await this._loadUser();
        if (user) {
          logger2.info("user loaded");
          await this._events.load(user, raiseEvent);
          return user;
        }
        logger2.info("user not found in storage");
        return null;
      }
      /**
       * Remove from any storage the currently authenticated user.
       *
       * @returns A promise
       */
      async removeUser() {
        const logger2 = this._logger.create("removeUser");
        await this.storeUser(null);
        logger2.info("user removed from storage");
        await this._events.unload();
      }
      /**
       * Trigger a redirect of the current window to the authorization endpoint.
       *
       * @returns A promise
       *
       * @throws `Error` In cases of wrong authentication.
       */
      async signinRedirect(args = {}) {
        var _a;
        this._logger.create("signinRedirect");
        const {
          redirectMethod,
          ...requestArgs
        } = args;
        let dpopJkt;
        if ((_a = this.settings.dpop) == null ? void 0 : _a.bind_authorization_code) {
          dpopJkt = await this.generateDPoPJkt(this.settings.dpop);
        }
        const handle = await this._redirectNavigator.prepare({ redirectMethod });
        await this._signinStart({
          request_type: "si:r",
          dpopJkt,
          ...requestArgs
        }, handle);
      }
      /**
       * Process the response (callback) from the authorization endpoint.
       * It is recommended to use {@link UserManager.signinCallback} instead.
       *
       * @returns A promise containing the authenticated `User`.
       *
       * @see {@link UserManager.signinCallback}
       */
      async signinRedirectCallback(url = window.location.href) {
        const logger2 = this._logger.create("signinRedirectCallback");
        const user = await this._signinEnd(url);
        if (user.profile && user.profile.sub) {
          logger2.info("success, signed in subject", user.profile.sub);
        } else {
          logger2.info("no subject");
        }
        return user;
      }
      /**
       * Trigger the signin with user/password.
       *
       * @returns A promise containing the authenticated `User`.
       * @throws {@link ErrorResponse} In cases of wrong authentication.
       */
      async signinResourceOwnerCredentials({
        username,
        password,
        skipUserInfo = false
      }) {
        const logger2 = this._logger.create("signinResourceOwnerCredential");
        const signinResponse = await this._client.processResourceOwnerPasswordCredentials({
          username,
          password,
          skipUserInfo,
          extraTokenParams: this.settings.extraTokenParams
        });
        logger2.debug("got signin response");
        const user = await this._buildUser(signinResponse);
        if (user.profile && user.profile.sub) {
          logger2.info("success, signed in subject", user.profile.sub);
        } else {
          logger2.info("no subject");
        }
        return user;
      }
      /**
       * Trigger a request (via a popup window) to the authorization endpoint.
       *
       * @returns A promise containing the authenticated `User`.
       * @throws `Error` In cases of wrong authentication.
       */
      async signinPopup(args = {}) {
        var _a;
        const logger2 = this._logger.create("signinPopup");
        let dpopJkt;
        if ((_a = this.settings.dpop) == null ? void 0 : _a.bind_authorization_code) {
          dpopJkt = await this.generateDPoPJkt(this.settings.dpop);
        }
        const {
          popupWindowFeatures,
          popupWindowTarget,
          popupSignal,
          popupAbortOnClose,
          ...requestArgs
        } = args;
        const url = this.settings.popup_redirect_uri;
        if (!url) {
          logger2.throw(new Error("No popup_redirect_uri configured"));
        }
        const handle = await this._popupNavigator.prepare({ popupWindowFeatures, popupWindowTarget, popupSignal, popupAbortOnClose });
        const user = await this._signin({
          request_type: "si:p",
          redirect_uri: url,
          display: "popup",
          dpopJkt,
          ...requestArgs
        }, handle);
        if (user) {
          if (user.profile && user.profile.sub) {
            logger2.info("success, signed in subject", user.profile.sub);
          } else {
            logger2.info("no subject");
          }
        }
        return user;
      }
      /**
       * Notify the opening window of response (callback) from the authorization endpoint.
       * It is recommended to use {@link UserManager.signinCallback} instead.
       *
       * @returns A promise
       *
       * @see {@link UserManager.signinCallback}
       */
      async signinPopupCallback(url = window.location.href, keepOpen = false) {
        const logger2 = this._logger.create("signinPopupCallback");
        await this._popupNavigator.callback(url, { keepOpen });
        logger2.info("success");
      }
      /**
       * Trigger a silent request (via refresh token or an iframe) to the authorization endpoint.
       *
       * @returns A promise that contains the authenticated `User`.
       */
      async signinSilent(args = {}) {
        var _a, _b;
        const logger2 = this._logger.create("signinSilent");
        const {
          silentRequestTimeoutInSeconds,
          ...requestArgs
        } = args;
        let user = await this._loadUser();
        if (!args.forceIframeAuth && (user == null ? void 0 : user.refresh_token)) {
          logger2.debug("using refresh token");
          const state = new RefreshState(user);
          return await this._useRefreshToken({
            state,
            redirect_uri: requestArgs.redirect_uri,
            resource: requestArgs.resource,
            extraTokenParams: requestArgs.extraTokenParams,
            timeoutInSeconds: silentRequestTimeoutInSeconds
          });
        }
        let dpopJkt;
        if ((_a = this.settings.dpop) == null ? void 0 : _a.bind_authorization_code) {
          dpopJkt = await this.generateDPoPJkt(this.settings.dpop);
        }
        const url = this.settings.silent_redirect_uri;
        if (!url) {
          logger2.throw(new Error("No silent_redirect_uri configured"));
        }
        let verifySub;
        if (user && this.settings.validateSubOnSilentRenew) {
          logger2.debug("subject prior to silent renew:", user.profile.sub);
          verifySub = user.profile.sub;
        }
        const handle = await this._iframeNavigator.prepare({ silentRequestTimeoutInSeconds });
        user = await this._signin({
          request_type: "si:s",
          redirect_uri: url,
          prompt: "none",
          id_token_hint: this.settings.includeIdTokenInSilentRenew ? user == null ? void 0 : user.id_token : void 0,
          dpopJkt,
          ...requestArgs
        }, handle, verifySub);
        if (user) {
          if ((_b = user.profile) == null ? void 0 : _b.sub) {
            logger2.info("success, signed in subject", user.profile.sub);
          } else {
            logger2.info("no subject");
          }
        }
        return user;
      }
      async _useRefreshToken(args) {
        const response = await this._client.useRefreshToken({
          timeoutInSeconds: this.settings.silentRequestTimeoutInSeconds,
          ...args
        });
        const user = new User({ ...args.state, ...response });
        await this.storeUser(user);
        await this._events.load(user);
        return user;
      }
      /**
       *
       * Notify the parent window of response (callback) from the authorization endpoint.
       * It is recommended to use {@link UserManager.signinCallback} instead.
       *
       * @returns A promise
       *
       * @see {@link UserManager.signinCallback}
       */
      async signinSilentCallback(url = window.location.href) {
        const logger2 = this._logger.create("signinSilentCallback");
        await this._iframeNavigator.callback(url);
        logger2.info("success");
      }
      /**
       * Process any response (callback) from the authorization endpoint, by dispatching the request_type
       * and executing one of the following functions:
       * - {@link UserManager.signinRedirectCallback}
       * - {@link UserManager.signinPopupCallback}
       * - {@link UserManager.signinSilentCallback}
       *
       * @throws `Error` If request_type is unknown or signin cannot be processed.
       */
      async signinCallback(url = window.location.href) {
        const { state } = await this._client.readSigninResponseState(url);
        switch (state.request_type) {
          case "si:r":
            return await this.signinRedirectCallback(url);
          case "si:p":
            await this.signinPopupCallback(url);
            break;
          case "si:s":
            await this.signinSilentCallback(url);
            break;
          default:
            throw new Error("invalid request_type in state");
        }
        return void 0;
      }
      /**
       * Process any response (callback) from the end session endpoint, by dispatching the request_type
       * and executing one of the following functions:
       * - {@link UserManager.signoutRedirectCallback}
       * - {@link UserManager.signoutPopupCallback}
       * - {@link UserManager.signoutSilentCallback}
       *
       * @throws `Error` If request_type is unknown or signout cannot be processed.
       */
      async signoutCallback(url = window.location.href, keepOpen = false) {
        const { state } = await this._client.readSignoutResponseState(url);
        if (!state) {
          return void 0;
        }
        switch (state.request_type) {
          case "so:r":
            return await this.signoutRedirectCallback(url);
          case "so:p":
            await this.signoutPopupCallback(url, keepOpen);
            break;
          case "so:s":
            await this.signoutSilentCallback(url);
            break;
          default:
            throw new Error("invalid request_type in state");
        }
        return void 0;
      }
      /**
       * Query OP for user's current signin status.
       *
       * @returns A promise object with session_state and subject identifier.
       */
      async querySessionStatus(args = {}) {
        const logger2 = this._logger.create("querySessionStatus");
        const {
          silentRequestTimeoutInSeconds,
          ...requestArgs
        } = args;
        const url = this.settings.silent_redirect_uri;
        if (!url) {
          logger2.throw(new Error("No silent_redirect_uri configured"));
        }
        const user = await this._loadUser();
        const handle = await this._iframeNavigator.prepare({ silentRequestTimeoutInSeconds });
        const navResponse = await this._signinStart({
          request_type: "si:s",
          // this acts like a signin silent
          redirect_uri: url,
          prompt: "none",
          id_token_hint: this.settings.includeIdTokenInSilentRenew ? user == null ? void 0 : user.id_token : void 0,
          response_type: this.settings.query_status_response_type,
          scope: "openid",
          skipUserInfo: true,
          ...requestArgs
        }, handle);
        try {
          const extraHeaders = {};
          const signinResponse = await this._client.processSigninResponse(navResponse.url, extraHeaders);
          logger2.debug("got signin response");
          if (signinResponse.session_state && signinResponse.profile.sub) {
            logger2.info("success for subject", signinResponse.profile.sub);
            return {
              session_state: signinResponse.session_state,
              sub: signinResponse.profile.sub
            };
          }
          logger2.info("success, user not authenticated");
          return null;
        } catch (err) {
          if (this.settings.monitorAnonymousSession && err instanceof ErrorResponse) {
            switch (err.error) {
              case "login_required":
              case "consent_required":
              case "interaction_required":
              case "account_selection_required":
                logger2.info("success for anonymous user");
                return {
                  session_state: err.session_state
                };
            }
          }
          throw err;
        }
      }
      async _signin(args, handle, verifySub) {
        const navResponse = await this._signinStart(args, handle);
        return await this._signinEnd(navResponse.url, verifySub);
      }
      async _signinStart(args, handle) {
        const logger2 = this._logger.create("_signinStart");
        try {
          const signinRequest = await this._client.createSigninRequest(args);
          logger2.debug("got signin request");
          return await handle.navigate({
            url: signinRequest.url,
            state: signinRequest.state.id,
            response_mode: signinRequest.state.response_mode,
            scriptOrigin: this.settings.iframeScriptOrigin
          });
        } catch (err) {
          logger2.debug("error after preparing navigator, closing navigator window");
          handle.close();
          throw err;
        }
      }
      async _signinEnd(url, verifySub) {
        const logger2 = this._logger.create("_signinEnd");
        const extraHeaders = {};
        const signinResponse = await this._client.processSigninResponse(url, extraHeaders);
        logger2.debug("got signin response");
        const user = await this._buildUser(signinResponse, verifySub);
        return user;
      }
      async _buildUser(signinResponse, verifySub) {
        const logger2 = this._logger.create("_buildUser");
        const user = new User(signinResponse);
        if (verifySub) {
          if (verifySub !== user.profile.sub) {
            logger2.debug("current user does not match user returned from signin. sub from signin:", user.profile.sub);
            throw new ErrorResponse({ ...signinResponse, error: "login_required" });
          }
          logger2.debug("current user matches user returned from signin");
        }
        await this.storeUser(user);
        logger2.debug("user stored");
        await this._events.load(user);
        return user;
      }
      /**
       * Trigger a redirect of the current window to the end session endpoint.
       *
       * @returns A promise
       */
      async signoutRedirect(args = {}) {
        const logger2 = this._logger.create("signoutRedirect");
        const {
          redirectMethod,
          ...requestArgs
        } = args;
        const handle = await this._redirectNavigator.prepare({ redirectMethod });
        await this._signoutStart({
          request_type: "so:r",
          post_logout_redirect_uri: this.settings.post_logout_redirect_uri,
          ...requestArgs
        }, handle);
        logger2.info("success");
      }
      /**
       * Process response (callback) from the end session endpoint.
       * It is recommended to use {@link UserManager.signoutCallback} instead.
       *
       * @returns A promise containing signout response
       *
       * @see {@link UserManager.signoutCallback}
       */
      async signoutRedirectCallback(url = window.location.href) {
        const logger2 = this._logger.create("signoutRedirectCallback");
        const response = await this._signoutEnd(url);
        logger2.info("success");
        return response;
      }
      /**
       * Trigger a redirect of a popup window to the end session endpoint.
       *
       * @returns A promise
       */
      async signoutPopup(args = {}) {
        const logger2 = this._logger.create("signoutPopup");
        const {
          popupWindowFeatures,
          popupWindowTarget,
          popupSignal,
          ...requestArgs
        } = args;
        const url = this.settings.popup_post_logout_redirect_uri;
        const handle = await this._popupNavigator.prepare({ popupWindowFeatures, popupWindowTarget, popupSignal });
        await this._signout({
          request_type: "so:p",
          post_logout_redirect_uri: url,
          // we're putting a dummy entry in here because we
          // need a unique id from the state for notification
          // to the parent window, which is necessary if we
          // plan to return back to the client after signout
          // and so we can close the popup after signout
          state: url == null ? void 0 : {},
          ...requestArgs
        }, handle);
        logger2.info("success");
      }
      /**
       * Process response (callback) from the end session endpoint from a popup window.
       * It is recommended to use {@link UserManager.signoutCallback} instead.
       *
       * @returns A promise
       *
       * @see {@link UserManager.signoutCallback}
       */
      async signoutPopupCallback(url = window.location.href, keepOpen = false) {
        const logger2 = this._logger.create("signoutPopupCallback");
        await this._popupNavigator.callback(url, { keepOpen });
        logger2.info("success");
      }
      async _signout(args, handle) {
        const navResponse = await this._signoutStart(args, handle);
        return await this._signoutEnd(navResponse.url);
      }
      async _signoutStart(args = {}, handle) {
        var _a;
        const logger2 = this._logger.create("_signoutStart");
        try {
          const user = await this._loadUser();
          logger2.debug("loaded current user from storage");
          if (this.settings.revokeTokensOnSignout) {
            await this._revokeInternal(user);
          }
          const id_token = args.id_token_hint || user && user.id_token;
          if (id_token) {
            logger2.debug("setting id_token_hint in signout request");
            args.id_token_hint = id_token;
          }
          await this.removeUser();
          logger2.debug("user removed, creating signout request");
          const signoutRequest = await this._client.createSignoutRequest(args);
          logger2.debug("got signout request");
          return await handle.navigate({
            url: signoutRequest.url,
            state: (_a = signoutRequest.state) == null ? void 0 : _a.id,
            scriptOrigin: this.settings.iframeScriptOrigin
          });
        } catch (err) {
          logger2.debug("error after preparing navigator, closing navigator window");
          handle.close();
          throw err;
        }
      }
      async _signoutEnd(url) {
        const logger2 = this._logger.create("_signoutEnd");
        const signoutResponse = await this._client.processSignoutResponse(url);
        logger2.debug("got signout response");
        return signoutResponse;
      }
      /**
       * Trigger a silent request (via an iframe) to the end session endpoint.
       *
       * @returns A promise
       */
      async signoutSilent(args = {}) {
        var _a;
        const logger2 = this._logger.create("signoutSilent");
        const {
          silentRequestTimeoutInSeconds,
          ...requestArgs
        } = args;
        const id_token_hint = this.settings.includeIdTokenInSilentSignout ? (_a = await this._loadUser()) == null ? void 0 : _a.id_token : void 0;
        const url = this.settings.popup_post_logout_redirect_uri;
        const handle = await this._iframeNavigator.prepare({ silentRequestTimeoutInSeconds });
        await this._signout({
          request_type: "so:s",
          post_logout_redirect_uri: url,
          id_token_hint,
          ...requestArgs
        }, handle);
        logger2.info("success");
      }
      /**
       * Notify the parent window of response (callback) from the end session endpoint.
       * It is recommended to use {@link UserManager.signoutCallback} instead.
       *
       * @returns A promise
       *
       * @see {@link UserManager.signoutCallback}
       */
      async signoutSilentCallback(url = window.location.href) {
        const logger2 = this._logger.create("signoutSilentCallback");
        await this._iframeNavigator.callback(url);
        logger2.info("success");
      }
      async revokeTokens(types2) {
        const user = await this._loadUser();
        await this._revokeInternal(user, types2);
      }
      async _revokeInternal(user, types2 = this.settings.revokeTokenTypes) {
        const logger2 = this._logger.create("_revokeInternal");
        if (!user) return;
        const typesPresent = types2.filter((type) => typeof user[type] === "string");
        if (!typesPresent.length) {
          logger2.debug("no need to revoke due to no token(s)");
          return;
        }
        for (const type of typesPresent) {
          await this._client.revokeToken(
            user[type],
            type
          );
          logger2.info(`${type} revoked successfully`);
          if (type !== "access_token") {
            user[type] = null;
          }
        }
        await this.storeUser(user);
        logger2.debug("user stored");
        await this._events.load(user);
      }
      /**
       * Enables silent renew for the `UserManager`.
       */
      startSilentRenew() {
        this._logger.create("startSilentRenew");
        void this._silentRenewService.start();
      }
      /**
       * Disables silent renew for the `UserManager`.
       */
      stopSilentRenew() {
        this._silentRenewService.stop();
      }
      get _userStoreKey() {
        return `user:${this.settings.authority}:${this.settings.client_id}`;
      }
      async _loadUser() {
        const logger2 = this._logger.create("_loadUser");
        const storageString = await this.settings.userStore.get(this._userStoreKey);
        if (storageString) {
          logger2.debug("user storageString loaded");
          return User.fromStorageString(storageString);
        }
        logger2.debug("no user storageString");
        return null;
      }
      async storeUser(user) {
        const logger2 = this._logger.create("storeUser");
        if (user) {
          logger2.debug("storing user");
          const storageString = user.toStorageString();
          await this.settings.userStore.set(this._userStoreKey, storageString);
        } else {
          this._logger.debug("removing user");
          await this.settings.userStore.remove(this._userStoreKey);
          if (this.settings.dpop) {
            await this.settings.dpop.store.remove(this.settings.client_id);
          }
        }
      }
      /**
       * Removes stale state entries in storage for incomplete authorize requests.
       */
      async clearStaleState() {
        await this._client.clearStaleState();
      }
      /**
       * Dynamically generates a DPoP proof for a given user, URL and optional Http method.
       * This method is useful when you need to make a request to a resource server
       * with fetch or similar, and you need to include a DPoP proof in a DPoP header.
       * @param url - The URL to generate the DPoP proof for
       * @param user - The user to generate the DPoP proof for
       * @param httpMethod - Optional, defaults to "GET"
       * @param nonce - Optional nonce provided by the resource server
       *
       * @returns A promise containing the DPoP proof or undefined if DPoP is not enabled/no user is found.
       */
      async dpopProof(url, user, httpMethod, nonce) {
        var _a, _b;
        const dpopState = await ((_b = (_a = this.settings.dpop) == null ? void 0 : _a.store) == null ? void 0 : _b.get(this.settings.client_id));
        if (dpopState) {
          return await CryptoUtils.generateDPoPProof({
            url,
            accessToken: user == null ? void 0 : user.access_token,
            httpMethod,
            keyPair: dpopState.keys,
            nonce
          });
        }
        return void 0;
      }
      async generateDPoPJkt(dpopSettings) {
        let dpopState = await dpopSettings.store.get(this.settings.client_id);
        if (!dpopState) {
          const dpopKeys = await CryptoUtils.generateDPoPKeys();
          dpopState = new DPoPState(dpopKeys);
          await dpopSettings.store.set(this.settings.client_id, dpopState);
        }
        return await CryptoUtils.generateDPoPJkt(dpopState.keys);
      }
    };
    var version = "3.5.0";
    var Version = version;
    var IndexedDbDPoPStore = class {
      constructor() {
        this._dbName = "oidc";
        this._storeName = "dpop";
      }
      async set(key, value) {
        const store = await this.createStore(this._dbName, this._storeName);
        await store("readwrite", (str) => {
          str.put(value, key);
          return this.promisifyRequest(str.transaction);
        });
      }
      async get(key) {
        const store = await this.createStore(this._dbName, this._storeName);
        return await store("readonly", (str) => {
          return this.promisifyRequest(str.get(key));
        });
      }
      async remove(key) {
        const item = await this.get(key);
        const store = await this.createStore(this._dbName, this._storeName);
        await store("readwrite", (str) => {
          return this.promisifyRequest(str.delete(key));
        });
        return item;
      }
      async getAllKeys() {
        const store = await this.createStore(this._dbName, this._storeName);
        return await store("readonly", (str) => {
          return this.promisifyRequest(str.getAllKeys());
        });
      }
      promisifyRequest(request) {
        return new Promise((resolve, reject) => {
          request.oncomplete = request.onsuccess = () => resolve(request.result);
          request.onabort = request.onerror = () => reject(request.error);
        });
      }
      async createStore(dbName, storeName) {
        const request = indexedDB.open(dbName);
        request.onupgradeneeded = () => request.result.createObjectStore(storeName);
        const db = await this.promisifyRequest(request);
        return async (txMode, callback) => {
          const tx = db.transaction(storeName, txMode);
          const store = tx.objectStore(storeName);
          return await callback(store);
        };
      }
    };
  }
});

// node_modules/@inrupt/oidc-client-ext/dist/index.js
var require_dist2 = __commonJS({
  "node_modules/@inrupt/oidc-client-ext/dist/index.js"(exports) {
    "use strict";
    var oidcClientTs = require_oidc_client_ts();
    var solidClientAuthnCore = require_dist();
    function processErrorResponse(responseBody, options) {
      if (responseBody.error === "invalid_redirect_uri") {
        throw new Error(`Dynamic client registration failed: the provided redirect uri [${options.redirectUrl?.toString()}] is invalid - ${responseBody.error_description ?? ""}`);
      }
      if (responseBody.error === "invalid_client_metadata") {
        throw new Error(`Dynamic client registration failed: the provided client metadata ${JSON.stringify(options)} is invalid - ${responseBody.error_description ?? ""}`);
      }
      throw new Error(`Dynamic client registration failed: ${responseBody.error} - ${responseBody.error_description ?? ""}`);
    }
    function hasClientId(body) {
      return typeof body.client_id === "string";
    }
    function hasRedirectUri(body) {
      return Array.isArray(body.redirect_uris) && body.redirect_uris.every((uri) => typeof uri === "string");
    }
    function validateRegistrationResponse(responseBody, options) {
      if (!hasClientId(responseBody)) {
        throw new Error(`Dynamic client registration failed: no client_id has been found on ${JSON.stringify(responseBody)}`);
      }
      if (options.redirectUrl && hasRedirectUri(responseBody) && responseBody.redirect_uris[0] !== options.redirectUrl.toString()) {
        throw new Error(`Dynamic client registration failed: the returned redirect URIs ${JSON.stringify(responseBody.redirect_uris)} don't match the provided ${JSON.stringify([
          options.redirectUrl.toString()
        ])}`);
      }
      return true;
    }
    async function registerClient(options, issuerConfig) {
      if (!issuerConfig.registrationEndpoint) {
        throw new Error("Dynamic Registration could not be completed because the issuer has no registration endpoint.");
      }
      if (!Array.isArray(issuerConfig.idTokenSigningAlgValuesSupported)) {
        throw new Error("The OIDC issuer discovery profile is missing the 'id_token_signing_alg_values_supported' value, which is mandatory.");
      }
      const signingAlg = solidClientAuthnCore.determineSigningAlg(issuerConfig.idTokenSigningAlgValuesSupported, solidClientAuthnCore.PREFERRED_SIGNING_ALG);
      const config = {
        client_name: options.clientName,
        application_type: "web",
        redirect_uris: [options.redirectUrl?.toString()],
        subject_type: "public",
        token_endpoint_auth_method: "client_secret_basic",
        id_token_signed_response_alg: signingAlg,
        grant_types: ["authorization_code", "refresh_token"]
      };
      const headers = {
        "Content-Type": "application/json"
      };
      const registerResponse = await fetch(issuerConfig.registrationEndpoint.toString(), {
        method: "POST",
        headers,
        body: JSON.stringify(config)
      });
      if (registerResponse.ok) {
        const responseBody = await registerResponse.json();
        validateRegistrationResponse(responseBody, options);
        return {
          clientId: responseBody.client_id,
          clientSecret: responseBody.client_secret,
          expiresAt: responseBody.client_secret_expires_at,
          idTokenSignedResponseAlg: responseBody.id_token_signed_response_alg,
          clientType: "dynamic"
        };
      }
      if (registerResponse.status === 400) {
        processErrorResponse(await registerResponse.json(), options);
      }
      throw new Error(`Dynamic client registration failed: the server returned ${registerResponse.status} ${registerResponse.statusText} - ${await registerResponse.text()}`);
    }
    function hasError(value) {
      return value.error !== void 0 && typeof value.error === "string";
    }
    function hasErrorDescription(value) {
      return value.error_description !== void 0 && typeof value.error_description === "string";
    }
    function hasErrorUri(value) {
      return value.error_uri !== void 0 && typeof value.error_uri === "string";
    }
    function hasAccessToken(value) {
      return value.access_token !== void 0 && typeof value.access_token === "string";
    }
    function hasIdToken(value) {
      return value.id_token !== void 0 && typeof value.id_token === "string";
    }
    function hasRefreshToken(value) {
      return value.refresh_token !== void 0 && typeof value.refresh_token === "string";
    }
    function hasTokenType(value) {
      return value.token_type !== void 0 && typeof value.token_type === "string";
    }
    function hasExpiresIn(value) {
      return value.expires_in === void 0 || typeof value.expires_in === "number";
    }
    function validatePreconditions(issuer, data) {
      if (data.grantType && (!issuer.grantTypesSupported || !issuer.grantTypesSupported.includes(data.grantType))) {
        throw new Error(`The issuer [${issuer.issuer}] does not support the [${data.grantType}] grant`);
      }
      if (!issuer.tokenEndpoint) {
        throw new Error(`This issuer [${issuer.issuer}] does not have a token endpoint`);
      }
    }
    function validateTokenEndpointResponse(tokenResponse, dpop) {
      if (hasError(tokenResponse)) {
        throw new solidClientAuthnCore.OidcProviderError(`Token endpoint returned error [${tokenResponse.error}]${hasErrorDescription(tokenResponse) ? `: ${tokenResponse.error_description}` : ""}${hasErrorUri(tokenResponse) ? ` (see ${tokenResponse.error_uri})` : ""}`, tokenResponse.error, hasErrorDescription(tokenResponse) ? tokenResponse.error_description : void 0);
      }
      if (!hasAccessToken(tokenResponse)) {
        throw new solidClientAuthnCore.InvalidResponseError(["access_token"]);
      }
      if (!hasIdToken(tokenResponse)) {
        throw new solidClientAuthnCore.InvalidResponseError(["id_token"]);
      }
      if (!hasTokenType(tokenResponse)) {
        throw new solidClientAuthnCore.InvalidResponseError(["token_type"]);
      }
      if (!hasExpiresIn(tokenResponse)) {
        throw new solidClientAuthnCore.InvalidResponseError(["expires_in"]);
      }
      if (!dpop && tokenResponse.token_type.toLowerCase() !== "bearer") {
        throw new Error(`Invalid token endpoint response: requested a [Bearer] token, but got a 'token_type' value of [${tokenResponse.token_type}].`);
      }
      return tokenResponse;
    }
    async function getTokens(issuer, client, data, dpop) {
      validatePreconditions(issuer, data);
      const headers = {
        "content-type": "application/x-www-form-urlencoded"
      };
      let dpopKey;
      if (dpop) {
        dpopKey = await solidClientAuthnCore.generateDpopKeyPair();
        headers.DPoP = await solidClientAuthnCore.createDpopHeader(issuer.tokenEndpoint, "POST", dpopKey);
      }
      if (client.clientSecret) {
        headers.Authorization = `Basic ${btoa(`${client.clientId}:${client.clientSecret}`)}`;
      }
      const requestBody = {
        grant_type: data.grantType,
        redirect_uri: data.redirectUrl,
        code: data.code,
        code_verifier: data.codeVerifier,
        client_id: client.clientId
      };
      const tokenRequestInit = {
        method: "POST",
        headers,
        body: new URLSearchParams(requestBody).toString()
      };
      const rawTokenResponse = await fetch(issuer.tokenEndpoint, tokenRequestInit);
      const jsonTokenResponse = await rawTokenResponse.json();
      const tokenResponse = validateTokenEndpointResponse(jsonTokenResponse, dpop);
      const { webId, clientId } = await solidClientAuthnCore.getWebidFromTokenPayload(tokenResponse.id_token, issuer.jwksUri, issuer.issuer, client.clientId);
      return {
        accessToken: tokenResponse.access_token,
        idToken: tokenResponse.id_token,
        refreshToken: hasRefreshToken(tokenResponse) ? tokenResponse.refresh_token : void 0,
        webId,
        clientId,
        dpopKey,
        expiresIn: tokenResponse.expires_in
      };
    }
    var isValidUrl = (url) => {
      try {
        new URL(url);
        return true;
      } catch {
        return false;
      }
    };
    async function refresh(refreshToken, issuer, client, dpopKey) {
      if (client.clientId === void 0) {
        throw new Error("No client ID available when trying to refresh the access token.");
      }
      const requestBody = {
        grant_type: "refresh_token",
        refresh_token: refreshToken
      };
      let dpopHeader = {};
      if (dpopKey !== void 0) {
        dpopHeader = {
          DPoP: await solidClientAuthnCore.createDpopHeader(issuer.tokenEndpoint, "POST", dpopKey)
        };
      }
      let authHeader = {};
      if (client.clientSecret !== void 0) {
        authHeader = {
          // We assume that client_secret_basic is the client authentication method.
          // TODO: Get the authentication method from the IClient configuration object.
          Authorization: `Basic ${btoa(`${client.clientId}:${client.clientSecret}`)}`
        };
      } else if (isValidUrl(client.clientId)) {
        requestBody.client_id = client.clientId;
      }
      const rawResponse = await fetch(issuer.tokenEndpoint, {
        method: "POST",
        body: new URLSearchParams(requestBody).toString(),
        headers: {
          ...dpopHeader,
          ...authHeader,
          "Content-Type": "application/x-www-form-urlencoded"
        }
      });
      let response;
      try {
        response = await rawResponse.json();
      } catch (_e) {
        throw new Error(`The token endpoint of issuer ${issuer.issuer} returned a malformed response.`);
      }
      const validatedResponse = validateTokenEndpointResponse(response, dpopKey !== void 0);
      const { webId } = await solidClientAuthnCore.getWebidFromTokenPayload(validatedResponse.id_token, issuer.jwksUri, issuer.issuer, client.clientId);
      return {
        accessToken: validatedResponse.access_token,
        idToken: validatedResponse.id_token,
        refreshToken: typeof validatedResponse.refresh_token === "string" ? validatedResponse.refresh_token : void 0,
        webId,
        dpopKey,
        expiresIn: validatedResponse.expires_in
      };
    }
    function normalizeCallbackUrl(redirectUrl) {
      const cleanedUrl = solidClientAuthnCore.removeOpenIdParams(redirectUrl);
      cleanedUrl.hash = "";
      if (
        // The trailing slash is present in the original redirect URL
        redirectUrl.includes(`${cleanedUrl.origin}/`)
      ) {
        return cleanedUrl.href;
      }
      return `${cleanedUrl.origin}${cleanedUrl.href.substring(
        // Adds 1 to the origin length to remove the trailing slash
        cleanedUrl.origin.length + 1
      )}`;
    }
    async function clearOidcPersistentStorage() {
      const store = new oidcClientTs.WebStorageStateStore({});
      await oidcClientTs.State.clearStaleState(store, 60 * 15);
      const myStorage = window.localStorage;
      const itemsToRemove = [];
      for (let i = 0; i <= myStorage.length; i += 1) {
        const key = myStorage.key(i);
        if (key && (key.match(/^oidc\..+$/) || key.match(/^solidClientAuthenticationUser:.+$/))) {
          itemsToRemove.push(key);
        }
      }
      itemsToRemove.forEach((key) => myStorage.removeItem(key));
    }
    Object.defineProperty(exports, "AccessTokenEvents", {
      enumerable: true,
      get: function() {
        return oidcClientTs.AccessTokenEvents;
      }
    });
    Object.defineProperty(exports, "CheckSessionIFrame", {
      enumerable: true,
      get: function() {
        return oidcClientTs.CheckSessionIFrame;
      }
    });
    Object.defineProperty(exports, "InMemoryWebStorage", {
      enumerable: true,
      get: function() {
        return oidcClientTs.InMemoryWebStorage;
      }
    });
    Object.defineProperty(exports, "Log", {
      enumerable: true,
      get: function() {
        return oidcClientTs.Log;
      }
    });
    Object.defineProperty(exports, "MetadataService", {
      enumerable: true,
      get: function() {
        return oidcClientTs.MetadataService;
      }
    });
    Object.defineProperty(exports, "OidcClient", {
      enumerable: true,
      get: function() {
        return oidcClientTs.OidcClient;
      }
    });
    Object.defineProperty(exports, "SessionMonitor", {
      enumerable: true,
      get: function() {
        return oidcClientTs.SessionMonitor;
      }
    });
    Object.defineProperty(exports, "SigninResponse", {
      enumerable: true,
      get: function() {
        return oidcClientTs.SigninResponse;
      }
    });
    Object.defineProperty(exports, "User", {
      enumerable: true,
      get: function() {
        return oidcClientTs.User;
      }
    });
    Object.defineProperty(exports, "UserManager", {
      enumerable: true,
      get: function() {
        return oidcClientTs.UserManager;
      }
    });
    Object.defineProperty(exports, "WebStorageStateStore", {
      enumerable: true,
      get: function() {
        return oidcClientTs.WebStorageStateStore;
      }
    });
    exports.clearOidcPersistentStorage = clearOidcPersistentStorage;
    exports.getTokens = getTokens;
    exports.normalizeCallbackUrl = normalizeCallbackUrl;
    exports.refresh = refresh;
    exports.registerClient = registerClient;
  }
});

// node_modules/@inrupt/solid-client-authn-browser/dist/index.js
var require_index = __commonJS({
  "node_modules/@inrupt/solid-client-authn-browser/dist/index.js"(exports) {
    var solidClientAuthnCore = require_dist();
    var uuid = require_cjs_browser();
    var EventEmitter = require_events();
    var oidcClientExt = require_dist2();
    var StorageUtilityBrowser = class extends solidClientAuthnCore.StorageUtility {
      constructor(secureStorage, insecureStorage) {
        super(secureStorage, insecureStorage);
      }
    };
    function isClientExpired(sessionInfo) {
      if (sessionInfo.clientExpiresAt === void 0 || sessionInfo.clientExpiresAt === 0) {
        return false;
      }
      return sessionInfo.clientExpiresAt < Math.floor(Date.now() / 1e3);
    }
    var ClientAuthentication = class extends solidClientAuthnCore.ClientAuthentication {
      // Define these functions as properties so that they don't get accidentally re-bound.
      // Isn't Javascript fun?
      login = async (options, eventEmitter) => {
        if (options.prompt !== "none") {
          await this.sessionInfoManager.clear(options.sessionId);
        }
        const redirectUrl = options.redirectUrl ?? oidcClientExt.normalizeCallbackUrl(window.location.href);
        if (!solidClientAuthnCore.isValidRedirectUrl(redirectUrl)) {
          throw new Error(`${redirectUrl} is not a valid redirect URL, it is either a malformed IRI, includes a hash fragment, or reserved query parameters ('code' or 'state').`);
        }
        await this.loginHandler.handle({
          ...options,
          redirectUrl,
          // If no clientName is provided, the clientId may be used instead.
          clientName: options.clientName ?? options.clientId,
          eventEmitter
        });
      };
      // Collects session information from storage, and returns them. Returns null
      // if the expected information cannot be found or if the client has expired.
      // Note that the ID token is not stored, which means the session information
      // cannot be validated at this point.
      validateCurrentSession = async (currentSessionId) => {
        const sessionInfo = await this.sessionInfoManager.get(currentSessionId);
        if (sessionInfo === void 0 || sessionInfo.clientAppId === void 0 || sessionInfo.issuer === void 0 || isClientExpired(sessionInfo)) {
          return null;
        }
        return sessionInfo;
      };
      handleIncomingRedirect = async (url, eventEmitter) => {
        try {
          const redirectInfo = await this.redirectHandler.handle(url, eventEmitter, void 0);
          this.fetch = redirectInfo.fetch.bind(window);
          this.boundLogout = redirectInfo.getLogoutUrl;
          await this.cleanUrlAfterRedirect(url);
          return {
            isLoggedIn: redirectInfo.isLoggedIn,
            webId: redirectInfo.webId,
            sessionId: redirectInfo.sessionId,
            expirationDate: redirectInfo.expirationDate,
            clientAppId: redirectInfo.clientAppId
          };
        } catch (err) {
          await this.cleanUrlAfterRedirect(url);
          eventEmitter.emit(solidClientAuthnCore.EVENTS.ERROR, "redirect", err);
          return void 0;
        }
      };
      async cleanUrlAfterRedirect(url) {
        const cleanedUpUrl = solidClientAuthnCore.removeOpenIdParams(url).href;
        window.history.replaceState(null, "", cleanedUpUrl);
        while (window.location.href !== cleanedUpUrl) {
          await new Promise((resolve) => {
            setTimeout(() => resolve(), 1);
          });
        }
      }
    };
    function hasIssuer(options) {
      return typeof options.oidcIssuer === "string";
    }
    function hasRedirectUrl(options) {
      return typeof options.redirectUrl === "string";
    }
    var OidcLoginHandler = class {
      storageUtility;
      oidcHandler;
      issuerConfigFetcher;
      clientRegistrar;
      constructor(storageUtility, oidcHandler, issuerConfigFetcher, clientRegistrar) {
        this.storageUtility = storageUtility;
        this.oidcHandler = oidcHandler;
        this.issuerConfigFetcher = issuerConfigFetcher;
        this.clientRegistrar = clientRegistrar;
        this.storageUtility = storageUtility;
        this.oidcHandler = oidcHandler;
        this.issuerConfigFetcher = issuerConfigFetcher;
        this.clientRegistrar = clientRegistrar;
      }
      async canHandle(options) {
        return hasIssuer(options) && hasRedirectUrl(options);
      }
      async handle(options) {
        if (!hasIssuer(options)) {
          throw new solidClientAuthnCore.ConfigurationError(`OidcLoginHandler requires an OIDC issuer: missing property 'oidcIssuer' in ${JSON.stringify(options)}`);
        }
        if (!hasRedirectUrl(options)) {
          throw new solidClientAuthnCore.ConfigurationError(`OidcLoginHandler requires a redirect URL: missing property 'redirectUrl' in ${JSON.stringify(options)}`);
        }
        const issuerConfig = await this.issuerConfigFetcher.fetchConfig(options.oidcIssuer);
        const clientRegistration = await solidClientAuthnCore.handleRegistration(options, issuerConfig, this.storageUtility, this.clientRegistrar);
        const OidcOptions = {
          // Note that here, the issuer is not the one from the received options, but
          // from the issuer's config. This enforces the canonical URL is used and stored,
          // which is also the one present in the ID token, so storing a technically
          // valid, but different issuer URL (e.g. using a trailing slash or not) now
          // could prevent from validating the ID token later.
          issuer: issuerConfig.issuer,
          // TODO: differentiate if DPoP should be true
          dpop: options.tokenType.toLowerCase() === "dpop",
          ...options,
          issuerConfiguration: issuerConfig,
          client: clientRegistration,
          scopes: solidClientAuthnCore.normalizeScopes(options.customScopes)
        };
        return this.oidcHandler.handle(OidcOptions);
      }
    };
    var AuthorizationCodeWithPkceOidcHandler = class extends solidClientAuthnCore.AuthorizationCodeWithPkceOidcHandlerBase {
      async handle(oidcLoginOptions) {
        const redirectUri = oidcLoginOptions.redirectUrl ?? "";
        const oidcOptions = {
          authority: oidcLoginOptions.issuer.toString(),
          client_id: oidcLoginOptions.client.clientId,
          client_secret: oidcLoginOptions.client.clientSecret,
          redirect_uri: redirectUri,
          response_type: "code",
          scope: oidcLoginOptions.scopes.join(" "),
          filterProtocolClaims: true,
          // The userinfo endpoint on NSS fails, so disable this for now
          // Note that in Solid, information should be retrieved from the
          // profile referenced by the WebId.
          loadUserInfo: false,
          prompt: oidcLoginOptions.prompt ?? "consent"
        };
        const oidcClientLibrary = new oidcClientExt.OidcClient(oidcOptions);
        try {
          const signingRequest = await oidcClientLibrary.createSigninRequest({});
          return await this.setupRedirectHandler({
            oidcLoginOptions,
            state: signingRequest.state.id,
            codeVerifier: signingRequest.state.code_verifier ?? "",
            targetUrl: signingRequest.url.toString()
          });
        } catch (err) {
          console.error(err);
        }
        return void 0;
      }
    };
    var WELL_KNOWN_OPENID_CONFIG = ".well-known/openid-configuration";
    var issuerConfigKeyMap = {
      issuer: {
        toKey: "issuer",
        convertToUrl: true
      },
      authorization_endpoint: {
        toKey: "authorizationEndpoint",
        convertToUrl: true
      },
      token_endpoint: {
        toKey: "tokenEndpoint",
        convertToUrl: true
      },
      userinfo_endpoint: {
        toKey: "userinfoEndpoint",
        convertToUrl: true
      },
      jwks_uri: {
        toKey: "jwksUri",
        convertToUrl: true
      },
      registration_endpoint: {
        toKey: "registrationEndpoint",
        convertToUrl: true
      },
      end_session_endpoint: {
        toKey: "endSessionEndpoint",
        convertToUrl: true
      },
      scopes_supported: { toKey: "scopesSupported" },
      response_types_supported: { toKey: "responseTypesSupported" },
      response_modes_supported: { toKey: "responseModesSupported" },
      grant_types_supported: { toKey: "grantTypesSupported" },
      acr_values_supported: { toKey: "acrValuesSupported" },
      subject_types_supported: { toKey: "subjectTypesSupported" },
      id_token_signing_alg_values_supported: {
        toKey: "idTokenSigningAlgValuesSupported"
      },
      id_token_encryption_alg_values_supported: {
        toKey: "idTokenEncryptionAlgValuesSupported"
      },
      id_token_encryption_enc_values_supported: {
        toKey: "idTokenEncryptionEncValuesSupported"
      },
      userinfo_signing_alg_values_supported: {
        toKey: "userinfoSigningAlgValuesSupported"
      },
      userinfo_encryption_alg_values_supported: {
        toKey: "userinfoEncryptionAlgValuesSupported"
      },
      userinfo_encryption_enc_values_supported: {
        toKey: "userinfoEncryptionEncValuesSupported"
      },
      request_object_signing_alg_values_supported: {
        toKey: "requestObjectSigningAlgValuesSupported"
      },
      request_object_encryption_alg_values_supported: {
        toKey: "requestObjectEncryptionAlgValuesSupported"
      },
      request_object_encryption_enc_values_supported: {
        toKey: "requestObjectEncryptionEncValuesSupported"
      },
      token_endpoint_auth_methods_supported: {
        toKey: "tokenEndpointAuthMethodsSupported"
      },
      token_endpoint_auth_signing_alg_values_supported: {
        toKey: "tokenEndpointAuthSigningAlgValuesSupported"
      },
      display_values_supported: { toKey: "displayValuesSupported" },
      claim_types_supported: { toKey: "claimTypesSupported" },
      claims_supported: { toKey: "claimsSupported" },
      service_documentation: { toKey: "serviceDocumentation" },
      claims_locales_supported: { toKey: "claimsLocalesSupported" },
      ui_locales_supported: { toKey: "uiLocalesSupported" },
      claims_parameter_supported: { toKey: "claimsParameterSupported" },
      request_parameter_supported: { toKey: "requestParameterSupported" },
      request_uri_parameter_supported: { toKey: "requestUriParameterSupported" },
      require_request_uri_registration: { toKey: "requireRequestUriRegistration" },
      op_policy_uri: {
        toKey: "opPolicyUri",
        convertToUrl: true
      },
      op_tos_uri: {
        toKey: "opTosUri",
        convertToUrl: true
      }
    };
    function processConfig(config) {
      const parsedConfig = {};
      Object.keys(config).forEach((key) => {
        if (issuerConfigKeyMap[key]) {
          parsedConfig[issuerConfigKeyMap[key].toKey] = config[key];
        }
      });
      if (!Array.isArray(parsedConfig.scopesSupported)) {
        parsedConfig.scopesSupported = ["openid"];
      }
      return parsedConfig;
    }
    var IssuerConfigFetcher = class _IssuerConfigFetcher {
      storageUtility;
      constructor(storageUtility) {
        this.storageUtility = storageUtility;
        this.storageUtility = storageUtility;
      }
      // This method needs no state (so can be static), and can be exposed to allow
      // callers to know where this implementation puts state it needs.
      static getLocalStorageKey(issuer) {
        return `issuerConfig:${issuer}`;
      }
      async fetchConfig(issuer) {
        let issuerConfig;
        const openIdConfigUrl = new URL(
          WELL_KNOWN_OPENID_CONFIG,
          // Make sure to append a slash at issuer URL, so that the .well-known URL
          // includes the full issuer path. See https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfig.
          issuer.endsWith("/") ? issuer : `${issuer}/`
        ).href;
        const issuerConfigRequestBody = await fetch(openIdConfigUrl);
        try {
          issuerConfig = processConfig(await issuerConfigRequestBody.json());
        } catch (err) {
          throw new solidClientAuthnCore.ConfigurationError(`[${issuer.toString()}] has an invalid configuration: ${err.message}`);
        }
        await this.storageUtility.set(_IssuerConfigFetcher.getLocalStorageKey(issuer), JSON.stringify(issuerConfig));
        return issuerConfig;
      }
    };
    async function clear(sessionId, storage) {
      await solidClientAuthnCore.clear(sessionId, storage);
      await oidcClientExt.clearOidcPersistentStorage();
    }
    var SessionInfoManager = class extends solidClientAuthnCore.SessionInfoManagerBase {
      async get(sessionId) {
        const [isLoggedIn2, webId, clientId, clientSecret, redirectUrl, refreshToken, issuer, tokenType, expiresAt] = await Promise.all([
          this.storageUtility.getForUser(sessionId, "isLoggedIn", {
            secure: true
          }),
          this.storageUtility.getForUser(sessionId, "webId", {
            secure: true
          }),
          this.storageUtility.getForUser(sessionId, "clientId", {
            secure: false
          }),
          this.storageUtility.getForUser(sessionId, "clientSecret", {
            secure: false
          }),
          this.storageUtility.getForUser(sessionId, "redirectUrl", {
            secure: false
          }),
          this.storageUtility.getForUser(sessionId, "refreshToken", {
            secure: true
          }),
          this.storageUtility.getForUser(sessionId, "issuer", {
            secure: false
          }),
          this.storageUtility.getForUser(sessionId, "tokenType", {
            secure: false
          }),
          this.storageUtility.getForUser(sessionId, "expiresAt", {
            secure: false
          })
        ]);
        if (typeof redirectUrl === "string" && !solidClientAuthnCore.isValidRedirectUrl(redirectUrl)) {
          await Promise.all([
            this.storageUtility.deleteAllUserData(sessionId, { secure: false }),
            this.storageUtility.deleteAllUserData(sessionId, { secure: true })
          ]);
          return void 0;
        }
        if (tokenType !== void 0 && !solidClientAuthnCore.isSupportedTokenType(tokenType)) {
          throw new Error(`Tokens of type [${tokenType}] are not supported.`);
        }
        if (clientId === void 0 && isLoggedIn2 === void 0 && webId === void 0 && refreshToken === void 0) {
          return void 0;
        }
        return {
          sessionId,
          webId,
          isLoggedIn: isLoggedIn2 === "true",
          redirectUrl,
          refreshToken,
          issuer,
          clientAppId: clientId,
          clientAppSecret: clientSecret,
          // Default the token type to DPoP if unspecified.
          tokenType: tokenType ?? "DPoP",
          clientExpiresAt: expiresAt !== void 0 ? Number.parseInt(expiresAt, 10) : void 0
        };
      }
      /**
       * This function removes all session-related information from storage.
       * @param sessionId the session identifier
       * @param storage the storage where session info is stored
       * @hidden
       */
      async clear(sessionId) {
        return clear(sessionId, this.storageUtility);
      }
    };
    var FallbackRedirectHandler = class {
      async canHandle(redirectUrl) {
        try {
          new URL(redirectUrl);
          return true;
        } catch (e) {
          throw new Error(`[${redirectUrl}] is not a valid URL, and cannot be used as a redirect URL: ${e}`);
        }
      }
      async handle(_redirectUrl) {
        return solidClientAuthnCore.getUnauthenticatedSession();
      }
    };
    var AuthCodeRedirectHandler = class {
      storageUtility;
      sessionInfoManager;
      issuerConfigFetcher;
      clientRegistrar;
      tokerRefresher;
      constructor(storageUtility, sessionInfoManager, issuerConfigFetcher, clientRegistrar, tokerRefresher) {
        this.storageUtility = storageUtility;
        this.sessionInfoManager = sessionInfoManager;
        this.issuerConfigFetcher = issuerConfigFetcher;
        this.clientRegistrar = clientRegistrar;
        this.tokerRefresher = tokerRefresher;
        this.storageUtility = storageUtility;
        this.sessionInfoManager = sessionInfoManager;
        this.issuerConfigFetcher = issuerConfigFetcher;
        this.clientRegistrar = clientRegistrar;
        this.tokerRefresher = tokerRefresher;
      }
      async canHandle(redirectUrl) {
        try {
          const myUrl = new URL(redirectUrl);
          return myUrl.searchParams.get("code") !== null && myUrl.searchParams.get("state") !== null;
        } catch (e) {
          throw new Error(`[${redirectUrl}] is not a valid URL, and cannot be used as a redirect URL: ${e}`);
        }
      }
      async handle(redirectUrl, eventEmitter) {
        if (!await this.canHandle(redirectUrl)) {
          throw new Error(`AuthCodeRedirectHandler cannot handle [${redirectUrl}]: it is missing one of [code, state].`);
        }
        const url = new URL(redirectUrl);
        const oauthState = url.searchParams.get("state");
        const storedSessionId = await this.storageUtility.getForUser(oauthState, "sessionId", {
          errorIfNull: true
        });
        const { issuerConfig, codeVerifier, redirectUrl: storedRedirectIri, dpop: isDpop } = await solidClientAuthnCore.loadOidcContextFromStorage(storedSessionId, this.storageUtility, this.issuerConfigFetcher);
        const iss = url.searchParams.get("iss");
        if (typeof iss === "string" && iss !== issuerConfig.issuer) {
          throw new Error(`The value of the iss parameter (${iss}) does not match the issuer identifier of the authorization server (${issuerConfig.issuer}). See [rfc9207](https://www.rfc-editor.org/rfc/rfc9207.html#section-2.3-3.1.1)`);
        }
        if (codeVerifier === void 0) {
          throw new Error(`The code verifier for session ${storedSessionId} is missing from storage.`);
        }
        if (storedRedirectIri === void 0) {
          throw new Error(`The redirect URL for session ${storedSessionId} is missing from storage.`);
        }
        const client = await this.clientRegistrar.getClient({ sessionId: storedSessionId }, issuerConfig);
        const tokenCreatedAt = Date.now();
        const tokens = await oidcClientExt.getTokens(issuerConfig, client, {
          grantType: "authorization_code",
          // We rely on our 'canHandle' function checking that the OAuth 'code'
          // parameter is present in our query string.
          code: url.searchParams.get("code"),
          codeVerifier,
          redirectUrl: storedRedirectIri
        }, isDpop);
        window.localStorage.removeItem(`oidc.${oauthState}`);
        let refreshOptions;
        if (tokens.refreshToken !== void 0) {
          refreshOptions = {
            sessionId: storedSessionId,
            refreshToken: tokens.refreshToken,
            tokenRefresher: this.tokerRefresher
          };
        }
        const authFetch = solidClientAuthnCore.buildAuthenticatedFetch(tokens.accessToken, {
          dpopKey: tokens.dpopKey,
          refreshOptions,
          eventEmitter,
          expiresIn: tokens.expiresIn
        });
        await solidClientAuthnCore.saveSessionInfoToStorage(this.storageUtility, storedSessionId, tokens.webId, tokens.clientId, "true", void 0, true);
        const sessionInfo = await this.sessionInfoManager.get(storedSessionId);
        if (!sessionInfo) {
          throw new Error(`Could not retrieve session: [${storedSessionId}].`);
        }
        return Object.assign(sessionInfo, {
          fetch: authFetch,
          getLogoutUrl: solidClientAuthnCore.maybeBuildRpInitiatedLogout({
            idTokenHint: tokens.idToken,
            endSessionEndpoint: issuerConfig.endSessionEndpoint
          }),
          expirationDate: typeof tokens.expiresIn === "number" ? tokenCreatedAt + tokens.expiresIn * 1e3 : void 0
        });
      }
    };
    var AggregateRedirectHandler = class extends solidClientAuthnCore.AggregateHandler {
      constructor(redirectHandlers) {
        super(redirectHandlers);
      }
    };
    var BrowserStorage = class {
      get storage() {
        return window.localStorage;
      }
      async get(key) {
        return this.storage.getItem(key) || void 0;
      }
      async set(key, value) {
        this.storage.setItem(key, value);
      }
      async delete(key) {
        this.storage.removeItem(key);
      }
    };
    var Redirector = class {
      redirect(redirectUrl, options) {
        if (options && options.handleRedirect) {
          options.handleRedirect(redirectUrl);
        } else if (options && options.redirectByReplacingState) {
          window.history.replaceState({}, "", redirectUrl);
        } else {
          window.location.href = redirectUrl;
        }
      }
    };
    var ClientRegistrar = class {
      storageUtility;
      constructor(storageUtility) {
        this.storageUtility = storageUtility;
        this.storageUtility = storageUtility;
      }
      async getClient(options, issuerConfig) {
        const [storedClientId, storedClientSecret, expiresAt, storedClientName, storedClientType] = await Promise.all([
          this.storageUtility.getForUser(options.sessionId, "clientId", {
            secure: false
          }),
          this.storageUtility.getForUser(options.sessionId, "clientSecret", {
            secure: false
          }),
          this.storageUtility.getForUser(options.sessionId, "expiresAt", {
            secure: false
          }),
          this.storageUtility.getForUser(options.sessionId, "clientName", {
            secure: false
          }),
          this.storageUtility.getForUser(options.sessionId, "clientType", {
            secure: false
          })
        ]);
        const expirationDate = expiresAt !== void 0 ? Number.parseInt(expiresAt, 10) : -1;
        const expired = storedClientSecret !== void 0 && expirationDate !== 0 && Math.floor(Date.now() / 1e3) > expirationDate;
        if (storedClientId && solidClientAuthnCore.isKnownClientType(storedClientType) && !expired) {
          return storedClientSecret !== void 0 ? {
            clientId: storedClientId,
            clientSecret: storedClientSecret,
            clientName: storedClientName,
            // Note: static clients are not applicable in a browser context.
            clientType: "dynamic",
            expiresAt: expirationDate
          } : {
            clientId: storedClientId,
            clientName: storedClientName,
            // Note: static clients are not applicable in a browser context.
            clientType: storedClientType
            // The type assertion is required even though the type should match the declaration.
          };
        }
        try {
          const registeredClient = await oidcClientExt.registerClient(options, issuerConfig);
          const infoToSave = {
            clientId: registeredClient.clientId,
            clientType: "dynamic"
          };
          if (registeredClient.clientSecret !== void 0) {
            infoToSave.clientSecret = registeredClient.clientSecret;
            infoToSave.expiresAt = String(registeredClient.expiresAt);
          }
          if (registeredClient.idTokenSignedResponseAlg) {
            infoToSave.idTokenSignedResponseAlg = registeredClient.idTokenSignedResponseAlg;
          }
          await this.storageUtility.setForUser(options.sessionId, infoToSave, {
            // FIXME: figure out how to persist secure storage at reload
            // Otherwise, the client info cannot be retrieved from storage, and
            // the lib tries to re-register the client on each fetch
            secure: false
          });
          return registeredClient;
        } catch (error) {
          throw new Error(`Client registration failed.`, { cause: error });
        }
      }
    };
    var ErrorOidcHandler = class {
      async canHandle(redirectUrl) {
        try {
          return new URL(redirectUrl).searchParams.has("error");
        } catch (e) {
          throw new Error(`[${redirectUrl}] is not a valid URL, and cannot be used as a redirect URL: ${e}`);
        }
      }
      async handle(redirectUrl, eventEmitter) {
        if (eventEmitter !== void 0) {
          const url = new URL(redirectUrl);
          const errorUrl = url.searchParams.get("error");
          const errorDescriptionUrl = url.searchParams.get("error_description");
          eventEmitter.emit(solidClientAuthnCore.EVENTS.ERROR, errorUrl, errorDescriptionUrl);
        }
        return solidClientAuthnCore.getUnauthenticatedSession();
      }
    };
    var TokenRefresher = class {
      storageUtility;
      issuerConfigFetcher;
      clientRegistrar;
      constructor(storageUtility, issuerConfigFetcher, clientRegistrar) {
        this.storageUtility = storageUtility;
        this.issuerConfigFetcher = issuerConfigFetcher;
        this.clientRegistrar = clientRegistrar;
        this.storageUtility = storageUtility;
        this.issuerConfigFetcher = issuerConfigFetcher;
        this.clientRegistrar = clientRegistrar;
      }
      async refresh(sessionId, refreshToken, dpopKey, eventEmitter) {
        const oidcContext = await solidClientAuthnCore.loadOidcContextFromStorage(sessionId, this.storageUtility, this.issuerConfigFetcher);
        const clientInfo = await this.clientRegistrar.getClient({ sessionId }, oidcContext.issuerConfig);
        if (refreshToken === void 0) {
          throw new Error(`Session [${sessionId}] has no refresh token to allow it to refresh its access token.`);
        }
        if (oidcContext.dpop && dpopKey === void 0) {
          throw new Error(`For session [${sessionId}], the key bound to the DPoP access token must be provided to refresh said access token.`);
        }
        const tokenSet = await oidcClientExt.refresh(refreshToken, oidcContext.issuerConfig, clientInfo, dpopKey);
        if (tokenSet.refreshToken !== void 0) {
          eventEmitter?.emit(solidClientAuthnCore.EVENTS.NEW_REFRESH_TOKEN, tokenSet.refreshToken);
        }
        return tokenSet;
      }
    };
    function getClientAuthenticationWithDependencies(dependencies) {
      const inMemoryStorage = new solidClientAuthnCore.InMemoryStorage();
      const secureStorage = dependencies.secureStorage || inMemoryStorage;
      const insecureStorage = dependencies.insecureStorage || new BrowserStorage();
      const storageUtility = new StorageUtilityBrowser(secureStorage, insecureStorage);
      const issuerConfigFetcher = new IssuerConfigFetcher(storageUtility);
      const clientRegistrar = new ClientRegistrar(storageUtility);
      const sessionInfoManager = new SessionInfoManager(storageUtility);
      const tokenRefresher = new TokenRefresher(storageUtility, issuerConfigFetcher, clientRegistrar);
      const redirector = new Redirector();
      const loginHandler = new OidcLoginHandler(storageUtility, new AuthorizationCodeWithPkceOidcHandler(storageUtility, redirector), issuerConfigFetcher, clientRegistrar);
      const redirectHandler = new AggregateRedirectHandler([
        new ErrorOidcHandler(),
        new AuthCodeRedirectHandler(storageUtility, sessionInfoManager, issuerConfigFetcher, clientRegistrar, tokenRefresher),
        // This catch-all class will always be able to handle the
        // redirect IRI, so it must be registered last.
        new FallbackRedirectHandler()
      ]);
      return new ClientAuthentication(loginHandler, redirectHandler, new solidClientAuthnCore.IWaterfallLogoutHandler(sessionInfoManager, redirector), sessionInfoManager, issuerConfigFetcher);
    }
    var KEY_CURRENT_SESSION = `${solidClientAuthnCore.SOLID_CLIENT_AUTHN_KEY_PREFIX}currentSession`;
    var KEY_CURRENT_URL = `${solidClientAuthnCore.SOLID_CLIENT_AUTHN_KEY_PREFIX}currentUrl`;
    async function silentlyAuthenticate(sessionId, clientAuthn, session) {
      const storedSessionInfo = await clientAuthn.validateCurrentSession(sessionId);
      if (storedSessionInfo !== null) {
        window.localStorage.setItem(KEY_CURRENT_URL, window.location.href);
        await clientAuthn.login({
          sessionId,
          prompt: "none",
          oidcIssuer: storedSessionInfo.issuer,
          redirectUrl: storedSessionInfo.redirectUrl,
          clientId: storedSessionInfo.clientAppId,
          clientSecret: storedSessionInfo.clientAppSecret,
          tokenType: storedSessionInfo.tokenType ?? "DPoP"
        }, session.events);
        return true;
      }
      return false;
    }
    function isLoggedIn(sessionInfo) {
      return !!sessionInfo?.isLoggedIn;
    }
    var Session = class {
      /**
       * Information regarding the current session.
       */
      info;
      /**
       * Session attribute exposing the EventEmitter interface, to listen on session
       * events such as login, logout, etc.
       * @since 1.15.0
       */
      events;
      clientAuthentication;
      tokenRequestInProgress = false;
      /**
       * Session object constructor. Typically called as follows:
       *
       * ```typescript
       * const session = new Session();
       * ```
       *
       * See also [getDefaultSession](https://docs.inrupt.com/developer-tools/api/javascript/solid-client-authn-browser/functions.html#getdefaultsession).
       *
       * @param sessionOptions The options enabling the correct instantiation of
       * the session. Either both storages or clientAuthentication are required. For
       * more information, see {@link ISessionOptions}.
       * @param sessionId A string uniquely identifying the session.
       *
       */
      constructor(sessionOptions = {}, sessionId = void 0) {
        this.events = new EventEmitter();
        if (sessionOptions.clientAuthentication) {
          this.clientAuthentication = sessionOptions.clientAuthentication;
        } else if (sessionOptions.secureStorage && sessionOptions.insecureStorage) {
          this.clientAuthentication = getClientAuthenticationWithDependencies({
            secureStorage: sessionOptions.secureStorage,
            insecureStorage: sessionOptions.insecureStorage
          });
        } else {
          this.clientAuthentication = getClientAuthenticationWithDependencies({});
        }
        if (sessionOptions.sessionInfo) {
          this.info = {
            sessionId: sessionOptions.sessionInfo.sessionId,
            isLoggedIn: false,
            webId: sessionOptions.sessionInfo.webId,
            clientAppId: sessionOptions.sessionInfo.clientAppId
          };
        } else {
          this.info = {
            sessionId: sessionId ?? uuid.v4(),
            isLoggedIn: false
          };
        }
        this.events.on(solidClientAuthnCore.EVENTS.LOGIN, () => window.localStorage.setItem(KEY_CURRENT_SESSION, this.info.sessionId));
        this.events.on(solidClientAuthnCore.EVENTS.SESSION_EXPIRED, () => this.internalLogout(false));
        this.events.on(solidClientAuthnCore.EVENTS.ERROR, () => this.internalLogout(false));
      }
      /**
       * Triggers the login process. Note that this method will redirect the user away from your app.
       *
       * @param options Parameter to customize the login behaviour. In particular, two options are mandatory: `options.oidcIssuer`, the user's identity provider, and `options.redirectUrl`, the URL to which the user will be redirected after logging in their identity provider.
       * @returns This method should redirect the user away from the app: it does not return anything. The login process is completed by {@linkcode handleIncomingRedirect}.
       */
      // Define these functions as properties so that they don't get accidentally re-bound.
      // Isn't Javascript fun?
      login = async (options) => {
        await this.clientAuthentication.login({
          sessionId: this.info.sessionId,
          ...options,
          // Defaults the token type to DPoP
          tokenType: options.tokenType ?? "DPoP"
        }, this.events);
        return new Promise(() => {
        });
      };
      /**
       * Fetches data using available login information. If the user is not logged in, this will behave as a regular `fetch`. The signature of this method is identical to the [canonical `fetch`](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API).
       *
       * @param url The URL from which data should be fetched.
       * @param init Optional parameters customizing the request, by specifying an HTTP method, headers, a body, etc. Follows the [WHATWG Fetch Standard](https://fetch.spec.whatwg.org/).
       */
      fetch = (url, init) => this.clientAuthentication.fetch(url, init);
      /**
       * An internal logout function, to control whether or not the logout signal
       * should be sent, i.e. if the logout was user-initiated or is the result of
       * an external event.
       *
       * @hidden
       */
      internalLogout = async (emitSignal, options) => {
        window.localStorage.removeItem(KEY_CURRENT_SESSION);
        await this.clientAuthentication.logout(this.info.sessionId, options);
        this.info.isLoggedIn = false;
        if (emitSignal) {
          this.events.emit(solidClientAuthnCore.EVENTS.LOGOUT);
        }
      };
      /**
       * Logs the user out of the application.
       *
       * There are 2 types of logout supported by this library,
       * `app` logout and `idp` logout.
       *
       * App logout will log the user out within the application
       * by clearing any session data from the browser. It does
       * not log the user out of their Solid identity provider,
       * and should not redirect the user away.
       * App logout can be performed as follows:
       * ```typescript
       * await session.logout({ logoutType: 'app' });
       * ```
       *
       * IDP logout will log the user out of their Solid identity provider,
       * and will redirect the user away from the application to do so. In order
       * for users to be redirected back to `postLogoutUrl` you MUST include the
       * `postLogoutUrl` value in the `post_logout_redirect_uris` field in the
       * [Client ID Document](https://docs.inrupt.com/ess/latest/security/authentication/#client-identifier-client-id).
       * IDP logout can be performed as follows:
       * ```typescript
       * await session.logout({
       *  logoutType: 'idp',
       *  // An optional URL to redirect to after logout has completed;
       *  // this MUST match a logout URL listed in the Client ID Document
       *  // of the application that is logged in.
       *  // If the application is logged in with a Client ID that is not
       *  // a URI dereferencing to a Client ID Document then users will
       *  // not be redirected back to the `postLogoutUrl` after logout.
       *  postLogoutUrl: 'https://example.com/logout',
       *  // An optional value to be included in the query parameters
       *  // when the IDP provider redirects the user to the postLogoutRedirectUrl.
       *  state: "my-state"
       * });
       * ```
       */
      logout = async (options) => this.internalLogout(true, options);
      /**
       * Completes the login process by processing the information provided by the
       * Solid identity provider through redirect.
       *
       * @param options See {@link IHandleIncomingRedirectOptions}.
       */
      handleIncomingRedirect = async (inputOptions = {}) => {
        if (this.info.isLoggedIn) {
          return this.info;
        }
        if (this.tokenRequestInProgress) {
          return void 0;
        }
        const options = typeof inputOptions === "string" ? { url: inputOptions } : inputOptions;
        const url = options.url ?? window.location.href;
        this.tokenRequestInProgress = true;
        const sessionInfo = await this.clientAuthentication.handleIncomingRedirect(url, this.events);
        if (isLoggedIn(sessionInfo)) {
          this.setSessionInfo(sessionInfo);
          const currentUrl = window.localStorage.getItem(KEY_CURRENT_URL);
          if (currentUrl === null) {
            this.events.emit(solidClientAuthnCore.EVENTS.LOGIN);
          } else {
            window.localStorage.removeItem(KEY_CURRENT_URL);
            this.events.emit(solidClientAuthnCore.EVENTS.SESSION_RESTORED, currentUrl);
          }
        } else if (options.restorePreviousSession === true) {
          const storedSessionId = window.localStorage.getItem(KEY_CURRENT_SESSION);
          if (storedSessionId !== null) {
            const attemptedSilentAuthentication = await silentlyAuthenticate(storedSessionId, this.clientAuthentication, this);
            if (attemptedSilentAuthentication) {
              return new Promise(() => {
              });
            }
          }
        }
        this.tokenRequestInProgress = false;
        return sessionInfo;
      };
      setSessionInfo(sessionInfo) {
        this.info.isLoggedIn = sessionInfo.isLoggedIn;
        this.info.webId = sessionInfo.webId;
        this.info.sessionId = sessionInfo.sessionId;
        this.info.clientAppId = sessionInfo.clientAppId;
        this.info.expirationDate = sessionInfo.expirationDate;
        this.events.on(solidClientAuthnCore.EVENTS.SESSION_EXTENDED, (expiresIn) => {
          this.info.expirationDate = Date.now() + expiresIn * 1e3;
        });
      }
    };
    var defaultSession;
    function getDefaultSession() {
      if (typeof defaultSession === "undefined") {
        defaultSession = new Session();
      }
      return defaultSession;
    }
    function fetch$1(...args) {
      const session = getDefaultSession();
      return session.fetch(...args);
    }
    function login(...args) {
      const session = getDefaultSession();
      return session.login(...args);
    }
    function logout(...args) {
      const session = getDefaultSession();
      return session.logout(...args);
    }
    function handleIncomingRedirect(...args) {
      const session = getDefaultSession();
      return session.handleIncomingRedirect(...args);
    }
    function events() {
      return getDefaultSession().events;
    }
    Object.defineProperty(exports, "ConfigurationError", {
      enumerable: true,
      get: function() {
        return solidClientAuthnCore.ConfigurationError;
      }
    });
    Object.defineProperty(exports, "EVENTS", {
      enumerable: true,
      get: function() {
        return solidClientAuthnCore.EVENTS;
      }
    });
    Object.defineProperty(exports, "InMemoryStorage", {
      enumerable: true,
      get: function() {
        return solidClientAuthnCore.InMemoryStorage;
      }
    });
    Object.defineProperty(exports, "NotImplementedError", {
      enumerable: true,
      get: function() {
        return solidClientAuthnCore.NotImplementedError;
      }
    });
    exports.Session = Session;
    exports.events = events;
    exports.fetch = fetch$1;
    exports.getDefaultSession = getDefaultSession;
    exports.handleIncomingRedirect = handleIncomingRedirect;
    exports.login = login;
    exports.logout = logout;
  }
});
export default require_index();
