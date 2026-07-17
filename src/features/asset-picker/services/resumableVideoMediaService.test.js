import * as MediaLibrary from 'expo-media-library';
import * as FileSystem from 'expo-file-system/legacy';

import { resolveResumableVideoAsset } from './resumableVideoMediaService';

jest.mock('expo-media-library', () => ({
  getAssetInfoAsync: jest.fn(),
}));
jest.mock('expo-file-system/legacy', () => ({
  getInfoAsync: jest.fn(),
}));

describe('resumableVideoMediaService', () => {
  const pickedVideo = {
    type: 'video',
    localAssetId: 'library-asset-123',
    uri: 'stale-uri',
    sizeBytes: 8,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    FileSystem.getInfoAsync.mockResolvedValue({ exists: true, size: 16 });
  });

  it('re-resolves the same photo-library asset and requests iCloud download', async () => {
    MediaLibrary.getAssetInfoAsync.mockResolvedValue({ localUri: 'file:///downloaded.mov' });

    await expect(resolveResumableVideoAsset(pickedVideo)).resolves.toEqual({
      ...pickedVideo,
      uri: 'file:///downloaded.mov',
      sizeBytes: 16,
    });
    expect(MediaLibrary.getAssetInfoAsync).toHaveBeenCalledWith('library-asset-123', {
      shouldDownloadFromNetwork: true,
    });
    expect(FileSystem.getInfoAsync).toHaveBeenCalledWith('file:///downloaded.mov', { size: true });
  });

  it('stops before session creation for missing asset id, iCloud failure, or missing local URI', async () => {
    await expect(resolveResumableVideoAsset({ ...pickedVideo, localAssetId: null })).rejects.toMatchObject({
      code: 'resumable_video_requires_library_asset',
    });

    MediaLibrary.getAssetInfoAsync.mockRejectedValue(new Error('iCloud unavailable'));
    await expect(resolveResumableVideoAsset(pickedVideo)).rejects.toMatchObject({ code: 'media_unavailable' });

    MediaLibrary.getAssetInfoAsync.mockResolvedValue({ localUri: null });
    await expect(resolveResumableVideoAsset(pickedVideo)).rejects.toMatchObject({ code: 'media_unavailable' });

    MediaLibrary.getAssetInfoAsync.mockResolvedValue({ localUri: 'file:///downloaded.mov' });
    FileSystem.getInfoAsync.mockResolvedValue({ exists: true, size: 0 });
    await expect(resolveResumableVideoAsset(pickedVideo)).rejects.toMatchObject({ code: 'media_unavailable' });
  });
});
