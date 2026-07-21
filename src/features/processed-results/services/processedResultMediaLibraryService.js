import * as MediaLibrary from 'expo-media-library';

import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

export async function requestProcessedResultLibraryPermission({ mediaLibrary = MediaLibrary } = {}) {
  try {
    const permission = await mediaLibrary.requestPermissionsAsync();
    if (permission?.status !== 'granted') {
      throw createAppError(
        'processed_result_library_permission_denied',
        messageForErrorCode('processed_result_library_permission_denied'),
      );
    }
    return true;
  } catch (error) {
    if (error?.code) {
      throw error;
    }
    throw createAppError(
      'processed_result_library_permission_denied',
      messageForErrorCode('processed_result_library_permission_denied'),
    );
  }
}

export async function createProcessedResultLibraryAsset({ uri, mediaLibrary = MediaLibrary }) {
  if (typeof uri !== 'string' || !uri.startsWith('file://')) {
    throw createAppError(
      'processed_result_library_save_failed',
      messageForErrorCode('processed_result_library_save_failed'),
    );
  }
  try {
    const asset = await mediaLibrary.createAssetAsync(uri);
    const localAssetIdentifier = normalizeLocalAssetIdentifier(asset?.id);
    return { localAssetIdentifier };
  } catch (error) {
    if (error?.code) {
      throw error;
    }
    throw createAppError(
      'processed_result_library_save_failed',
      messageForErrorCode('processed_result_library_save_failed'),
    );
  }
}

function normalizeLocalAssetIdentifier(value) {
  const normalized = String(value ?? '').trim();
  if (!normalized || normalized.length > 256 || normalized.startsWith('file:') || normalized.startsWith('/')) {
    throw createAppError(
      'processed_result_library_save_failed',
      messageForErrorCode('processed_result_library_save_failed'),
    );
  }
  return normalized;
}
