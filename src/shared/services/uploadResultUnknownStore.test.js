import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  blocksUploadForAsset,
  clearUploadResultUnknown,
  readUploadResultUnknown,
  saveUploadResultUnknown,
} from './uploadResultUnknownStore';

describe('uploadResultUnknownStore', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('persists only a local asset identifier and restores it', async () => {
    await saveUploadResultUnknown({ kind: 'local_asset', localAssetId: 'local-123' });
    AsyncStorage.getItem.mockResolvedValueOnce('{"kind":"local_asset","localAssetId":"local-123"}');

    const restored = await readUploadResultUnknown();

    expect(AsyncStorage.setItem).toHaveBeenCalledWith(
      'mediavault.uploadResultUnknown',
      '{"kind":"local_asset","localAssetId":"local-123"}',
    );
    expect(restored).toEqual({ kind: 'local_asset', localAssetId: 'local-123' });
    expect(blocksUploadForAsset(restored, 'local-123')).toBe(true);
    expect(blocksUploadForAsset(restored, 'local-456')).toBe(false);
  });

  it('uses a global lock for malformed or unreadable storage without deleting it', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce('{bad');
    const malformed = await readUploadResultUnknown();
    AsyncStorage.getItem.mockRejectedValueOnce(new Error('storage unavailable'));
    const unreadable = await readUploadResultUnknown();

    expect(malformed).toEqual({ kind: 'global_pending' });
    expect(unreadable).toEqual({ kind: 'global_pending' });
    expect(AsyncStorage.removeItem).not.toHaveBeenCalled();
  });

  it('clears the pending marker only when explicitly requested', async () => {
    await clearUploadResultUnknown();

    expect(AsyncStorage.removeItem).toHaveBeenCalledWith('mediavault.uploadResultUnknown');
  });
});
