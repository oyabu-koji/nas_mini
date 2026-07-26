import * as MediaLibrary from 'expo-media-library';

import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

const NATIVE_ERROR_CODES = {
  E_USER_CANCELLED: 'original_delete_cancelled',
  E_ASSET_NOT_FOUND: 'original_delete_asset_unavailable',
  E_UNAVAILABLE: 'original_delete_api_unavailable',
};
const DELETION_ERROR_CODES = new Set([
  'original_delete_permission_denied',
  'original_delete_cancelled',
  'original_delete_asset_unavailable',
  'original_delete_api_unavailable',
  'original_delete_failed',
]);

export async function deleteOriginalAsset({
  localAssetId,
  mediaLibrary = MediaLibrary,
}) {
  const normalizedId = normalizeLocalAssetId(localAssetId);
  if (
    typeof mediaLibrary.requestPermissionsAsync !== 'function'
    || typeof mediaLibrary.deleteAssetsAsync !== 'function'
  ) {
    throw domainError('original_delete_api_unavailable');
  }
  try {
    const permission = await mediaLibrary.requestPermissionsAsync();
    if (permission?.status !== 'granted') {
      throw domainError('original_delete_permission_denied');
    }
    const deleted = await mediaLibrary.deleteAssetsAsync([normalizedId]);
    if (deleted !== true) {
      throw domainError('original_delete_cancelled');
    }
    return { status: 'deleted' };
  } catch (error) {
    if (DELETION_ERROR_CODES.has(error?.code)) {
      throw domainError(error.code);
    }
    throw domainError(NATIVE_ERROR_CODES[error?.code] ?? 'original_delete_failed');
  }
}

function normalizeLocalAssetId(value) {
  const normalized = String(value ?? '').trim();
  if (
    !normalized
    || normalized.length > 256
    || normalized.startsWith('file:')
    || normalized.startsWith('/')
    || /[\u0000-\u001f\u007f]/.test(normalized)
  ) {
    throw domainError('original_delete_asset_unavailable');
  }
  return normalized;
}

function domainError(code) {
  return createAppError(code, messageForErrorCode(code));
}
