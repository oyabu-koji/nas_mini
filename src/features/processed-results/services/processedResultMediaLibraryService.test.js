import * as MediaLibrary from 'expo-media-library';

import {
  createProcessedResultLibraryAsset,
  requestProcessedResultLibraryPermission,
} from './processedResultMediaLibraryService';

jest.mock('expo-media-library', () => ({
  requestPermissionsAsync: jest.fn(),
  createAssetAsync: jest.fn(),
}));

describe('processedResultMediaLibraryService', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('requests permission only when called and returns a safe local asset identifier', async () => {
    MediaLibrary.requestPermissionsAsync.mockResolvedValue({ status: 'granted' });
    MediaLibrary.createAssetAsync.mockResolvedValue({ id: 'photo-library-id' });

    await expect(requestProcessedResultLibraryPermission()).resolves.toBe(true);
    await expect(createProcessedResultLibraryAsset({ uri: 'file:///cache/result.mp4' })).resolves.toEqual({
      localAssetIdentifier: 'photo-library-id',
    });
    expect(MediaLibrary.createAssetAsync).toHaveBeenCalledWith('file:///cache/result.mp4');
  });

  it('returns safe domain errors for denial and native failure', async () => {
    MediaLibrary.requestPermissionsAsync.mockResolvedValue({ status: 'denied' });
    MediaLibrary.createAssetAsync.mockRejectedValue(new Error('native details must not escape'));

    await expect(requestProcessedResultLibraryPermission()).rejects.toMatchObject({
      code: 'processed_result_library_permission_denied',
    });
    await expect(createProcessedResultLibraryAsset({ uri: 'file:///cache/result.mp4' })).rejects.toMatchObject({
      code: 'processed_result_library_save_failed',
    });
  });
});
