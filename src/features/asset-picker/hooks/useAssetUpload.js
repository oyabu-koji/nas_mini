import { useCallback, useState } from 'react';

import { uploadAsset } from '../../../shared/api/mediaVaultApi';
import { MAX_UPLOAD_SIZE_BYTES } from '../../../shared/constants/assetStatuses';
import { saveLocalAssetMapping } from '../../../shared/services/localAssetMappingStore';
import { canUploadSize, isUploadTooLarge } from '../../../shared/utils/fileSize';
import { createAppError, messageForErrorCode, toDisplayError } from '../../../shared/utils/errors';
import { pickSingleMediaAsset } from '../services/mediaPickerService';

export function useAssetUpload({ settings, canUseApi, onUploaded }) {
  const [pickedAsset, setPickedAsset] = useState(null);
  const [isLog, setIsLog] = useState(false);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);

  const pickAsset = useCallback(async () => {
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
  }, []);

  const startUpload = useCallback(async () => {
    setError(null);
    if (!canUseApi) {
      setError(toDisplayError(createAppError('missing_settings', messageForErrorCode('missing_settings'))));
      setStatus('error');
      return;
    }
    if (!pickedAsset?.uri || !canUploadSize(pickedAsset.sizeBytes)) {
      const code = isUploadTooLarge(pickedAsset?.sizeBytes) ? 'too_large' : 'validation_error';
      setError(toDisplayError(createAppError(code, messageForErrorCode(code))));
      setStatus('error');
      return;
    }

    setStatus('uploading');
    try {
      const result = await uploadAsset({ settings, pickedAsset, isLog });
      setUploadResult(result);
      if (pickedAsset.localAssetId && result?.asset?.id) {
        await saveLocalAssetMapping({
          backendAssetId: result.asset.id,
          localAssetId: pickedAsset.localAssetId,
        });
      }
      setStatus('uploaded');
      onUploaded?.(result.asset.id);
    } catch (uploadError) {
      setStatus('error');
      setError(toDisplayError(uploadError));
    }
  }, [canUseApi, isLog, onUploaded, pickedAsset, settings]);

  const isTooLarge = isUploadTooLarge(pickedAsset?.sizeBytes);
  const hasKnownUploadableSize = canUploadSize(pickedAsset?.sizeBytes);
  const canUpload = status !== 'uploading' && Boolean(pickedAsset) && canUseApi && hasKnownUploadableSize;

  return {
    pickedAsset,
    isLog,
    setIsLog,
    status,
    error,
    uploadResult,
    maxUploadSizeBytes: MAX_UPLOAD_SIZE_BYTES,
    isTooLarge,
    hasKnownUploadableSize,
    canUpload,
    pickAsset,
    startUpload,
  };
}
