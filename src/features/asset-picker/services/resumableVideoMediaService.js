import * as MediaLibrary from 'expo-media-library';
import * as FileSystem from 'expo-file-system/legacy';

import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

/**
 * Resolve the current local URI for a photo-library video before hashing/upload.
 * @param {{ localAssetId?: string | null, type?: string, sizeBytes?: number | null }} pickedAsset
 * @returns {Promise<object>}
 */
export async function resolveResumableVideoAsset(pickedAsset) {
  if (pickedAsset?.type !== 'video') {
    throw createAppError('validation_error', messageForErrorCode('validation_error'));
  }
  if (!pickedAsset.localAssetId) {
    throw createAppError(
      'resumable_video_requires_library_asset',
      messageForErrorCode('resumable_video_requires_library_asset'),
    );
  }

  try {
    const info = await MediaLibrary.getAssetInfoAsync(pickedAsset.localAssetId, {
      shouldDownloadFromNetwork: true,
    });
    const localUri = typeof info?.localUri === 'string' && info.localUri.trim() ? info.localUri : null;
    if (!localUri) {
      throw createAppError('media_unavailable', messageForErrorCode('media_unavailable'));
    }
    const fileInfo = await FileSystem.getInfoAsync(localUri, { size: true });
    const sizeBytes = Number(fileInfo?.size);
    if (!fileInfo?.exists || !Number.isSafeInteger(sizeBytes) || sizeBytes <= 0) {
      throw createAppError('media_unavailable', messageForErrorCode('media_unavailable'));
    }
    return {
      ...pickedAsset,
      uri: localUri,
      localAssetId: pickedAsset.localAssetId,
      sizeBytes,
    };
  } catch (error) {
    if (error?.code) {
      throw error;
    }
    throw createAppError('media_unavailable', messageForErrorCode('media_unavailable'), {
      retryable: true,
    });
  }
}
