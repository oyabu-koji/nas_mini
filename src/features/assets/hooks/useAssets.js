import { useCallback, useEffect, useRef, useState } from 'react';

import { getAsset, listAssets } from '../../../shared/api/mediaVaultApi';
import { PREVIEW_STATUS } from '../../../shared/constants/assetStatuses';
import { toDisplayError } from '../../../shared/utils/errors';

export function useAssetList(settings, canUseApi, { autoLoad = true } = {}) {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const requestIdRef = useRef(0);

  const refreshAssets = useCallback(async () => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    if (!canUseApi) {
      setStatus('idle');
      setItems([]);
      return { success: false, isLatest: requestId === requestIdRef.current };
    }
    setStatus('loading');
    setError(null);
    try {
      const response = await listAssets(settings);
      if (requestId !== requestIdRef.current) {
        return { success: true, isLatest: false };
      }
      setItems(response.items);
      setStatus('ready');
      return { success: true, isLatest: true };
    } catch (assetError) {
      if (requestId !== requestIdRef.current) {
        return { success: false, isLatest: false };
      }
      setStatus('error');
      setError(toDisplayError(assetError));
      return { success: false, isLatest: true };
    }
  }, [canUseApi, settings]);

  useEffect(() => {
    if (autoLoad) {
      refreshAssets();
    }
  }, [autoLoad, refreshAssets]);

  return {
    items,
    status,
    error,
    refreshAssets,
  };
}

export function useAssetDetail(settings, canUseApi, assetId, { autoPoll = true } = {}) {
  const [asset, setAsset] = useState(null);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const intervalRef = useRef(null);

  const loadAsset = useCallback(async () => {
    if (!canUseApi || !assetId) {
      setStatus('idle');
      return null;
    }
    setStatus((currentStatus) => (currentStatus === 'ready' ? 'refreshing' : 'loading'));
    setError(null);
    try {
      const nextAsset = await getAsset(settings, assetId);
      setAsset(nextAsset);
      setStatus('ready');
      return nextAsset;
    } catch (assetError) {
      setStatus('error');
      setError(toDisplayError(assetError));
      return null;
    }
  }, [assetId, canUseApi, settings]);

  useEffect(() => {
    loadAsset();
  }, [loadAsset]);

  useEffect(() => {
    if (!autoPoll || asset?.preview_status !== PREVIEW_STATUS.GENERATING) {
      return undefined;
    }

    intervalRef.current = setInterval(() => {
      loadAsset();
    }, 2000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [asset?.preview_status, autoPoll, loadAsset]);

  return {
    asset,
    status,
    error,
    loadAsset,
  };
}
