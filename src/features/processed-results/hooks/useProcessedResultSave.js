import { useCallback, useEffect, useRef, useState } from 'react';

import { createAppError, messageForErrorCode, toDisplayError } from '../../../shared/utils/errors';
import {
  cleanupProcessedResultTempFile,
  cleanupProcessedResultTempFiles,
  downloadProcessedResult,
} from '../services/processedResultDownloadService';
import {
  createProcessedResultLibraryAsset,
  requestProcessedResultLibraryPermission,
} from '../services/processedResultMediaLibraryService';
import {
  getProcessedResultSave,
  listProcessedResultSaves,
  markProcessedResultFailed,
  markProcessedResultSaved,
  writeProcessedResultDownload,
  writeUnknownProcessedResultSave,
} from '../services/processedResultSaveStore';

export function useProcessedResultStartupCleanup() {
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const records = await listProcessedResultSaves();
        if (active) {
          await cleanupProcessedResultTempFiles({ records });
        }
      } catch {
        // Startup cleanup is best effort and does not alter a persisted save state.
      }
    })();
    return () => {
      active = false;
    };
  }, []);
}

export function useProcessedResultSave({ settings, assetId, result, onSuperseded }) {
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const [savedLocalAssetIdentifier, setSavedLocalAssetIdentifier] = useState(null);
  const [inFlightIdentityKey, setInFlightIdentityKey] = useState(null);
  const abortControllerRef = useRef(null);

  const identity = result
    ? {
        backendAssetId: assetId,
        backendResultId: result.result_id,
        resultSha256: result.sha256,
      }
    : null;
  const identityKey = identity
    ? `${identity.backendAssetId}:${identity.backendResultId}:${identity.resultSha256}`
    : null;
  const currentIdentityKeyRef = useRef(identityKey);
  const inFlightIdentityKeyRef = useRef(null);
  currentIdentityKeyRef.current = identityKey;

  useEffect(() => {
    let active = true;
    setStatus('idle');
    setError(null);
    setSavedLocalAssetIdentifier(null);
    if (!identity) {
      return () => {
        active = false;
      };
    }
    void (async () => {
      try {
        const record = await getProcessedResultSave(identity);
        if (
          !active
          || inFlightIdentityKeyRef.current === identityKey
          || !record
        ) {
          return;
        }
        if (record.saveStatus === 'saved') {
          setStatus('saved');
          setSavedLocalAssetIdentifier(record.savedLocalAssetIdentifier ?? null);
          setError(null);
        } else if (record.saveStatus === 'unknown') {
          setStatus('unknown');
          setError(
            toDisplayError(
              createAppError(
                'processed_result_save_outcome_unknown',
                messageForErrorCode('processed_result_save_outcome_unknown'),
              ),
            ),
          );
        } else if (record.saveStatus === 'failed') {
          setStatus('failed');
          setError(
            toDisplayError(
              createAppError(
                record.lastErrorCode || 'processed_result_download_failed',
                messageForErrorCode(record.lastErrorCode || 'processed_result_download_failed'),
              ),
            ),
          );
        }
      } catch {
        // A local status read must never block a new explicit save action.
      }
    })();
    return () => {
      active = false;
    };
  }, [identityKey]);

  const save = useCallback(async () => {
    const operationIdentityKey = identityKey;
    if (!identity || !result || !operationIdentityKey || inFlightIdentityKeyRef.current) {
      return;
    }
    inFlightIdentityKeyRef.current = operationIdentityKey;
    setInFlightIdentityKey(operationIdentityKey);
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    let tempUri = null;
    let writeAheadPersisted = false;
    const isCurrentIdentity = () => currentIdentityKeyRef.current === operationIdentityKey;
    setError(null);
    setSavedLocalAssetIdentifier(null);
    setStatus('downloading');

    try {
      await writeProcessedResultDownload(identity);
      const downloaded = await downloadProcessedResult({
        settings,
        assetId,
        result,
        signal: abortController.signal,
        onStage: (nextStage) => {
          if (nextStage === 'verifying' && isCurrentIdentity()) {
            setStatus('verifying');
          }
        },
      });
      tempUri = downloaded.tempUri;
      if (isCurrentIdentity()) {
        setStatus('requesting_library_permission');
      }
      await requestProcessedResultLibraryPermission();

      try {
        await writeUnknownProcessedResultSave(identity);
      } catch (writeAheadError) {
        await cleanupProcessedResultTempFile({ uri: tempUri });
        tempUri = null;
        throw writeAheadError;
      }
      writeAheadPersisted = true;
      if (isCurrentIdentity()) {
        setStatus('saving_to_library');
      }
      const libraryAsset = await createProcessedResultLibraryAsset({ uri: tempUri });

      try {
        await markProcessedResultSaved({
          ...identity,
          savedLocalAssetIdentifier: libraryAsset.localAssetIdentifier,
        });
      } catch {
        await cleanupProcessedResultTempFile({ uri: tempUri });
        tempUri = null;
        if (isCurrentIdentity()) {
          setStatus('unknown');
          setError(
            toDisplayError(
              createAppError(
                'processed_result_save_outcome_unknown',
                messageForErrorCode('processed_result_save_outcome_unknown'),
              ),
            ),
          );
        }
        return;
      }

      await cleanupProcessedResultTempFile({ uri: tempUri });
      tempUri = null;
      if (isCurrentIdentity()) {
        setStatus('saved');
        setSavedLocalAssetIdentifier(libraryAsset.localAssetIdentifier);
        setError(null);
      }
    } catch (saveError) {
      if (tempUri) {
        await cleanupProcessedResultTempFile({ uri: tempUri });
      }
      if (saveError?.code === 'processed_result_superseded') {
        if (isCurrentIdentity()) {
          setStatus('superseded');
          setError(toDisplayError(saveError));
          onSuperseded?.();
        }
        return;
      }

      const errorCode = safeErrorCode(saveError?.code);
      try {
        await markProcessedResultFailed({ ...identity, lastErrorCode: errorCode });
        if (isCurrentIdentity()) {
          setStatus('failed');
          setError(toDisplayError(saveError));
        }
      } catch {
        if (isCurrentIdentity()) {
          if (writeAheadPersisted) {
            setStatus('unknown');
            setError(
              toDisplayError(
                createAppError(
                  'processed_result_save_outcome_unknown',
                  messageForErrorCode('processed_result_save_outcome_unknown'),
                ),
              ),
            );
          } else {
            setStatus('failed');
            setError(toDisplayError(saveError));
          }
        }
      }
    } finally {
      if (inFlightIdentityKeyRef.current === operationIdentityKey) {
        inFlightIdentityKeyRef.current = null;
        setInFlightIdentityKey((currentKey) => (
          currentKey === operationIdentityKey ? null : currentKey
        ));
      }
      if (abortControllerRef.current === abortController) {
        abortControllerRef.current = null;
      }
    }
  }, [assetId, identityKey, onSuperseded, result, settings]);

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  return {
    canSave: Boolean(identity) && inFlightIdentityKey == null,
    status,
    error,
    savedLocalAssetIdentifier,
    save,
    retry: save,
    cancel,
    canCancel: inFlightIdentityKey === identityKey && status === 'downloading',
  };
}

function safeErrorCode(value) {
  return /^[a-z0-9_]{1,100}$/.test(String(value ?? ''))
    ? String(value)
    : 'processed_result_download_failed';
}
