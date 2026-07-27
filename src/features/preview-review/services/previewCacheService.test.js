import * as FileSystem from 'expo-file-system/legacy';

import { createAppError } from '../../../shared/utils/errors';
import { downloadPreviewToCache } from './previewCacheService';

jest.mock('expo-file-system/legacy', () => ({
  cacheDirectory: 'file:///cache/',
  FileSystemSessionType: { FOREGROUND: 'FOREGROUND' },
  downloadAsync: jest.fn(),
}));
const settings = { backendUrl: 'http://mediavault', apiToken: 'secret-token' };

describe('previewCacheService', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // The Jest module mock intentionally exposes mutable cache availability.
    // eslint-disable-next-line import/namespace
    FileSystem.cacheDirectory = 'file:///cache/';
  });

  it('uses a sanitized extension, authenticated URL, and foreground cache download', async () => {
    FileSystem.downloadAsync.mockResolvedValue({ status: 200, uri: 'file:///cache/saved.mp4' });

    await expect(
      downloadPreviewToCache({ settings, assetId: 42, extension: '.m-p4!' }),
    ).resolves.toBe('file:///cache/saved.mp4');
    expect(FileSystem.downloadAsync).toHaveBeenCalledWith(
      'http://mediavault/assets/42/preview',
      'file:///cache/mediavault-preview-42.mp4',
      {
        headers: {
          Authorization: 'Bearer secret-token',
          'X-MediaVault-Client-Version': '0.2.0',
        },
        cache: true,
        sessionType: 'FOREGROUND',
      },
    );
  });

  it('uses bin for an empty sanitized extension and rejects unavailable cache', async () => {
    FileSystem.downloadAsync.mockResolvedValue({ status: 200, uri: 'file:///cache/saved.bin' });
    await downloadPreviewToCache({ settings, assetId: 7, extension: '---' });
    expect(FileSystem.downloadAsync).toHaveBeenCalledWith(
      expect.any(String),
      'file:///cache/mediavault-preview-7.bin',
      expect.any(Object),
    );

    // The Jest module mock intentionally exposes mutable cache availability.
    // eslint-disable-next-line import/namespace
    FileSystem.cacheDirectory = null;
    await expect(downloadPreviewToCache({ settings, assetId: 7 })).rejects.toMatchObject({
      code: 'storage_or_cache_error',
    });
  });

  it('maps HTTP and native failures while preserving stable domain errors', async () => {
    FileSystem.downloadAsync.mockResolvedValueOnce({ status: 500, uri: 'file:///cache/error.mp4' });
    await expect(downloadPreviewToCache({ settings, assetId: 42 })).rejects.toMatchObject({
      code: 'storage_or_cache_error',
    });

    FileSystem.downloadAsync.mockRejectedValueOnce(new Error('native details'));
    await expect(downloadPreviewToCache({ settings, assetId: 42 })).rejects.toMatchObject({
      code: 'storage_or_cache_error',
      message: 'Preview cache could not be prepared.',
    });

    FileSystem.downloadAsync.mockRejectedValueOnce(createAppError('missing_settings', 'Missing settings'));
    await expect(downloadPreviewToCache({ settings, assetId: 42 })).rejects.toMatchObject({
      code: 'missing_settings',
    });
  });

  it('rejects an invalid URL before token coercion or a cache download', async () => {
    const token = {
      toString: jest.fn(() => 'must-not-be-read'),
    };

    await expect(
      downloadPreviewToCache({
        settings: {
          backendUrl: 'http://public.example.com/private-path?token=leak',
          apiToken: token,
        },
        assetId: 42,
      }),
    ).rejects.toMatchObject({
      code: 'invalid_url',
      message: 'Use a private HTTP backend or a valid HTTPS backend.',
    });

    expect(token.toString).not.toHaveBeenCalled();
    expect(FileSystem.downloadAsync).not.toHaveBeenCalled();
  });
});
