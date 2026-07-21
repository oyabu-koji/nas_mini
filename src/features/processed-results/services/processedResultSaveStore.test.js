import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  PROCESSED_RESULT_SAVE_STORE_KEY,
  getProcessedResultSave,
  markProcessedResultFailed,
  markProcessedResultSaved,
  processedResultSaveKey,
  writeProcessedResultDownload,
  writeUnknownProcessedResultSave,
} from './processedResultSaveStore';

const identity = {
  backendAssetId: 42,
  backendResultId: 'a'.repeat(32),
  resultSha256: 'b'.repeat(64),
};

describe('processedResultSaveStore', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('uses a separate result-and-digest keyed store and never needs source mapping data', async () => {
    AsyncStorage.getItem.mockResolvedValue(null);

    const record = await writeProcessedResultDownload(identity);

    expect(processedResultSaveKey(identity)).toBe(`42:${identity.backendResultId}:${identity.resultSha256}`);
    expect(record).toMatchObject({ ...identity, saveStatus: 'downloading' });
    expect(JSON.parse(AsyncStorage.setItem.mock.calls[0][1])).toEqual({
      [processedResultSaveKey(identity)]: expect.objectContaining({
        ...identity,
        saveStatus: 'downloading',
      }),
    });
    expect(PROCESSED_RESULT_SAVE_STORE_KEY).not.toContain('localAssetMapping');
  });

  it('keeps an unknown write-ahead record until the native save has been recorded as saved', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce(null).mockResolvedValueOnce(
      JSON.stringify({
        [processedResultSaveKey(identity)]: {
          ...identity,
          saveStatus: 'unknown',
          savedLocalAssetIdentifier: null,
          saveAttemptedAt: '2026-07-18T00:00:00.000Z',
          lastErrorCode: null,
          updatedAt: '2026-07-18T00:00:00.000Z',
        },
      }),
    );

    await writeUnknownProcessedResultSave({
      ...identity,
      saveAttemptedAt: '2026-07-18T00:00:00.000Z',
    });
    const saved = await markProcessedResultSaved({
      ...identity,
      savedLocalAssetIdentifier: 'local-library-id',
    });

    expect(saved).toMatchObject({
      saveStatus: 'saved',
      savedLocalAssetIdentifier: 'local-library-id',
      saveAttemptedAt: '2026-07-18T00:00:00.000Z',
    });
  });

  it('returns an empty store for corrupt storage and only accepts safe local error codes', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce('{not-json').mockResolvedValueOnce(null);

    await expect(getProcessedResultSave(identity)).resolves.toBeNull();
    await expect(
      markProcessedResultFailed({ ...identity, lastErrorCode: 'processed_result_download_failed' }),
    ).resolves.toMatchObject({ saveStatus: 'failed' });
    await expect(
      markProcessedResultFailed({ ...identity, lastErrorCode: 'unsafe path / secret' }),
    ).rejects.toMatchObject({ code: 'processed_result_save_state_unavailable' });
  });
});
