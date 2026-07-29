import * as FileSystem from 'expo-file-system/legacy';

import { hashWholeFile } from '../../asset-picker/services/streamingSha256Service';
import {
  cleanupProcessedResultTempFiles,
  downloadProcessedResult,
  processedResultTempUri,
} from './processedResultDownloadService';

jest.mock('expo-file-system/legacy', () => ({
  cacheDirectory: 'file:///cache/',
  FileSystemSessionType: { FOREGROUND: 'FOREGROUND' },
  createDownloadResumable: jest.fn(),
  deleteAsync: jest.fn(),
  getInfoAsync: jest.fn(),
  readAsStringAsync: jest.fn(),
}));

jest.mock('../../asset-picker/services/streamingSha256Service', () => ({
  hashWholeFile: jest.fn(),
}));

const settings = { backendUrl: 'http://mediavault', apiToken: 'secret-token' };
const result = {
  result_id: 'a'.repeat(32),
  mime_type: 'video/mp4',
  size_bytes: 10,
  sha256: 'b'.repeat(64),
  created_at: '2026-07-18T00:00:00Z',
  url: `/assets/42/results/${'a'.repeat(32)}`,
};

function prepareDownload({ status = 200, headers, onDownload } = {}) {
  const resumable = {
    downloadAsync: jest.fn(async () => {
      onDownload?.();
      return {
        status,
        headers:
          headers ?? {
            'X-Processed-Result-Id': result.result_id,
            'X-Processed-Result-SHA256': result.sha256,
            'X-Processed-Result-Size': String(result.size_bytes),
          },
      };
    }),
    cancelAsync: jest.fn().mockResolvedValue(),
  };
  FileSystem.createDownloadResumable.mockReturnValue(resumable);
  return resumable;
}

describe('processedResultDownloadService', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // The Jest module mock intentionally exposes mutable cache availability.
    // eslint-disable-next-line import/namespace
    FileSystem.cacheDirectory = 'file:///cache/';
    FileSystem.deleteAsync.mockResolvedValue();
    FileSystem.getInfoAsync.mockResolvedValue({ exists: true, size: 10 });
    FileSystem.readAsStringAsync.mockResolvedValue('{}');
    hashWholeFile.mockResolvedValue(result.sha256);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('downloads only the canonical authenticated source then verifies headers, size, and native SHA-256', async () => {
    prepareDownload();

    await expect(downloadProcessedResult({ settings, assetId: 42, result })).resolves.toEqual({
      tempUri: `file:///cache/mediavault-processed-${result.result_id}.mp4`,
      result,
    });

    expect(FileSystem.createDownloadResumable).toHaveBeenCalledWith(
      `http://mediavault/assets/42/results/${result.result_id}`,
      `file:///cache/mediavault-processed-${result.result_id}.mp4`,
      expect.objectContaining({
        headers: {
          Authorization: 'Bearer secret-token',
          'X-MediaVault-Client-Version': '0.3.0',
        },
      }),
      expect.any(Function),
    );
    expect(hashWholeFile).toHaveBeenCalledWith(`file:///cache/mediavault-processed-${result.result_id}.mp4`);
  });

  it('rejects unsupported MIME and unsafe response URL before creating an authenticated download', async () => {
    await expect(
      downloadProcessedResult({
        settings,
        assetId: 42,
        result: { ...result, mime_type: 'video/quicktime' },
      }),
    ).rejects.toMatchObject({ code: 'processed_result_unsupported_mime' });
    await expect(
      downloadProcessedResult({
        settings,
        assetId: 42,
        result: { ...result, url: 'https://attacker.invalid/file.mp4' },
      }),
    ).rejects.toMatchObject({ code: 'processed_result_invalid_identity' });
    expect(FileSystem.createDownloadResumable).not.toHaveBeenCalled();
  });

  it('rejects an invalid backend URL before token coercion or download adapter creation', async () => {
    const token = { toString: jest.fn(() => 'secret-token') };

    await expect(
      downloadProcessedResult({
        settings: {
          backendUrl: 'http://results.example.com',
          apiToken: token,
        },
        assetId: 42,
        result,
      }),
    ).rejects.toMatchObject({ code: 'invalid_url' });

    expect(token.toString).not.toHaveBeenCalled();
    expect(FileSystem.createDownloadResumable).not.toHaveBeenCalled();
  });

  it('fails before download when the app cache is unavailable', async () => {
    // The Jest module mock intentionally exposes mutable cache availability.
    // eslint-disable-next-line import/namespace
    FileSystem.cacheDirectory = null;

    await expect(downloadProcessedResult({ settings, assetId: 42, result })).rejects.toMatchObject({
      code: 'processed_result_cache_unavailable',
    });
    expect(FileSystem.createDownloadResumable).not.toHaveBeenCalled();
  });

  it('cleans up and rejects a header or digest mismatch without allowing save', async () => {
    prepareDownload({
      headers: {
        'x-processed-result-id': result.result_id,
        'x-processed-result-sha256': 'c'.repeat(64),
        'x-processed-result-size': '10',
      },
    });

    await expect(downloadProcessedResult({ settings, assetId: 42, result })).rejects.toMatchObject({
      code: 'processed_result_integrity_mismatch',
    });
    expect(FileSystem.deleteAsync).toHaveBeenCalledWith(
      `file:///cache/mediavault-processed-${result.result_id}.mp4`,
      { idempotent: true },
    );
  });

  it('maps user cancellation through cancelAsync once', async () => {
    const abortController = new AbortController();
    const resumable = prepareDownload({
      onDownload: () => abortController.abort(),
    });
    await expect(
      downloadProcessedResult({
        settings,
        assetId: 42,
        result,
        signal: abortController.signal,
      }),
    ).rejects.toMatchObject({ code: 'processed_result_download_cancelled' });
    expect(resumable.cancelAsync).toHaveBeenCalledTimes(1);
  });

  it('cancels exactly once after 120 seconds without progress and cleans up after settlement', async () => {
    jest.useFakeTimers();
    let rejectDownload;
    const resumable = {
      downloadAsync: jest.fn(
        () =>
          new Promise((_resolve, reject) => {
            rejectDownload = reject;
          }),
      ),
      cancelAsync: jest.fn(async () => {
        rejectDownload(new Error('cancelled'));
      }),
    };
    FileSystem.createDownloadResumable.mockReturnValue(resumable);

    const pending = downloadProcessedResult({ settings, assetId: 42, result }).catch((error) => error);
    await jest.advanceTimersByTimeAsync(120000);

    await expect(pending).resolves.toMatchObject({ code: 'processed_result_download_timeout' });
    expect(resumable.cancelAsync).toHaveBeenCalledTimes(1);
    expect(FileSystem.deleteAsync).toHaveBeenCalledWith(
      `file:///cache/mediavault-processed-${result.result_id}.mp4`,
      { idempotent: true },
    );
    jest.useRealTimers();
  });

  it('maps stable server supersession errors after cleaning the error response file', async () => {
    prepareDownload({ status: 409 });
    FileSystem.readAsStringAsync.mockResolvedValue(JSON.stringify({ code: 'processed_result_superseded' }));

    await expect(downloadProcessedResult({ settings, assetId: 42, result })).rejects.toMatchObject({
      code: 'processed_result_superseded',
    });
  });

  it.each([
    { status: 404, payload: null, expectedCode: 'processed_result_not_found' },
    { status: 409, payload: 'processed_result_not_ready', expectedCode: 'processed_result_not_ready' },
    {
      status: 416,
      payload: 'processed_result_range_not_satisfiable',
      expectedCode: 'processed_result_range_not_satisfiable',
    },
  ])('maps result delivery failure $status to a safe Mobile domain error', async ({ status, payload, expectedCode }) => {
    prepareDownload({ status });
    FileSystem.readAsStringAsync.mockResolvedValue(payload ? JSON.stringify({ code: payload }) : '{}');

    await expect(downloadProcessedResult({ settings, assetId: 42, result })).rejects.toMatchObject({
      code: expectedCode,
    });
  });

  it('maps a download transport failure to a retryable processed-result error and removes the temp file', async () => {
    FileSystem.createDownloadResumable.mockReturnValue({
      downloadAsync: jest.fn().mockRejectedValue(new Error('network unavailable')),
      cancelAsync: jest.fn().mockResolvedValue(),
    });

    await expect(downloadProcessedResult({ settings, assetId: 42, result })).rejects.toMatchObject({
      code: 'processed_result_download_failed',
      retryable: true,
    });
    expect(FileSystem.deleteAsync).toHaveBeenCalledWith(
      `file:///cache/mediavault-processed-${result.result_id}.mp4`,
      { idempotent: true },
    );
  });

  it('cleans startup leftovers only from a result ID-derived cache path', async () => {
    await cleanupProcessedResultTempFiles({
      records: [{ backendResultId: result.result_id }],
    });

    expect(FileSystem.deleteAsync).toHaveBeenCalledWith(
      `file:///cache/mediavault-processed-${result.result_id}.mp4`,
      { idempotent: true },
    );
  });

  it('derives an mp4 temp URI only from the validated result ID', () => {
    expect(processedResultTempUri({ result })).toBe(
      `file:///cache/mediavault-processed-${result.result_id}.mp4`,
    );
    expect(() => processedResultTempUri({ result: { ...result, mime_type: 'image/jpeg' } })).toThrow(
      'not supported',
    );
  });
});
