import { useCallback, useMemo, useState } from 'react';

import { buildPreviewSource, buildPreviewVideoSource, confirmPreview } from '../../../shared/api/mediaVaultApi';
import { ASSET_TYPE, PREVIEW_STATUS } from '../../../shared/constants/assetStatuses';
import { toDisplayError } from '../../../shared/utils/errors';
import { useAssetDetail } from '../../assets/hooks/useAssets';
import { downloadPreviewToCache } from '../services/previewCacheService';

export function usePreviewReview(settings, canUseApi, assetId) {
  const { asset, status: assetStatus, error: assetError, loadAsset } = useAssetDetail(settings, canUseApi, assetId, {
    autoPoll: false,
  });
  const [confirmStatus, setConfirmStatus] = useState('idle');
  const [confirmError, setConfirmError] = useState(null);
  const [cachedPreviewUri, setCachedPreviewUri] = useState(null);
  const [cacheStatus, setCacheStatus] = useState('idle');
  const [cacheError, setCacheError] = useState(null);

  const canReview = asset?.preview_status === PREVIEW_STATUS.READY;

  const videoSource = useMemo(() => {
    if (!canReview || asset?.type !== ASSET_TYPE.VIDEO) {
      return null;
    }
    if (cachedPreviewUri) {
      return { uri: cachedPreviewUri };
    }
    return buildPreviewVideoSource({
      baseUrl: settings.backendUrl,
      apiToken: settings.apiToken,
      assetId,
    });
  }, [asset?.type, assetId, cachedPreviewUri, canReview, settings.apiToken, settings.backendUrl]);

  const imageSource = useMemo(() => {
    if (!canReview || asset?.type !== ASSET_TYPE.IMAGE) {
      return null;
    }
    if (cachedPreviewUri) {
      return { uri: cachedPreviewUri };
    }
    return buildPreviewSource({
      baseUrl: settings.backendUrl,
      apiToken: settings.apiToken,
      assetId,
    });
  }, [asset?.type, assetId, cachedPreviewUri, canReview, settings.apiToken, settings.backendUrl]);

  const confirm = useCallback(async () => {
    setConfirmStatus('saving');
    setConfirmError(null);
    try {
      await confirmPreview(settings, assetId);
      await loadAsset();
      setConfirmStatus('confirmed');
    } catch (error) {
      setConfirmStatus('error');
      setConfirmError(toDisplayError(error));
    }
  }, [assetId, loadAsset, settings]);

  const cachePreview = useCallback(async () => {
    setCacheStatus('loading');
    setCacheError(null);
    try {
      const extension = asset?.type === ASSET_TYPE.IMAGE ? 'jpg' : 'mp4';
      const uri = await downloadPreviewToCache({ settings, assetId, extension });
      setCachedPreviewUri(uri);
      setCacheStatus('ready');
    } catch (error) {
      setCacheStatus('error');
      setCacheError(toDisplayError(error));
    }
  }, [asset?.type, assetId, settings]);

  return {
    asset,
    assetStatus,
    assetError,
    canReview,
    videoSource,
    imageSource,
    confirmStatus,
    confirmError,
    cacheStatus,
    cacheError,
    cachedPreviewUri,
    confirm,
    loadAsset,
    cachePreview,
  };
}
