import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { createTimeoutError } from '../../../shared/utils/errors';
import { useResumableVideoUpload } from './useResumableVideoUpload';

jest.mock('../../../shared/api/mediaVaultApi', () => ({
  cancelUploadSession: jest.fn(),
  createUploadSession: jest.fn(),
  finalizeUploadSession: jest.fn(),
  getUploadSession: jest.fn(),
  uploadUploadSessionChunk: jest.fn(),
}));
jest.mock('../../../shared/services/localAssetMappingStore', () => ({
  saveLocalAssetMapping: jest.fn(),
}));
jest.mock('../../../shared/services/resumableUploadStore', () => ({
  readResumableUploadRecord: jest.fn(),
  removeResumableUploadRecord: jest.fn(),
  saveResumableUploadRecord: jest.fn(),
  updateResumableUploadProgress: jest.fn(),
  updateResumableUploadSessionId: jest.fn(),
}));
jest.mock('../services/resumableVideoMediaService', () => ({
  resolveResumableVideoAsset: jest.fn(),
}));
jest.mock('../services/streamingSha256Service', () => ({
  hashFileRange: jest.fn(),
  hashWholeFile: jest.fn(),
}));

const api = require('../../../shared/api/mediaVaultApi');
const mapping = require('../../../shared/services/localAssetMappingStore');
const store = require('../../../shared/services/resumableUploadStore');
const media = require('../services/resumableVideoMediaService');
const hashing = require('../services/streamingSha256Service');

const pickedVideo = {
  type: 'video',
  localAssetId: 'library-asset-123',
  filename: 'clip.mov',
  sizeBytes: 16,
  takenAt: null,
  latitude: null,
  longitude: null,
  exif: null,
};
const record = {
  localAssetId: 'library-asset-123',
  clientUploadId: 'client-upload-123',
  sessionId: null,
  sizeBytes: 16,
  expectedFileSha256: 'a'.repeat(64),
  uploadedBytes: 0,
};

function HookHarness({ asset = pickedVideo, onUploaded, onMappingUnavailable }) {
  global.latestResumableHook = useResumableVideoUpload({
    settings: { backendUrl: 'http://mediavault', apiToken: 'masked' },
    pickedAsset: asset,
    isLog: false,
    canUseApi: true,
    onUploaded,
    onMappingUnavailable,
  });
  return null;
}

describe('useResumableVideoUpload', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    store.readResumableUploadRecord.mockResolvedValue(null);
    store.saveResumableUploadRecord.mockImplementation(async (value) => value);
    store.updateResumableUploadSessionId.mockImplementation(async (_id, sessionId) => ({ ...record, sessionId }));
    store.updateResumableUploadProgress.mockImplementation(async (_id, uploadedBytes) => ({ ...record, uploadedBytes }));
    media.resolveResumableVideoAsset.mockResolvedValue({ ...pickedVideo, uri: 'file:///clip.mov' });
    hashing.hashWholeFile.mockResolvedValue('a'.repeat(64));
    hashing.hashFileRange.mockResolvedValue('b'.repeat(64));
  });

  afterEach(() => {
    delete global.latestResumableHook;
  });

  it('persists a nullable record before create, uploads only missing chunks, and maps after completion', async () => {
    const onUploaded = jest.fn();
    api.createUploadSession.mockResolvedValue({ id: 'session-123' });
    api.getUploadSession
      .mockResolvedValueOnce({
        id: 'session-123',
        status: 'created',
        size_bytes: 16,
        chunk_size_bytes: 8,
        missing_chunk_indexes: [0, 1],
      })
      .mockResolvedValueOnce({
        id: 'session-123',
        status: 'completed',
        size_bytes: 16,
        chunk_size_bytes: 8,
        missing_chunk_indexes: [],
        asset_id: 42,
      });
    api.finalizeUploadSession.mockResolvedValue({ session: { id: 'session-123', status: 'assembling' } });

    await render(<HookHarness onUploaded={onUploaded} />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(store.saveResumableUploadRecord).toHaveBeenCalledWith(expect.objectContaining({ sessionId: null }));
    expect(store.saveResumableUploadRecord.mock.invocationCallOrder[0]).toBeLessThan(
      api.createUploadSession.mock.invocationCallOrder[0],
    );
    expect(api.uploadUploadSessionChunk).toHaveBeenCalledTimes(2);
    expect(hashing.hashFileRange).toHaveBeenCalledWith('file:///clip.mov', 0, 8);
    expect(hashing.hashFileRange).toHaveBeenCalledWith('file:///clip.mov', 8, 8);
    expect(onUploaded).toHaveBeenCalledWith(42);
    expect(mapping.saveLocalAssetMapping).toHaveBeenCalledWith({ backendAssetId: 42, localAssetId: 'library-asset-123' });
    expect(store.removeResumableUploadRecord).toHaveBeenCalledWith('client-upload-123');
    expect(global.latestResumableHook.status).toBe('completed');
  });

  it('keeps the pre-create record after a lost create response for same-key recovery', async () => {
    api.createUploadSession.mockRejectedValue(createTimeoutError());

    await render(<HookHarness />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(store.saveResumableUploadRecord).toHaveBeenCalledWith(expect.objectContaining({ sessionId: null }));
    expect(store.updateResumableUploadSessionId).not.toHaveBeenCalled();
    expect(store.removeResumableUploadRecord).not.toHaveBeenCalled();
    expect(global.latestResumableHook.status).toBe('retryable_failed');
  });

  it('recovers a lost create response after restart with the saved client upload id', async () => {
    store.readResumableUploadRecord.mockResolvedValue(record);
    api.createUploadSession.mockResolvedValue({ id: 'session-123' });
    api.getUploadSession.mockResolvedValueOnce({
      id: 'session-123',
      status: 'completed',
      size_bytes: 16,
      chunk_size_bytes: 8,
      missing_chunk_indexes: [],
      asset_id: 42,
    });

    await render(<HookHarness />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(store.saveResumableUploadRecord).not.toHaveBeenCalled();
    expect(api.createUploadSession).toHaveBeenCalledWith(expect.objectContaining({
      session: expect.objectContaining({ client_upload_id: 'client-upload-123' }),
    }));
  });

  it('keeps a saved session id when its status fetch is interrupted', async () => {
    store.readResumableUploadRecord.mockResolvedValue({ ...record, sessionId: 'session-123' });
    api.getUploadSession.mockRejectedValue(createTimeoutError());

    await render(<HookHarness />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(api.getUploadSession).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'session-123' }));
    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(store.removeResumableUploadRecord).not.toHaveBeenCalled();
    expect(global.latestResumableHook.status).toBe('retryable_failed');
  });

  it('requires an explicit new upload when the saved video hash changes', async () => {
    let savedRecord = { ...record, expectedFileSha256: 'c'.repeat(64) };
    store.readResumableUploadRecord.mockImplementation(async () => savedRecord);
    store.removeResumableUploadRecord.mockImplementation(async () => {
      savedRecord = null;
    });
    api.createUploadSession.mockResolvedValue({ id: 'new-session-123' });
    api.getUploadSession.mockResolvedValue({
      id: 'new-session-123',
      status: 'completed',
      size_bytes: 16,
      chunk_size_bytes: 8,
      missing_chunk_indexes: [],
      asset_id: 42,
    });

    await render(<HookHarness />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(store.removeResumableUploadRecord).toHaveBeenCalledWith('client-upload-123');
    expect(global.latestResumableHook.status).toBe('terminal_failed');

    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(store.saveResumableUploadRecord).toHaveBeenCalledWith(expect.objectContaining({
      clientUploadId: expect.not.stringMatching(/^client-upload-123$/),
      expectedFileSha256: 'a'.repeat(64),
    }));
    expect(api.createUploadSession).toHaveBeenCalledTimes(1);
  });

  it('does not create a session when the resolved video size changes', async () => {
    store.readResumableUploadRecord.mockResolvedValue({ ...record, sizeBytes: 8 });

    await render(<HookHarness />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(store.removeResumableUploadRecord).toHaveBeenCalledWith('client-upload-123');
    expect(global.latestResumableHook.status).toBe('terminal_failed');
  });

  it('does not create a session when Photo Library resolution fails', async () => {
    const { createAppError } = require('../../../shared/utils/errors');
    media.resolveResumableVideoAsset.mockRejectedValue(createAppError('media_unavailable', 'unavailable'));

    await render(<HookHarness />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(global.latestResumableHook.status).toBe('media_unavailable');
  });

  it('does not create a session for a null photo-library asset id', async () => {
    const { createAppError } = require('../../../shared/utils/errors');
    media.resolveResumableVideoAsset.mockRejectedValue(
      createAppError('resumable_video_requires_library_asset', 'library asset required'),
    );

    await render(<HookHarness asset={{ ...pickedVideo, localAssetId: null }} />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(global.latestResumableHook.status).toBe('terminal_failed');
  });

  it('clears the resume record for a terminal session failure without creating a duplicate', async () => {
    store.readResumableUploadRecord.mockResolvedValue({ ...record, sessionId: 'session-123' });
    api.getUploadSession.mockResolvedValue({
      id: 'session-123',
      status: 'failed',
      size_bytes: 16,
      chunk_size_bytes: 8,
      missing_chunk_indexes: [],
      retryable: false,
    });

    await render(<HookHarness />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(store.removeResumableUploadRecord).toHaveBeenCalledWith('client-upload-123');
    expect(global.latestResumableHook.status).toBe('terminal_failed');
  });

  it('treats local mapping persistence as best effort after completed finalization', async () => {
    const onMappingUnavailable = jest.fn();
    api.createUploadSession.mockResolvedValue({ id: 'session-123' });
    api.getUploadSession
      .mockResolvedValueOnce({
        id: 'session-123',
        status: 'completed',
        size_bytes: 16,
        chunk_size_bytes: 8,
        missing_chunk_indexes: [],
        asset_id: 42,
      });
    mapping.saveLocalAssetMapping.mockRejectedValue(new Error('storage unavailable'));

    await render(<HookHarness onMappingUnavailable={onMappingUnavailable} />);
    await act(async () => {
      await global.latestResumableHook.startUpload();
    });

    await waitFor(() => expect(onMappingUnavailable).toHaveBeenCalledWith(42));
    expect(global.latestResumableHook.status).toBe('completed');
  });
});
