import { sha256File, sha256Range } from '../../../../modules/streaming-sha256/src';
import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

const SHA256_PATTERN = /^[0-9a-f]{64}$/;

/**
 * @param {string} uri Resolved local video URI.
 * @returns {Promise<string>} Lowercase SHA-256 digest.
 */
export async function hashWholeFile(uri) {
  validateUri(uri);
  return callNativeHash(() => sha256File(uri));
}

/**
 * @param {string} uri Resolved local video URI.
 * @param {number} offset Inclusive byte offset.
 * @param {number} length Number of bytes to hash.
 * @returns {Promise<string>} Lowercase SHA-256 digest.
 */
export async function hashFileRange(uri, offset, length) {
  validateUri(uri);
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(length) || length <= 0) {
    throw createAppError('native_hash_invalid_range', messageForErrorCode('native_hash_invalid_range'));
  }
  return callNativeHash(() => sha256Range(uri, offset, length));
}

async function callNativeHash(operation) {
  try {
    const digest = String(await operation()).toLowerCase();
    if (!SHA256_PATTERN.test(digest)) {
      throw createAppError('native_hash_invalid_result', messageForErrorCode('native_hash_invalid_result'));
    }
    return digest;
  } catch (error) {
    if (error?.code) {
      throw error;
    }
    throw createAppError('native_hash_unavailable', messageForErrorCode('native_hash_unavailable'), {
      retryable: false,
    });
  }
}

function validateUri(uri) {
  if (typeof uri !== 'string' || !uri.trim()) {
    throw createAppError('media_unavailable', messageForErrorCode('media_unavailable'));
  }
}
