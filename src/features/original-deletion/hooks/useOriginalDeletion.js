import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert } from 'react-native';

import { getLocalAssetMappingState } from '../../../shared/services/localAssetMappingStore';
import { toDisplayError } from '../../../shared/utils/errors';
import { deleteOriginalAsset } from '../services/originalDeletionMediaLibraryService';
import { isOriginalDeletionEligible } from '../services/originalDeletionEligibility';
import {
  readOriginalDeletionOutcome,
  writeOriginalDeletionOutcome,
} from '../services/originalDeletionStore';

export function useOriginalDeletion({ asset, capabilities }) {
  const [mappingState, setMappingState] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);
  const assetId = asset?.id ?? null;

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

  const performDeletion = useCallback(async () => {
    if (!eligible || !assetId) {
      return;
    }
    setStatus('deleting');
    setError(null);
    try {
      await deleteOriginalAsset({
        localAssetId: mappingState.mapping.localAssetId,
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

  const requestDeletion = useCallback(() => {
    if (!eligible) {
      return;
    }
    Alert.alert(
      'Delete iPhone original?',
      `${asset.filename}\nOnly the iPhone original will be deleted. Backend originals and processed videos are kept.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: performDeletion,
        },
      ],
    );
  }, [asset?.filename, eligible, performDeletion]);

  return {
    canDelete: eligible,
    status: outcome?.status === 'deleted' ? 'deleted' : status,
    error,
    requestDeletion,
  };
}
