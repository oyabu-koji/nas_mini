import * as FileSystem from 'expo-file-system/legacy';

import {
  buildProcessedResultSource,
  sanitizeProcessedResult,
} from '../../../shared/api/mediaVaultApi';
import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';
import { hashWholeFile } from '../../asset-picker/services/streamingSha256Service';

export const PROCESSED_RESULT_DOWNLOAD_WATCHDOG_MS = 120000;

export async function downloadProcessedResult({
  settings,
  assetId,
  result,
  signal,
  onStage,
  onProgress,
  fileSystem = FileSystem,
  hashFile = hashWholeFile,
  watchdogMs = PROCESSED_RESULT_DOWNLOAD_WATCHDOG_MS,
}) {
  if (result && typeof result === 'object' && result.mime_type && result.mime_type !== 'video/mp4') {
    throw createAppError(
      'processed_result_unsupported_mime',
      messageForErrorCode('processed_result_unsupported_mime'),
    );
  }
  const safeResult = sanitizeProcessedResult(result, assetId);
  if (!safeResult) {
    throw createAppError(
      'processed_result_invalid_identity',
      messageForErrorCode('processed_result_invalid_identity'),
    );
  }
  if (safeResult.mime_type !== 'video/mp4') {
    throw createAppError(
      'processed_result_unsupported_mime',
      messageForErrorCode('processed_result_unsupported_mime'),
    );
  }
  if (!fileSystem.cacheDirectory) {
    throw createAppError(
      'processed_result_cache_unavailable',
      messageForErrorCode('processed_result_cache_unavailable'),
    );
  }

  const source = buildProcessedResultSource({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    assetId,
    result: safeResult,
  });
  const tempUri = processedResultTempUri({ result: safeResult, fileSystem });
  await cleanupProcessedResultTempFile({ uri: tempUri, fileSystem });
  onStage?.('downloading');

  let resumable;
  let cancellationReason = null;
  let cancellationPromise = null;
  let watchdogId = null;
  let lastBytesWritten = 0;
  let settled = false;

  const clearWatchdog = () => {
    if (watchdogId != null) {
      clearTimeout(watchdogId);
      watchdogId = null;
    }
  };
  const cancelOnce = (reason) => {
    if (cancellationReason) {
      return cancellationPromise ?? Promise.resolve();
    }
    cancellationReason = reason;
    clearWatchdog();
    cancellationPromise = Promise.resolve(resumable?.cancelAsync?.()).catch(() => undefined);
    return cancellationPromise;
  };
  const resetWatchdog = () => {
    clearWatchdog();
    watchdogId = setTimeout(() => {
      if (!settled) {
        void cancelOnce('timeout');
      }
    }, watchdogMs);
  };
  const onAbort = () => {
    if (!settled) {
      void cancelOnce('cancelled');
    }
  };

  if (signal?.aborted) {
    await cleanupProcessedResultTempFile({ uri: tempUri, fileSystem });
    throw cancellationError('cancelled');
  }

  try {
    resumable = fileSystem.createDownloadResumable(
      source.uri,
      tempUri,
      {
        headers: source.headers,
        cache: true,
        sessionType: fileSystem.FileSystemSessionType?.FOREGROUND,
      },
      (progress) => {
        const written = Number(progress?.totalBytesWritten ?? 0);
        if (written > lastBytesWritten) {
          lastBytesWritten = written;
          resetWatchdog();
        }
        onProgress?.(progress);
      },
    );
    signal?.addEventListener?.('abort', onAbort, { once: true });
    resetWatchdog();

    const response = await resumable.downloadAsync();
    settled = true;
    clearWatchdog();
    signal?.removeEventListener?.('abort', onAbort);
    if (cancellationReason) {
      await cancellationPromise;
      throw cancellationError(cancellationReason);
    }
    if (!response || response.status !== 200) {
      throw await responseError({ fileSystem, response, tempUri });
    }

    validateIdentityHeaders({ headers: response.headers, result: safeResult });
    const info = await fileSystem.getInfoAsync(tempUri, { size: true });
    if (!info?.exists || info.size !== safeResult.size_bytes) {
      throw integrityMismatchError();
    }
    onStage?.('verifying');
    const sha256 = await hashFile(tempUri);
    if (sha256 !== safeResult.sha256) {
      throw integrityMismatchError();
    }
    return {
      tempUri,
      result: safeResult,
    };
  } catch (error) {
    settled = true;
    clearWatchdog();
    signal?.removeEventListener?.('abort', onAbort);
    if (cancellationReason) {
      await cancellationPromise;
      await cleanupProcessedResultTempFile({ uri: tempUri, fileSystem });
      throw cancellationError(cancellationReason);
    }
    await cleanupProcessedResultTempFile({ uri: tempUri, fileSystem });
    if (error?.code) {
      throw error;
    }
    throw createAppError(
      'processed_result_download_failed',
      messageForErrorCode('processed_result_download_failed'),
      { retryable: true },
    );
  }
}

export function processedResultTempUri({ result, fileSystem = FileSystem }) {
  const resultId = String(result?.result_id ?? '');
  if (!/^[0-9a-f]{32}$/.test(resultId) || result?.mime_type !== 'video/mp4' || !fileSystem.cacheDirectory) {
    throw createAppError(
      result?.mime_type && result.mime_type !== 'video/mp4'
        ? 'processed_result_unsupported_mime'
        : 'processed_result_cache_unavailable',
      messageForErrorCode(
        result?.mime_type && result.mime_type !== 'video/mp4'
          ? 'processed_result_unsupported_mime'
          : 'processed_result_cache_unavailable',
      ),
    );
  }
  return `${fileSystem.cacheDirectory}mediavault-processed-${resultId}.mp4`;
}

export async function cleanupProcessedResultTempFile({ uri, fileSystem = FileSystem }) {
  if (!uri) {
    return;
  }
  try {
    await fileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // Cleanup is best effort and never changes a verified save outcome.
  }
}

export async function cleanupProcessedResultTempFiles({ records, fileSystem = FileSystem }) {
  await Promise.all(
    (Array.isArray(records) ? records : []).map((record) =>
      cleanupProcessedResultTempFile({
        uri: processedResultTempUri({
          result: { result_id: record.backendResultId, mime_type: 'video/mp4' },
          fileSystem,
        }),
        fileSystem,
      }),
    ),
  );
}

function validateIdentityHeaders({ headers, result }) {
  const resultId = readHeader(headers, 'x-processed-result-id');
  const sha256 = readHeader(headers, 'x-processed-result-sha256');
  const size = readHeader(headers, 'x-processed-result-size');
  if (resultId !== result.result_id || sha256 !== result.sha256 || size !== String(result.size_bytes)) {
    throw integrityMismatchError();
  }
}

function readHeader(headers, expectedName) {
  const matched = Object.entries(headers ?? {}).find(
    ([name]) => String(name).toLowerCase() === expectedName,
  );
  return matched ? String(matched[1]) : null;
}

async function responseError({ fileSystem, response, tempUri }) {
  const serverCode = await readResponseCode({ fileSystem, tempUri });
  const code =
    serverCode === 'processed_result_superseded'
      ? serverCode
      : serverCode === 'incompatible_client'
        ? serverCode
        : serverCode === 'formal_preview_not_ready'
          ? serverCode
          : serverCode === 'formal_preview_provenance_invalid'
            ? serverCode
      : serverCode === 'processed_result_not_ready'
        ? serverCode
        : serverCode === 'processed_result_range_not_satisfiable'
          ? serverCode
          : response?.status === 404
            ? 'processed_result_not_found'
            : response?.status === 416
              ? 'processed_result_range_not_satisfiable'
              : 'processed_result_download_failed';
  return createAppError(code, messageForErrorCode(code), {
    retryable: code === 'processed_result_download_failed',
  });
}

async function readResponseCode({ fileSystem, tempUri }) {
  try {
    const text = await fileSystem.readAsStringAsync(tempUri);
    const payload = JSON.parse(text);
    return typeof payload?.code === 'string' ? payload.code : null;
  } catch {
    return null;
  }
}

function integrityMismatchError() {
  return createAppError(
    'processed_result_integrity_mismatch',
    messageForErrorCode('processed_result_integrity_mismatch'),
  );
}

function cancellationError(reason) {
  const code = reason === 'timeout' ? 'processed_result_download_timeout' : 'processed_result_download_cancelled';
  return createAppError(code, messageForErrorCode(code), { retryable: true });
}
