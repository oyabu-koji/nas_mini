import { requireNativeModule } from 'expo';

function getNativeModule() {
  return requireNativeModule('StreamingSha256');
}

/**
 * @param {string} uri A resolved local or content URI.
 * @returns {Promise<string>} Lowercase SHA-256 hexadecimal digest.
 */
export function sha256File(uri) {
  return getNativeModule().sha256File(uri);
}

/**
 * @param {string} uri A resolved local or content URI.
 * @param {number} offset Inclusive byte offset.
 * @param {number} length Number of bytes to hash.
 * @returns {Promise<string>} Lowercase SHA-256 hexadecimal digest.
 */
export function sha256Range(uri, offset, length) {
  return getNativeModule().sha256Range(uri, offset, length);
}
