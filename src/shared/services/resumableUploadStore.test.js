import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  readResumableUploadRecord,
  removeResumableUploadRecord,
  saveResumableUploadRecord,
  updateResumableUploadProgress,
  updateResumableUploadSessionId,
} from './resumableUploadStore';

const record = {
  localAssetId: 'asset-123',
  clientUploadId: 'client-upload-123',
  sessionId: null,
  sizeBytes: 16,
  expectedFileSha256: 'a'.repeat(64),
  uploadedBytes: 0,
  uri: 'file:///must-not-be-saved.mov',
  apiToken: 'must-not-be-saved',
  filename: 'must-not-be-saved.mov',
};

describe('resumableUploadStore', () => {
  beforeEach(() => {
    AsyncStorage.clear();
    jest.clearAllMocks();
  });

  it('persists the nullable session record before session create and updates only allowed fields', async () => {
    await saveResumableUploadRecord(record);
    const stored = await readResumableUploadRecord();
    const withSession = await updateResumableUploadSessionId(record.clientUploadId, 'session-123');
    const progressed = await updateResumableUploadProgress(record.clientUploadId, 8);

    expect(stored).toEqual({
      localAssetId: 'asset-123',
      clientUploadId: 'client-upload-123',
      sessionId: null,
      sizeBytes: 16,
      expectedFileSha256: 'a'.repeat(64),
      uploadedBytes: 0,
    });
    expect(withSession.sessionId).toBe('session-123');
    expect(progressed.uploadedBytes).toBe(8);
  });

  it('cleans corrupt state and only removes matching terminal records', async () => {
    await AsyncStorage.setItem('mediavault.resumableVideoUpload', '{invalid');
    await expect(readResumableUploadRecord()).resolves.toBeNull();

    await saveResumableUploadRecord(record);
    await removeResumableUploadRecord('other-upload');
    expect(await readResumableUploadRecord()).not.toBeNull();
    await removeResumableUploadRecord(record.clientUploadId);
    await expect(readResumableUploadRecord()).resolves.toBeNull();
  });
});
