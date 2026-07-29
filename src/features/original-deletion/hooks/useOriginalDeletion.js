import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert } from 'react-native';

import { getLocalAssetMappingState } from '../../../shared/services/localAssetMappingStore';
import { toDisplayError } from '../../../shared/utils/errors';
import { deleteOriginalAsset } from '../services/originalDeletionMediaLibraryService';
import { isOriginalDeletionEligible } from '../services/originalDeletionEligibility';
import {
  readOriginalDeletionOutcome,
  writeOriginalDeletionOutcome,
} from '../services/originalDeletionStore';

export function useOriginalDeletion({
  asset,
  capabilities,
  refreshAsset,
  refreshCapabilities,
}) {
  const [mappingState, setMappingState] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);
  const assetId = asset?.id ?? null;
  const currentAssetIdRef = useRef(assetId);
  const currentMappingStateRef = useRef(mappingState);
  currentAssetIdRef.current = assetId;
  currentMappingStateRef.current = mappingState;

  useEffect(() => {
    let active = true;
    if (!assetId) {
      setMappingState(null);
      setOutcome(null);
      setStatus('idle');
      return () => {
        active = false;
      };
    }
    setStatus('loading');
    Promise.all([
      getLocalAssetMappingState(assetId),
      readOriginalDeletionOutcome(assetId),
    ]).then(([nextMapping, nextOutcome]) => {
      if (!active) {
        return;
      }
      setMappingState(nextMapping);
      setOutcome(nextOutcome);
      setStatus(nextOutcome?.status === 'deleted' ? 'deleted' : 'idle');
      setError(null);
    }).catch((loadError) => {
      if (!active) {
        return;
      }
      setMappingState(null);
      setOutcome(null);
      setStatus('failed');
      setError(toDisplayError(loadError));
    });
    return () => {
      active = false;
    };
  }, [assetId]);

  const eligible = useMemo(() => isOriginalDeletionEligible({
    asset,
    capabilities,
    mappingState,
    outcome,
    status,
  }), [
    asset,
    capabilities,
    mappingState,
    outcome,
    status,
  ]);

  const performDeletion = useCallback(async ({
    expectedAssetId = assetId,
    expectedLocalAssetId = mappingState?.mapping?.localAssetId,
  } = {}) => {
    if (
      !eligible
      || !assetId
      || expectedAssetId !== currentAssetIdRef.current
      || expectedLocalAssetId !== currentMappingStateRef.current?.mapping?.localAssetId
      || Number(currentMappingStateRef.current?.mapping?.backendAssetId) !== Number(expectedAssetId)
    ) {
      return;
    }
    setStatus('deleting');
    setError(null);
    try {
      await deleteOriginalAsset({
        localAssetId: expectedLocalAssetId,
      });
    } catch (deleteError) {
      const displayError = toDisplayError(deleteError);
      setError(displayError);
      setStatus('failed');
      try {
        const record = await writeOriginalDeletionOutcome({
          backendAssetId: assetId,
          status: 'failed',
          errorCode: displayError.code,
        });
        setOutcome(record);
      } catch (stateError) {
        setError(toDisplayError(stateError));
      }
      return;
    }

    setOutcome({
      backendAssetId: assetId,
      status: 'deleted',
      errorCode: null,
      updatedAt: null,
    });
    setStatus('deleted');
    try {
      const record = await writeOriginalDeletionOutcome({
        backendAssetId: assetId,
        status: 'deleted',
      });
      setOutcome(record);
    } catch (stateError) {
      setError(toDisplayError(stateError));
    }
  }, [assetId, eligible, mappingState]);

  const requestDeletion = useCallback(async () => {
    if (!eligible) {
      return;
    }

    const [assetRefresh, capabilityRefresh] = await Promise.allSettled([
      Promise.resolve().then(refreshAsset),
      Promise.resolve().then(refreshCapabilities),
    ]);
    const latestAsset = assetRefresh.status === 'fulfilled'
      ? assetRefresh.value
      : null;
    const latestCapabilities = capabilityRefresh.status === 'fulfilled'
      ? capabilityRefresh.value
      : null;

    const sameAsset = latestAsset?.id === assetId;
    const sameMapping = (
      mappingState?.status === 'available'
      && Number(mappingState.mapping?.backendAssetId) === Number(assetId)
      && Boolean(mappingState.mapping?.localAssetId)
    );
    const stillEligible = (
      sameAsset
      && sameMapping
      && isOriginalDeletionEligible({
        asset: latestAsset,
        capabilities: latestCapabilities,
        mappingState,
        outcome,
        status,
      })
    );
    if (!stillEligible) {
      Alert.alert(
        'Deletion no longer available',
        'The latest asset state is not ready for iPhone deletion. Refresh and try again.',
      );
      return;
    }

    Alert.alert(
      'Delete iPhone original?',
      `${latestAsset.filename}\nOnly the iPhone original will be deleted. Backend originals and processed videos are kept.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => performDeletion({
            expectedAssetId: latestAsset.id,
            expectedLocalAssetId: mappingState.mapping.localAssetId,
          }),
        },
      ],
    );
  }, [
    assetId,
    eligible,
    mappingState,
    outcome,
    performDeletion,
    refreshAsset,
    refreshCapabilities,
    status,
  ]);

  return {
    canDelete: eligible,
    status: outcome?.status === 'deleted' ? 'deleted' : status,
    error,
    requestDeletion,
  };
}
