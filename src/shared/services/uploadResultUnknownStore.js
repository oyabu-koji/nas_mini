import AsyncStorage from '@react-native-async-storage/async-storage';

const UPLOAD_RESULT_UNKNOWN_KEY = 'mediavault.uploadResultUnknown';

/**
 * @typedef {{ kind: 'local_asset', localAssetId: string } | { kind: 'global_pending' }} UploadResultUnknown
 */

/**
 * @param {unknown} value
 * @returns {UploadResultUnknown | null}
 */
function parsePending(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  if (value.kind === 'global_pending') {
    return { kind: 'global_pending' };
  }
  if (value.kind === 'local_asset' && typeof value.localAssetId === 'string' && value.localAssetId.trim()) {
    return { kind: 'local_asset', localAssetId: value.localAssetId };
  }
  return null;
}

/**
 * Reads a pending upload conservatively. Unreadable or malformed storage is a global lock.
 * @returns {Promise<UploadResultUnknown | null>}
 */
export async function readUploadResultUnknown() {
  try {
    const raw = await AsyncStorage.getItem(UPLOAD_RESULT_UNKNOWN_KEY);
    if (raw == null) {
      return null;
    }
    const pending = parsePending(JSON.parse(raw));
    return pending ?? { kind: 'global_pending' };
  } catch {
    return { kind: 'global_pending' };
  }
}

/**
 * @param {UploadResultUnknown} pending
 * @returns {Promise<UploadResultUnknown>}
 */
export async function saveUploadResultUnknown(pending) {
  const normalized = parsePending(pending);
  if (!normalized) {
    throw new Error('invalid upload result unknown state');
  }
  await AsyncStorage.setItem(UPLOAD_RESULT_UNKNOWN_KEY, JSON.stringify(normalized));
  return normalized;
}

export async function clearUploadResultUnknown() {
  await AsyncStorage.removeItem(UPLOAD_RESULT_UNKNOWN_KEY);
}

/**
 * @param {UploadResultUnknown | null} pending
 * @param {string | null | undefined} localAssetId
 */
export function blocksUploadForAsset(pending, localAssetId) {
  if (!pending) {
    return false;
  }
  if (pending.kind === 'global_pending') {
    return true;
  }
  return Boolean(localAssetId) && pending.localAssetId === localAssetId;
}

export function blocksAssetSelection(pending) {
  return pending?.kind === 'global_pending';
}
