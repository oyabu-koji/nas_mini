import * as FileSystem from 'expo-file-system/legacy';

import { buildPreviewUrl, createAuthHeaders } from '../../../shared/api/mediaVaultApi';
import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

export async function downloadPreviewToCache({ settings, assetId, extension = 'mp4' }) {
  if (!FileSystem.cacheDirectory) {
    throw createAppError('storage_or_cache_error', messageForErrorCode('storage_or_cache_error'));
  }

  const safeExtension = String(extension || 'bin').replace(/[^a-zA-Z0-9]/g, '') || 'bin';
  const fileUri = `${FileSystem.cacheDirectory}mediavault-preview-${assetId}.${safeExtension}`;

  try {
    const result = await FileSystem.downloadAsync(buildPreviewUrl(settings.backendUrl, assetId), fileUri, {
      headers: createAuthHeaders(settings.apiToken),
      cache: true,
      sessionType: FileSystem.FileSystemSessionType.FOREGROUND,
    });
    if (result.status >= 400) {
      throw createAppError('storage_or_cache_error', messageForErrorCode('storage_or_cache_error'));
    }
    return result.uri;
  } catch (error) {
    if (error?.code) {
      throw error;
    }
    throw createAppError('storage_or_cache_error', messageForErrorCode('storage_or_cache_error'));
  }
}
