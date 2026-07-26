import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  ORIGINAL_DELETION_STORE_KEY,
  readOriginalDeletionOutcome,
  writeOriginalDeletionOutcome,
} from './originalDeletionStore';

describe('originalDeletionStore', () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
  });

  it('stores only terminal status, fixed code, and timestamp in its own namespace', async () => {
    const record = await writeOriginalDeletionOutcome({
      backendAssetId: 42,
      status: 'failed',
      errorCode: 'original_delete_permission_denied',
      now: () => new Date('2026-07-24T00:00:00Z'),
    });

    expect(record).toEqual({
      backendAssetId: 42,
      status: 'failed',
      errorCode: 'original_delete_permission_denied',
      updatedAt: '2026-07-24T00:00:00.000Z',
    });
    expect(await readOriginalDeletionOutcome(42)).toEqual(record);
    const raw = await AsyncStorage.getItem(ORIGINAL_DELETION_STORE_KEY);
    expect(raw).not.toContain('localAssetId');
    expect(raw).not.toContain('deleting');
  });

  it('rejects nonterminal or malformed records', async () => {
    await expect(writeOriginalDeletionOutcome({
      backendAssetId: 42,
      status: 'deleting',
    })).rejects.toMatchObject({ code: 'original_delete_state_unavailable' });
    await expect(writeOriginalDeletionOutcome({
      backendAssetId: 42,
      status: 'failed',
      errorCode: 'original_delete_private_native_detail',
    })).rejects.toMatchObject({ code: 'original_delete_state_unavailable' });
    await AsyncStorage.setItem(ORIGINAL_DELETION_STORE_KEY, '{bad-json');
    await expect(readOriginalDeletionOutcome(42)).rejects.toMatchObject({
      code: 'original_delete_state_unavailable',
    });
  });
});
