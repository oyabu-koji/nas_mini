import { useCallback, useRef, useState } from 'react';

import {
  cancelUploadSession,
  createUploadSession,
  finalizeUploadSession,
  getUploadSession,
  uploadUploadSessionChunk,
} from '../../../shared/api/mediaVaultApi';
import { saveLocalAssetMapping } from '../../../shared/services/localAssetMappingStore';
import {
  readResumableUploadRecord,
  removeResumableUploadRecord,
  saveResumableUploadRecord,
  updateResumableUploadProgress,
  updateResumableUploadSessionId,
} from '../../../shared/services/resumableUploadStore';
import { createAppError, messageForErrorCode, toDisplayError } from '../../../shared/utils/errors';
import { resolveResumableVideoAsset } from '../services/resumableVideoMediaService';
import { hashFileRange, hashWholeFile } from '../services/streamingSha256Service';

const MAX_FINALIZATION_POLLS = 5;
const FINALIZATION_POLL_INTERVAL_MS = 1000;

/**
 * Owns session-backed video transfer state. It intentionally retains resume
 * state across timeouts and only removes it for completed/cancelled/expired uploads.
 */
export function useResumableVideoUpload({ settings, pickedAsset, isLog, canUseApi, onMappingUnavailable, onUploaded }) {
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState({ uploadedBytes: 0, totalBytes: 0 });
  const [session, setSession] = useState(null);
  const inFlightRef = useRef(false);

  const startUpload = useCallback(async () => {
    if (inFlightRef.current || !pickedAsset || pickedAsset.type !== 'video') {
      return;
    }
    if (!canUseApi) {
      setStatus('terminal_failed');
      setError(toDisplayError(createAppError('missing_settings', messageForErrorCode('missing_settings'))));
      return;
    }

    inFlightRef.current = true;
    setError(null);
    let activeClientUploadId = null;
    try {
      setStatus('resolving_local_media');
      const resolvedAsset = await resolveResumableVideoAsset(pickedAsset);
      if (!Number.isSafeInteger(resolvedAsset.sizeBytes) || resolvedAsset.sizeBytes <= 0) {
        throw createAppError('media_unavailable', messageForErrorCode('media_unavailable'));
      }

      setStatus('hashing');
      const expectedFileSha256 = await hashWholeFile(resolvedAsset.uri);
      let record = await readResumableUploadRecord();
      const canRecover = record
        && record.localAssetId === resolvedAsset.localAssetId
        && record.sizeBytes === resolvedAsset.sizeBytes
        && record.expectedFileSha256 === expectedFileSha256;

      if (!canRecover) {
        if (record?.localAssetId === resolvedAsset.localAssetId) {
          await removeResumableUploadRecord(record.clientUploadId);
          throw createAppError(
            'resumable_upload_source_changed',
            messageForErrorCode('resumable_upload_source_changed'),
          );
        }
        record = await saveResumableUploadRecord({
          localAssetId: resolvedAsset.localAssetId,
          clientUploadId: createClientUploadId(),
          sessionId: null,
          sizeBytes: resolvedAsset.sizeBytes,
          expectedFileSha256,
          uploadedBytes: 0,
        });
      }
      activeClientUploadId = record.clientUploadId;

      let currentSession;
      if (record.sessionId) {
        currentSession = await getUploadSession({ settings, sessionId: record.sessionId });
      } else {
        setStatus('creating_session');
        currentSession = await createUploadSession({
          settings,
          session: {
            client_upload_id: record.clientUploadId,
            filename: resolvedAsset.filename,
            size_bytes: resolvedAsset.sizeBytes,
            expected_file_sha256: expectedFileSha256,
            taken_at: resolvedAsset.takenAt,
            latitude: resolvedAsset.latitude,
            longitude: resolvedAsset.longitude,
            exif_json: resolvedAsset.exif,
            is_log: Boolean(isLog),
          },
        });
        record = await updateResumableUploadSessionId(record.clientUploadId, currentSession.id);
        currentSession = await getUploadSession({ settings, sessionId: currentSession.id });
      }

      setSession(currentSession);
      await continueSession({
        settings,
        resolvedAsset,
        record,
        currentSession,
        setProgress,
        setSession,
        setStatus,
        onCompleted: async (assetId) => {
          await completeUpload({
            assetId,
            localAssetId: resolvedAsset.localAssetId,
            onMappingUnavailable,
            onUploaded,
          });
          await removeResumableUploadRecord(record.clientUploadId);
        },
      });
    } catch (uploadError) {
      await handleUploadError(uploadError, setStatus, setError, activeClientUploadId);
    } finally {
      inFlightRef.current = false;
    }
  }, [canUseApi, isLog, onMappingUnavailable, onUploaded, pickedAsset, settings]);

  const cancelUpload = useCallback(async () => {
    if (!session?.id || inFlightRef.current) {
      return;
    }
    inFlightRef.current = true;
    try {
      await cancelUploadSession({ settings, sessionId: session.id });
      await removeResumableUploadRecord();
      setSession((currentSession) => (currentSession ? { ...currentSession, status: 'cancelled' } : null));
      setStatus('cancelled');
      setError(null);
    } catch (cancelError) {
      await handleUploadError(cancelError, setStatus, setError);
    } finally {
      inFlightRef.current = false;
    }
  }, [session, settings]);

  return {
    status,
    error,
    progress,
    session,
    canStart: !inFlightRef.current && Boolean(pickedAsset) && pickedAsset.type === 'video' && canUseApi,
    canCancel: !inFlightRef.current && Boolean(session?.id) && !['completed', 'cancelled', 'expired'].includes(status),
    startUpload,
    cancelUpload,
  };
}

async function continueSession({
  settings,
  resolvedAsset,
  record,
  currentSession,
  setProgress,
  setSession,
  setStatus,
  onCompleted,
}) {
  if (currentSession.status === 'completed' && currentSession.asset_id) {
    await onCompleted(currentSession.asset_id);
    setStatus('completed');
    return;
  }
  if (currentSession.status === 'cancelled' || currentSession.status === 'expired') {
    await removeResumableUploadRecord(record.clientUploadId);
    throw createAppError(`session_${currentSession.status}`, messageForErrorCode(`session_${currentSession.status}`));
  }
  if (currentSession.status === 'failed' && !currentSession.retryable) {
    throw createAppError('session_terminal_failure', messageForErrorCode('unknown'));
  }

  const missingIndexes = currentSession.missing_chunk_indexes ?? [];
  let uploadedBytes = calculateUploadedBytes(currentSession, missingIndexes);
  setProgress({ uploadedBytes, totalBytes: currentSession.size_bytes });

  if (missingIndexes.length) {
    setStatus('uploading_chunks');
    for (const chunkIndex of missingIndexes) {
      const offset = chunkIndex * currentSession.chunk_size_bytes;
      const length = Math.min(currentSession.chunk_size_bytes, currentSession.size_bytes - offset);
      const chunkSha256 = await hashFileRange(resolvedAsset.uri, offset, length);
      await uploadUploadSessionChunk({
        settings,
        sessionId: currentSession.id,
        uri: resolvedAsset.uri,
        chunkIndex,
        offset,
        length,
        totalSize: currentSession.size_bytes,
        sha256: chunkSha256,
      });
      uploadedBytes += length;
      await updateResumableUploadProgress(record.clientUploadId, uploadedBytes);
      setProgress({ uploadedBytes, totalBytes: currentSession.size_bytes });
    }
  }

  setStatus('finalizing');
  const finalizing = await finalizeUploadSession({ settings, sessionId: currentSession.id });
  setSession(finalizing.session);
  await pollFinalization({ settings, sessionId: currentSession.id, setSession, setStatus, onCompleted });
}

async function pollFinalization({ settings, sessionId, setSession, setStatus, onCompleted }) {
  for (let attempt = 0; attempt < MAX_FINALIZATION_POLLS; attempt += 1) {
    const current = await getUploadSession({ settings, sessionId });
    setSession(current);
    if (current.status === 'completed' && current.asset_id) {
      await onCompleted(current.asset_id);
      setStatus('completed');
      return;
    }
    if (current.status === 'failed') {
      if (current.retryable) {
        setStatus('retryable_failed');
        return;
      }
      throw createAppError('session_terminal_failure', messageForErrorCode('unknown'));
    }
    if (current.status === 'cancelled' || current.status === 'expired') {
      throw createAppError(`session_${current.status}`, messageForErrorCode(`session_${current.status}`));
    }
    await delay(FINALIZATION_POLL_INTERVAL_MS);
  }
  setStatus('finalizing');
}

async function completeUpload({ assetId, localAssetId, onMappingUnavailable, onUploaded }) {
  onUploaded?.(assetId);
  try {
    await saveLocalAssetMapping({ backendAssetId: assetId, localAssetId });
  } catch {
    onMappingUnavailable?.(assetId);
  }
}

async function handleUploadError(error, setStatus, setError, clientUploadId = null) {
  const code = error?.code;
  if (code === 'session_cancelled' || code === 'session_expired') {
    await removeResumableUploadRecord();
    setStatus(code === 'session_cancelled' ? 'cancelled' : 'expired');
  } else if (code === 'resumable_video_requires_library_asset' || code === 'media_unavailable') {
    setStatus(code === 'media_unavailable' ? 'media_unavailable' : 'terminal_failed');
  } else if (error?.retryable || code === 'timeout' || code === 'network_unreachable') {
    setStatus('retryable_failed');
  } else {
    if (clientUploadId && (
      code === 'session_terminal_failure'
      || code === 'session_metadata_conflict'
      || code === 'resumable_upload_source_changed'
    )) {
      await removeResumableUploadRecord(clientUploadId);
    }
    setStatus('terminal_failed');
  }
  setError(toDisplayError(error));
}

function calculateUploadedBytes(session, missingIndexes) {
  const missingBytes = missingIndexes.reduce((sum, chunkIndex) => {
    const offset = chunkIndex * session.chunk_size_bytes;
    return sum + Math.min(session.chunk_size_bytes, session.size_bytes - offset);
  }, 0);
  return session.size_bytes - missingBytes;
}

function createClientUploadId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `upload-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
