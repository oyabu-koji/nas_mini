import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { createAppError } from '../../../shared/utils/errors';
import { useProcessedResultSave, useProcessedResultStartupCleanup } from './useProcessedResultSave';

jest.mock('../services/processedResultDownloadService', () => ({
  cleanupProcessedResultTempFile: jest.fn(),
  cleanupProcessedResultTempFiles: jest.fn(),
  downloadProcessedResult: jest.fn(),
}));
jest.mock('../services/processedResultMediaLibraryService', () => ({
  createProcessedResultLibraryAsset: jest.fn(),
  requestProcessedResultLibraryPermission: jest.fn(),
}));
jest.mock('../services/processedResultSaveStore', () => ({
  getProcessedResultSave: jest.fn(),
  listProcessedResultSaves: jest.fn(),
  markProcessedResultFailed: jest.fn(),
  markProcessedResultSaved: jest.fn(),
  writeProcessedResultDownload: jest.fn(),
  writeUnknownProcessedResultSave: jest.fn(),
}));

const download = require('../services/processedResultDownloadService');
const mediaLibrary = require('../services/processedResultMediaLibraryService');
const store = require('../services/processedResultSaveStore');

const result = {
  result_id: 'a'.repeat(32),
  mime_type: 'video/mp4',
  size_bytes: 10,
  sha256: 'b'.repeat(64),
  created_at: '2026-07-18T00:00:00Z',
  url: `/assets/42/results/${'a'.repeat(32)}`,
};
const replacementResult = {
  ...result,
  result_id: 'c'.repeat(32),
  sha256: 'd'.repeat(64),
  url: `/assets/42/results/${'c'.repeat(32)}`,
};

function HookHarness({ activeResult = result, onSuperseded }) {
  global.latestProcessedResultSave = useProcessedResultSave({
    settings: { backendUrl: 'http://backend.test', apiToken: 'secret-token' },
    assetId: 42,
    result: activeResult,
    onSuperseded,
  });
  return null;
}

function StartupCleanupHarness() {
  useProcessedResultStartupCleanup();
  return null;
}

describe('useProcessedResultSave', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    store.getProcessedResultSave.mockResolvedValue(null);
    store.writeProcessedResultDownload.mockResolvedValue();
    store.writeUnknownProcessedResultSave.mockResolvedValue();
    store.markProcessedResultSaved.mockResolvedValue();
    store.markProcessedResultFailed.mockResolvedValue();
    store.listProcessedResultSaves.mockResolvedValue([]);
    download.downloadProcessedResult.mockResolvedValue({ tempUri: 'file:///cache/result.mp4', result });
    download.cleanupProcessedResultTempFile.mockResolvedValue();
    download.cleanupProcessedResultTempFiles.mockResolvedValue();
    mediaLibrary.requestProcessedResultLibraryPermission.mockResolvedValue(true);
    mediaLibrary.createProcessedResultLibraryAsset.mockResolvedValue({ localAssetIdentifier: 'library-id' });
  });

  afterEach(() => {
    delete global.latestProcessedResultSave;
  });

  it('writes unknown before MediaLibrary, persists saved before temp cleanup, and never changes source mapping', async () => {
    await render(<HookHarness />);

    await act(async () => {
      await global.latestProcessedResultSave.save();
    });

    expect(store.writeUnknownProcessedResultSave).toHaveBeenCalledWith({
      backendAssetId: 42,
      backendResultId: result.result_id,
      resultSha256: result.sha256,
    });
    expect(store.writeUnknownProcessedResultSave.mock.invocationCallOrder[0]).toBeLessThan(
      mediaLibrary.createProcessedResultLibraryAsset.mock.invocationCallOrder[0],
    );
    expect(store.markProcessedResultSaved.mock.invocationCallOrder[0]).toBeLessThan(
      download.cleanupProcessedResultTempFile.mock.invocationCallOrder.at(-1),
    );
    expect(global.latestProcessedResultSave.status).toBe('saved');
  });

  it('does not invoke native save when write-ahead persistence fails', async () => {
    store.writeUnknownProcessedResultSave.mockRejectedValue(new Error('storage unavailable'));
    await render(<HookHarness />);

    await act(async () => {
      await global.latestProcessedResultSave.save();
    });

    expect(mediaLibrary.createProcessedResultLibraryAsset).not.toHaveBeenCalled();
    expect(global.latestProcessedResultSave.status).toBe('failed');
  });

  it('keeps unknown rather than showing saved when the saved record write fails', async () => {
    store.markProcessedResultSaved.mockRejectedValue(new Error('write failed'));
    await render(<HookHarness />);

    await act(async () => {
      await global.latestProcessedResultSave.save();
    });

    expect(mediaLibrary.createProcessedResultLibraryAsset).toHaveBeenCalledTimes(1);
    expect(global.latestProcessedResultSave.status).toBe('unknown');
    expect(global.latestProcessedResultSave.error.code).toBe('processed_result_save_outcome_unknown');
  });

  it('maps a known MediaLibrary failure to failed without changing backend state', async () => {
    mediaLibrary.createProcessedResultLibraryAsset.mockRejectedValue(
      createAppError('processed_result_library_save_failed', 'failed'),
    );
    await render(<HookHarness />);

    await act(async () => {
      await global.latestProcessedResultSave.save();
    });

    expect(store.markProcessedResultFailed).toHaveBeenCalledWith(expect.objectContaining({
      lastErrorCode: 'processed_result_library_save_failed',
    }));
    expect(global.latestProcessedResultSave.status).toBe('failed');
  });

  it('does not call the native save when permission or download fails', async () => {
    mediaLibrary.requestProcessedResultLibraryPermission.mockRejectedValue(
      createAppError('processed_result_library_permission_denied', 'denied'),
    );
    await render(<HookHarness />);

    await act(async () => {
      await global.latestProcessedResultSave.save();
    });

    expect(mediaLibrary.createProcessedResultLibraryAsset).not.toHaveBeenCalled();
    expect(store.markProcessedResultFailed).toHaveBeenCalledWith(expect.objectContaining({
      lastErrorCode: 'processed_result_library_permission_denied',
    }));

    jest.clearAllMocks();
    store.getProcessedResultSave.mockResolvedValue(null);
    store.writeProcessedResultDownload.mockResolvedValue();
    store.markProcessedResultFailed.mockResolvedValue();
    download.downloadProcessedResult.mockRejectedValue(
      createAppError('processed_result_download_failed', 'download failed'),
    );
    mediaLibrary.createProcessedResultLibraryAsset.mockResolvedValue({ localAssetIdentifier: 'library-id' });

    await act(async () => {
      await global.latestProcessedResultSave.save();
    });

    expect(mediaLibrary.requestProcessedResultLibraryPermission).not.toHaveBeenCalled();
    expect(mediaLibrary.createProcessedResultLibraryAsset).not.toHaveBeenCalled();
    expect(store.markProcessedResultFailed).toHaveBeenCalledWith(expect.objectContaining({
      lastErrorCode: 'processed_result_download_failed',
    }));
  });

  it('moves to superseded and asks the detail screen to refresh instead of saving another result', async () => {
    const onSuperseded = jest.fn();
    download.downloadProcessedResult.mockRejectedValue(
      createAppError('processed_result_superseded', 'superseded'),
    );
    await render(<HookHarness onSuperseded={onSuperseded} />);

    await act(async () => {
      await global.latestProcessedResultSave.save();
    });

    await waitFor(() => expect(onSuperseded).toHaveBeenCalledTimes(1));
    expect(global.latestProcessedResultSave.status).toBe('superseded');
    expect(mediaLibrary.createProcessedResultLibraryAsset).not.toHaveBeenCalled();
  });

  it('resets saved UI state when the active result identity changes', async () => {
    store.getProcessedResultSave.mockImplementation(async ({ backendResultId }) => (
      backendResultId === result.result_id
        ? {
            backendAssetId: 42,
            backendResultId: result.result_id,
            resultSha256: result.sha256,
            saveStatus: 'saved',
            savedLocalAssetIdentifier: 'old-library-id',
          }
        : null
    ));
    const view = await render(<HookHarness />);

    await waitFor(() => expect(global.latestProcessedResultSave.status).toBe('saved'));

    await act(async () => {
      view.rerender(<HookHarness activeResult={replacementResult} />);
    });

    await waitFor(() => {
      expect(global.latestProcessedResultSave.status).toBe('idle');
      expect(global.latestProcessedResultSave.savedLocalAssetIdentifier).toBeNull();
      expect(global.latestProcessedResultSave.canSave).toBe(true);
    });
  });

  it('keeps an old in-flight save from updating a replacement result UI', async () => {
    let resolveDownload;
    download.downloadProcessedResult.mockImplementation(
      () => new Promise((resolve) => {
        resolveDownload = resolve;
      }),
    );
    const view = await render(<HookHarness />);
    let pendingSave;

    await act(async () => {
      pendingSave = global.latestProcessedResultSave.save();
      await Promise.resolve();
    });
    expect(global.latestProcessedResultSave.status).toBe('downloading');

    await act(async () => {
      view.rerender(<HookHarness activeResult={replacementResult} />);
    });
    await waitFor(() => {
      expect(global.latestProcessedResultSave.status).toBe('idle');
      expect(global.latestProcessedResultSave.canSave).toBe(false);
    });

    resolveDownload({ tempUri: 'file:///cache/old-result.mp4', result });
    await act(async () => {
      await pendingSave;
    });

    expect(store.markProcessedResultSaved).toHaveBeenCalledWith(expect.objectContaining({
      backendResultId: result.result_id,
      resultSha256: result.sha256,
    }));
    expect(global.latestProcessedResultSave.status).toBe('idle');
    expect(global.latestProcessedResultSave.savedLocalAssetIdentifier).toBeNull();
    expect(global.latestProcessedResultSave.canSave).toBe(true);
  });

  it('offers cancellation only while the file download is active', async () => {
    let resolveDownload;
    download.downloadProcessedResult.mockImplementation(
      ({ onStage }) => new Promise((resolve) => {
        onStage('verifying');
        resolveDownload = resolve;
      }),
    );
    await render(<HookHarness />);
    let pendingSave;

    await act(async () => {
      pendingSave = global.latestProcessedResultSave.save();
      await Promise.resolve();
    });

    expect(global.latestProcessedResultSave.status).toBe('verifying');
    expect(global.latestProcessedResultSave.canCancel).toBe(false);

    resolveDownload({ tempUri: 'file:///cache/result.mp4', result });
    await act(async () => {
      await pendingSave;
    });
    expect(global.latestProcessedResultSave.status).toBe('saved');
  });

  it('cleans persisted result temp files once at app startup without changing their save records', async () => {
    const records = [{ backendResultId: result.result_id, saveStatus: 'unknown' }];
    store.listProcessedResultSaves.mockResolvedValue(records);

    render(<StartupCleanupHarness />);

    await waitFor(() => {
      expect(download.cleanupProcessedResultTempFiles).toHaveBeenCalledWith({ records });
    });
    expect(store.markProcessedResultFailed).not.toHaveBeenCalled();
    expect(store.markProcessedResultSaved).not.toHaveBeenCalled();
  });
});
