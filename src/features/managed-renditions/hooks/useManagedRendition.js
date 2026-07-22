import { useCallback, useEffect, useRef, useState } from 'react';

import { createAppError, messageForErrorCode, toDisplayError } from '../../../shared/utils/errors';
import {
  createManagedRendition,
  getManagedCapabilities,
  getManagedRendition,
  listManagedPresets,
} from '../services/managedRenditionApi';
import {
  generateClientRenditionRequestId,
  readManagedRenditionRecord,
  updateManagedRenditionRecord,
  writePendingManagedRendition,
} from '../services/managedRenditionStore';

const TERMINAL_STATES = new Set(['ready', 'failed', 'superseded']);

export function isManagedRenditionEligible(asset) {
  return Boolean(
    asset
    && asset.type === 'video'
    && asset.verification_status === 'file_verified'
    && asset.preview_status === 'preview_ready'
    && asset.is_log === false
    && asset.active_processed_result,
  );
}

export function useManagedRendition({
  settings,
  canUseApi,
  asset,
  loadAsset,
  pollIntervalMs = 2000,
}) {
  const [catalogStatus, setCatalogStatus] = useState('idle');
  const [presets, setPresets] = useState([]);
  const [selectedPresetId, setSelectedPresetId] = useState(null);
  const [submitStatus, setSubmitStatus] = useState('idle');
  const [rendition, setRendition] = useState(null);
  const [error, setError] = useState(null);
  const [readyResultConfirmed, setReadyResultConfirmed] = useState(false);
  const mountedRef = useRef(true);
  const operationRef = useRef(0);
  const catalogOperationRef = useRef(0);
  const recordRef = useRef(null);
  const presetsRef = useRef([]);

  const eligible = canUseApi && isManagedRenditionEligible(asset);
  const assetId = asset?.id ?? null;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      operationRef.current += 1;
      catalogOperationRef.current += 1;
    };
  }, []);

  const loadCatalog = useCallback(async () => {
    const operation = catalogOperationRef.current + 1;
    catalogOperationRef.current = operation;
    if (!eligible) {
      setCatalogStatus('idle');
      setPresets([]);
      presetsRef.current = [];
      setSelectedPresetId(null);
      return;
    }
    setCatalogStatus('loading');
    setError(null);
    try {
      await getManagedCapabilities(settings);
      const nextPresets = await listManagedPresets(settings);
      if (!mountedRef.current || operation !== catalogOperationRef.current) {
        return;
      }
      presetsRef.current = nextPresets;
      setPresets(nextPresets);
      setSelectedPresetId((current) => {
        const candidate = current ?? recordRef.current?.requestedPresetId ?? null;
        return candidate && nextPresets.some((preset) => preset.presetId === candidate)
          ? candidate
          : null;
      });
      setCatalogStatus('ready');
    } catch (loadError) {
      if (!mountedRef.current || operation !== catalogOperationRef.current) {
        return;
      }
      const displayError = toDisplayError(loadError);
      setCatalogStatus(displayError.code === 'incompatible_client' ? 'incompatible' : 'error');
      setPresets([]);
      presetsRef.current = [];
      setSelectedPresetId(null);
      setError(displayError);
    }
  }, [eligible, settings]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    let active = true;
    const restoreOperation = operationRef.current + 1;
    operationRef.current = restoreOperation;
    recordRef.current = null;
    setSelectedPresetId(null);
    setRendition(null);
    setSubmitStatus('idle');
    setReadyResultConfirmed(false);
    if (!eligible || !assetId) {
      return () => {
        active = false;
      };
    }
    readManagedRenditionRecord(assetId).then((record) => {
      if (
        !active || !mountedRef.current || restoreOperation !== operationRef.current || !record
      ) {
        return;
      }
      recordRef.current = record;
      setSelectedPresetId(
        presetsRef.current.some((preset) => preset.presetId === record.requestedPresetId)
          ? record.requestedPresetId
          : null,
      );
      setRendition(record.rendition);
      if (record.rendition?.state === 'failed') {
        setSubmitStatus('terminal_failed');
      } else if (!record.renditionId) {
        setSubmitStatus('retryable_failed');
      }
    }).catch(() => {
      if (active && mountedRef.current && restoreOperation === operationRef.current) {
        setError(toDisplayError(createAppError(
          'managed_rendition_state_unavailable',
          messageForErrorCode('managed_rendition_state_unavailable'),
        )));
      }
    });
    return () => {
      active = false;
    };
  }, [assetId, eligible]);

  const selectPreset = useCallback((presetId) => {
    if (!presetsRef.current.some((preset) => preset.presetId === presetId)) {
      return;
    }
    operationRef.current += 1;
    setSelectedPresetId(presetId);
    setSubmitStatus('idle');
    setError(null);
    setReadyResultConfirmed(false);
  }, []);

  const performSubmit = useCallback(async ({ reusePending }) => {
    const pending = recordRef.current;
    const requestedPresetId = reusePending && pending
      ? pending.requestedPresetId
      : selectedPresetId;
    if (!eligible || !assetId || !requestedPresetId) {
      return false;
    }
    const canReuse = reusePending
      && pending
      && pending.requestedPresetId === requestedPresetId
      && pending.clientRequestId;
    let clientRequestId;
    let selectionSequence;
    try {
      clientRequestId = canReuse
        ? pending.clientRequestId
        : generateClientRenditionRequestId();
      selectionSequence = canReuse
        ? pending.selectionSequence
        : (pending?.selectionSequence ?? 0) + 1;
    } catch (_requestIdError) {
      setSubmitStatus('terminal_failed');
      setError({
        code: 'managed_request_id_unavailable',
        message: 'A secure rendition request ID could not be created.',
        retryable: false,
      });
      return false;
    }

    const operation = operationRef.current + 1;
    operationRef.current = operation;
    setSubmitStatus('submitting');
    setError(null);
    setReadyResultConfirmed(false);
    try {
      let record = pending;
      if (!canReuse) {
        record = await writePendingManagedRendition({
          assetId,
          clientRequestId,
          requestedPresetId,
          selectionSequence,
        });
        if (!mountedRef.current || operation !== operationRef.current) {
          return false;
        }
        recordRef.current = record;
      }
      const response = await createManagedRendition({
        settings,
        assetId,
        clientRequestId,
        presetId: requestedPresetId,
      });
      if (!mountedRef.current || operation !== operationRef.current) {
        return false;
      }
      const updated = await updateManagedRenditionRecord({
        assetId,
        clientRequestId,
        selectionSequence,
        rendition: response,
      });
      if (!mountedRef.current || operation !== operationRef.current) {
        return false;
      }
      recordRef.current = updated;
      setRendition(response);
      setSubmitStatus(response.state === 'failed' ? 'terminal_failed' : 'idle');
      if (response.state === 'ready') {
        const refreshed = await loadAsset();
        if (mountedRef.current && operation === operationRef.current) {
          setReadyResultConfirmed(
            refreshed?.active_processed_result?.result_id === response.resultId,
          );
        }
      }
      return true;
    } catch (submitError) {
      if (!mountedRef.current || operation !== operationRef.current) {
        return false;
      }
      const displayError = toDisplayError(submitError);
      setSubmitStatus(displayError.retryable ? 'retryable_failed' : 'terminal_failed');
      setError(displayError);
      return false;
    }
  }, [assetId, eligible, loadAsset, selectedPresetId, settings]);

  const submit = useCallback(() => performSubmit({ reusePending: false }), [performSubmit]);
  const retry = useCallback(() => performSubmit({ reusePending: true }), [performSubmit]);

  useEffect(() => {
    if (
      !eligible || !rendition?.renditionId || TERMINAL_STATES.has(rendition.state)
      || !recordRef.current
    ) {
      return undefined;
    }
    let active = true;
    const expectedOperation = operationRef.current;
    const expectedClientId = recordRef.current.clientRequestId;
    const expectedSequence = recordRef.current.selectionSequence;
    const poll = async () => {
      try {
        const response = await getManagedRendition({
          settings,
          assetId,
          renditionId: rendition.renditionId,
        });
        if (
          !active || !mountedRef.current || operationRef.current !== expectedOperation
          || recordRef.current?.clientRequestId !== expectedClientId
          || recordRef.current?.selectionSequence !== expectedSequence
        ) {
          return;
        }
        const updated = await updateManagedRenditionRecord({
          assetId,
          clientRequestId: expectedClientId,
          selectionSequence: expectedSequence,
          rendition: response,
        });
        if (!active || !mountedRef.current || operationRef.current !== expectedOperation) {
          return;
        }
        recordRef.current = updated;
        setRendition(response);
        if (response.state === 'failed') {
          setSubmitStatus('terminal_failed');
          setError({
            code: response.errorCode,
            message: messageForErrorCode(response.errorCode),
            retryable: false,
          });
        } else if (response.state === 'ready') {
          const refreshed = await loadAsset();
          if (
            active && mountedRef.current && operationRef.current === expectedOperation
          ) {
            setReadyResultConfirmed(
              refreshed?.active_processed_result?.result_id === response.resultId,
            );
          }
        }
      } catch (pollError) {
        if (active && mountedRef.current && operationRef.current === expectedOperation) {
          const displayError = toDisplayError(pollError);
          setError(displayError);
          if (!displayError.retryable) {
            setSubmitStatus('terminal_failed');
          }
        }
      }
    };
    const timer = setInterval(poll, pollIntervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [
    assetId,
    eligible,
    loadAsset,
    pollIntervalMs,
    rendition?.renditionId,
    rendition?.state,
    settings,
  ]);

  return {
    eligible,
    catalogStatus,
    presets,
    selectedPresetId,
    submitStatus,
    rendition,
    error,
    readyResultConfirmed,
    selectPreset,
    submit,
    retry,
    reloadCatalog: loadCatalog,
  };
}
