import { useCallback, useEffect, useRef, useState } from 'react';

import { uploadAsset } from '../../../shared/api/mediaVaultApi';
import { MAX_UPLOAD_SIZE_BYTES } from '../../../shared/constants/assetStatuses';
import { saveLocalAssetMapping } from '../../../shared/services/localAssetMappingStore';
import {
  blocksAssetSelection,
  blocksUploadForAsset,
  readUploadResultUnknown,
  saveUploadResultUnknown,
} from '../../../shared/services/uploadResultUnknownStore';
import { canUploadSize, isUploadTooLarge } from '../../../shared/utils/fileSize';
import { createAppError, messageForErrorCode, toDisplayError } from '../../../shared/utils/errors';
import { pickSingleMediaAsset } from '../services/mediaPickerService';
import { useResumableVideoUpload } from './useResumableVideoUpload';

export function useAssetUpload({ settings, canUseApi, onMappingUnavailable, onUploaded }) {
  const [pickedAsset, setPickedAsset] = useState(null);
  const [isLog, setIsLog] = useState(false);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [pendingUpload, setPendingUpload] = useState(null);
  const [pendingLoading, setPendingLoading] = useState(true);
  const uploadInFlightRef = useRef(false);
  const resumableVideo = useResumableVideoUpload({
    settings,
    pickedAsset,
    isLog,
    canUseApi,
    onMappingUnavailable,
    onUploaded,
  });

  useEffect(() => {
    let isMounted = true;

    async function restorePendingUpload() {
      const restored = await readUploadResultUnknown();
      if (!isMounted) {
        return;
      }
      setPendingUpload(restored);
      if (restored) {
        setStatus('result_unknown');
      }
      setPendingLoading(false);
    }

    restorePendingUpload();
    return () => {
      isMounted = false;
    };
  }, []);

  const pickAsset = useCallback(async () => {
    if (pendingLoading || blocksAssetSelection(pendingUpload)) {
      return;
    }
    setStatus('picking');
    setError(null);
    try {
      const result = await pickSingleMediaAsset();
      if (result.canceled) {
        setStatus('idle');
        return;
      }
      setPickedAsset(result.asset);
      setUploadResult(null);
      setStatus('ready');
    } catch {
      setStatus('error');
      setError({ message: 'Could not open the photo library.' });
    }
  }, [pendingLoading, pendingUpload]);

  const startUpload = useCallback(async () => {
    if (uploadInFlightRef.current) {
      return;
    }
    uploadInFlightRef.current = true;

    try {
      setError(null);
      if (pendingLoading) {
        return;
      }
      if (!canUseApi) {
        setError(toDisplayError(createAppError('missing_settings', messageForErrorCode('missing_settings'))));
        setStatus('error');
        return;
      }
      if (pickedAsset?.type === 'video') {
        await resumableVideo.startUpload();
        return;
      }
      if (!pickedAsset?.uri || !canUploadSize(pickedAsset.sizeBytes)) {
        const code = isUploadTooLarge(pickedAsset?.sizeBytes) ? 'too_large' : 'validation_error';
        setError(toDisplayError(createAppError(code, messageForErrorCode(code))));
        setStatus('error');
        return;
      }

      const latestPending = await readUploadResultUnknown();
      setPendingUpload(latestPending);
      if (blocksUploadForAsset(latestPending, pickedAsset.localAssetId)) {
        setStatus('result_unknown');
        setError({
          code: 'result_unknown',
          message: 'The previous upload result is unknown. Check the asset list before uploading again.',
        });
        return;
      }

      setStatus('uploading');
      const result = await uploadAsset({ settings, pickedAsset, isLog });
      setUploadResult(result);
      setStatus('uploaded');
      onUploaded?.(result.asset.id);
      if (pickedAsset.localAssetId && result?.asset?.id) {
        Promise.resolve()
          .then(() => saveLocalAssetMapping({
            backendAssetId: result.asset.id,
            localAssetId: pickedAsset.localAssetId,
          }))
          .catch(() => {
            onMappingUnavailable?.(result.asset.id);
          });
      }
    } catch (uploadError) {
      if (uploadError?.code === 'timeout') {
        const pending = pickedAsset.localAssetId
          ? { kind: 'local_asset', localAssetId: pickedAsset.localAssetId }
          : { kind: 'global_pending' };
        try {
          await saveUploadResultUnknown(pending);
          setPendingUpload(pending);
        } catch {
          setPendingUpload({ kind: 'global_pending' });
        }
        setStatus('result_unknown');
        setError({
          code: 'result_unknown',
          message: 'The upload result is unknown. Check the asset list before uploading again.',
        });
        return;
      }
      setStatus('error');
      setError(toDisplayError(uploadError));
    } finally {
      uploadInFlightRef.current = false;
    }
  }, [canUseApi, isLog, onMappingUnavailable, onUploaded, pendingLoading, pickedAsset, resumableVideo, settings]);

  const isVideo = pickedAsset?.type === 'video';
  const isTooLarge = !isVideo && isUploadTooLarge(pickedAsset?.sizeBytes);
  const hasKnownUploadableSize = isVideo || canUploadSize(pickedAsset?.sizeBytes);
  const isPendingForPickedAsset = blocksUploadForAsset(pendingUpload, pickedAsset?.localAssetId);
  const canPickAsset = !pendingLoading && !blocksAssetSelection(pendingUpload) && status !== 'picking' && !resumableVideo.canCancel;
  const canUpload =
    isVideo
      ? resumableVideo.canStart
      : status !== 'uploading'
        && !pendingLoading
        && !isPendingForPickedAsset
        && Boolean(pickedAsset)
        && canUseApi
        && hasKnownUploadableSize;

  return {
    pickedAsset,
    isLog,
    setIsLog,
    status: isVideo ? resumableVideo.status : status,
    error: isVideo ? resumableVideo.error : error,
    uploadResult,
    pendingUpload,
    pendingLoading,
    maxUploadSizeBytes: MAX_UPLOAD_SIZE_BYTES,
    isTooLarge,
    hasKnownUploadableSize,
    canPickAsset,
    canUpload,
    pickAsset,
    startUpload,
    resumableVideo,
  };
}
