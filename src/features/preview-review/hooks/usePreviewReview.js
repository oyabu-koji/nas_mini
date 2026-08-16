import { useCallback, useMemo, useState } from 'react';

import { buildPreviewSource, buildPreviewVideoSource, confirmPreview } from '../../../shared/api/mediaVaultApi';
import { ASSET_TYPE, PREVIEW_STATUS } from '../../../shared/constants/assetStatuses';
import { messageForErrorCode, toDisplayError } from '../../../shared/utils/errors';
import { formalPreviewProfileLabel } from '../../../shared/utils/formalPreviewPresentation';
import { useAssetDetail } from '../../assets/hooks/useAssets';
import { downloadPreviewToCache } from '../services/previewCacheService';

export function usePreviewReview(settings, canUseApi, assetId) {
  const { asset, status: assetStatus, error: assetError, loadAsset } = useAssetDetail(settings, canUseApi, assetId, {
    autoPoll: true,
  });
  const [confirmStatus, setConfirmStatus] = useState('idle');
  const [confirmError, setConfirmError] = useState(null);
  const [cachedPreviewUri, setCachedPreviewUri] = useState(null);
  const [cacheStatus, setCacheStatus] = useState('idle');
  const [cacheError, setCacheError] = useState(null);

  const hasFormalPreview = Boolean(
    asset && Object.prototype.hasOwnProperty.call(asset, 'formal_preview'),
  );
  const formalPreview = asset?.formal_preview ?? null;
  const canReview = hasFormalPreview
    ? formalPreview?.state === 'ready'
    : !asset?.is_log && asset?.preview_status === PREVIEW_STATUS.READY;
  const presentation = formalPreviewPresentation(formalPreview);

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
    if (!canReview) {
      return;
    }
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
  }, [assetId, canReview, loadAsset, settings]);

  const cachePreview = useCallback(async () => {
    if (!canReview) {
      return;
    }
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
  }, [asset?.type, assetId, canReview, settings]);

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
    formalPreview,
    profileLabel: presentation.profileLabel,
    transformLabel: presentation.transformLabel,
    stateMessage: presentation.stateMessage,
    confirm,
    loadAsset,
    cachePreview,
  };
}

export function formalPreviewPresentation(formalPreview) {
  if (!formalPreview) {
    return { profileLabel: null, transformLabel: null, stateMessage: null };
  }
  if (formalPreview.state === 'generating') {
    return {
      profileLabel: null,
      transformLabel: null,
      stateMessage: 'Preview is generating',
    };
  }
  if (formalPreview.state === 'failed') {
    return {
      profileLabel: null,
      transformLabel: null,
      stateMessage: messageForErrorCode(formalPreview.failure_code),
    };
  }
  return {
    profileLabel: formalPreviewProfileLabel(formalPreview),
    transformLabel: (
      formalPreview.detection_status === 'apple_log'
      && formalPreview.color_transform_status === 'unavailable'
    )
      ? 'Color transform unavailable'
      : null,
    stateMessage: null,
  };
}
