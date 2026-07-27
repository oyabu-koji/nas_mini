import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { createTimeoutError } from '../../../shared/utils/errors';
import { useAssetUpload } from './useAssetUpload';

jest.mock('../../../shared/api/mediaVaultApi', () => ({
  uploadAsset: jest.fn(),
}));
jest.mock('../../../shared/services/localAssetMappingStore', () => ({
  saveLocalAssetMapping: jest.fn(),
}));
jest.mock('../../../shared/services/uploadResultUnknownStore', () => ({
  blocksAssetSelection: jest.fn((pending) => pending?.kind === 'global_pending'),
  blocksUploadForAsset: jest.fn((pending, localAssetId) => {
    if (!pending) {
      return false;
    }
    return pending.kind === 'global_pending' || pending.localAssetId === localAssetId;
  }),
  readUploadResultUnknown: jest.fn(),
  saveUploadResultUnknown: jest.fn(),
}));
jest.mock('../services/mediaPickerService', () => ({
  pickSingleMediaAsset: jest.fn(),
}));
jest.mock('./useResumableVideoUpload', () => ({
  useResumableVideoUpload: jest.fn(),
}));

const { uploadAsset } = require('../../../shared/api/mediaVaultApi');
const { saveLocalAssetMapping } = require('../../../shared/services/localAssetMappingStore');
const pendingStore = require('../../../shared/services/uploadResultUnknownStore');
const { pickSingleMediaAsset } = require('../services/mediaPickerService');
const { useResumableVideoUpload } = require('./useResumableVideoUpload');

const pickedAsset = {
  uri: 'media-reference',
  localAssetId: 'local-123',
  type: 'image',
  filename: 'photo.jpg',
  sizeBytes: 10,
};

function UploadHarness({ onMappingUnavailable, onUploaded }) {
  const upload = useAssetUpload({
    settings: { backendUrl: 'http://mediavault', apiToken: 'masked' },
    canUseApi: true,
    onMappingUnavailable,
    onUploaded,
  });
  global.latestUploadHook = upload;
  return null;
}

describe('useAssetUpload', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useResumableVideoUpload.mockReturnValue({
      status: 'idle',
      error: null,
      canStart: true,
      canCancel: false,
      startUpload: jest.fn(),
      cancelUpload: jest.fn(),
    });
    pendingStore.readUploadResultUnknown.mockResolvedValue(null);
  });

  afterEach(() => {
    delete global.latestUploadHook;
  });

  it('blocks selection until pending restoration completes', async () => {
    let resolvePending;
    pendingStore.readUploadResultUnknown.mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePending = resolve;
      }),
    );
    await render(<UploadHarness />);

    await act(async () => {
      await global.latestUploadHook.pickAsset();
    });
    expect(pickSingleMediaAsset).not.toHaveBeenCalled();

    await act(async () => {
      resolvePending(null);
    });
    await waitFor(() => expect(global.latestUploadHook.pendingLoading).toBe(false));
  });

  it('rechecks pending storage immediately before sending an upload', async () => {
    pendingStore.readUploadResultUnknown
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ kind: 'global_pending' });
    pickSingleMediaAsset.mockResolvedValue({ canceled: false, asset: pickedAsset });
    await render(<UploadHarness />);
    await waitFor(() => expect(global.latestUploadHook.pendingLoading).toBe(false));

    await act(async () => {
      await global.latestUploadHook.pickAsset();
    });
    await waitFor(() => expect(global.latestUploadHook.pickedAsset).toEqual(pickedAsset));
    await act(async () => {
      await global.latestUploadHook.startUpload();
    });

    expect(uploadAsset).not.toHaveBeenCalled();
    expect(global.latestUploadHook.status).toBe('result_unknown');
  });

  it('blocks only the matching local asset when pending storage is restored before send', async () => {
    pendingStore.readUploadResultUnknown
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ kind: 'local_asset', localAssetId: 'local-123' });
    pickSingleMediaAsset.mockResolvedValue({ canceled: false, asset: pickedAsset });
    await render(<UploadHarness />);
    await waitFor(() => expect(global.latestUploadHook.pendingLoading).toBe(false));

    await act(async () => {
      await global.latestUploadHook.pickAsset();
    });
    await waitFor(() => expect(global.latestUploadHook.pickedAsset).toEqual(pickedAsset));
    await act(async () => {
      await global.latestUploadHook.startUpload();
    });

    expect(uploadAsset).not.toHaveBeenCalled();
    expect(global.latestUploadHook.status).toBe('result_unknown');
  });

  it('persists a timeout as result unknown before allowing another upload', async () => {
    uploadAsset.mockRejectedValue(createTimeoutError());
    pickSingleMediaAsset.mockResolvedValue({ canceled: false, asset: pickedAsset });
    await render(<UploadHarness />);
    await waitFor(() => expect(global.latestUploadHook.pendingLoading).toBe(false));

    await act(async () => {
      await global.latestUploadHook.pickAsset();
    });
    await waitFor(() => expect(global.latestUploadHook.pickedAsset).toEqual(pickedAsset));
    await act(async () => {
      await global.latestUploadHook.startUpload();
    });

    expect(pendingStore.saveUploadResultUnknown).toHaveBeenCalledWith({
      kind: 'local_asset',
      localAssetId: 'local-123',
    });
    expect(global.latestUploadHook.status).toBe('result_unknown');
  });

  it('keeps successful navigation when local mapping persistence fails', async () => {
    const onMappingUnavailable = jest.fn();
    const onUploaded = jest.fn();
    uploadAsset.mockResolvedValue({ asset: { id: 42 } });
    saveLocalAssetMapping.mockRejectedValue(new Error('mapping unavailable'));
    pickSingleMediaAsset.mockResolvedValue({ canceled: false, asset: pickedAsset });
    await render(<UploadHarness onMappingUnavailable={onMappingUnavailable} onUploaded={onUploaded} />);
    await waitFor(() => expect(global.latestUploadHook.pendingLoading).toBe(false));

    await act(async () => {
      await global.latestUploadHook.pickAsset();
    });
    await waitFor(() => expect(global.latestUploadHook.pickedAsset).toEqual(pickedAsset));
    await act(async () => {
      await global.latestUploadHook.startUpload();
    });

    expect(onUploaded).toHaveBeenCalledWith(42);
    expect(global.latestUploadHook.status).toBe('uploaded');
    await waitFor(() => expect(onMappingUnavailable).toHaveBeenCalledWith(42));
  });

  it('starts only one request when upload is invoked twice before pending recheck completes', async () => {
    let resolveUpload;
    uploadAsset.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );
    pickSingleMediaAsset.mockResolvedValue({ canceled: false, asset: pickedAsset });
    await render(<UploadHarness />);
    await waitFor(() => expect(global.latestUploadHook.pendingLoading).toBe(false));

    await act(async () => {
      await global.latestUploadHook.pickAsset();
    });
    await waitFor(() => expect(global.latestUploadHook.pickedAsset).toEqual(pickedAsset));

    let firstUpload;
    let secondUpload;
    await act(async () => {
      firstUpload = global.latestUploadHook.startUpload();
      secondUpload = global.latestUploadHook.startUpload();
      await Promise.resolve();
    });
    await waitFor(() => expect(uploadAsset).toHaveBeenCalledTimes(1));
    expect(pendingStore.readUploadResultUnknown).toHaveBeenCalledTimes(2);

    await act(async () => {
      resolveUpload({ asset: { id: 42 } });
      await Promise.all([firstUpload, secondUpload]);
    });

    expect(uploadAsset).toHaveBeenCalledTimes(1);
  });

  it('routes videos to the resumable hook without the Phase 1 size limit', async () => {
    const startUpload = jest.fn().mockResolvedValue(undefined);
    useResumableVideoUpload.mockReturnValue({
      status: 'uploading_chunks',
      error: null,
      canStart: true,
      canCancel: true,
      startUpload,
      cancelUpload: jest.fn(),
    });
    pickSingleMediaAsset.mockResolvedValue({
      canceled: false,
      asset: { ...pickedAsset, type: 'video', filename: 'clip.mov', sizeBytes: 200 * 1024 * 1024 },
    });
    await render(<UploadHarness />);
    await waitFor(() => expect(global.latestUploadHook.pendingLoading).toBe(false));

    await act(async () => {
      await global.latestUploadHook.pickAsset();
    });
    await act(async () => {
      await global.latestUploadHook.startUpload();
    });

    expect(startUpload).toHaveBeenCalled();
    expect(uploadAsset).not.toHaveBeenCalled();
    expect(global.latestUploadHook.isTooLarge).toBe(false);
  });
});
