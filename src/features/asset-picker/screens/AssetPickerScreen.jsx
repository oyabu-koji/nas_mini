import { StyleSheet, Switch, Text, View } from 'react-native';

import { ActionButton } from '../../../shared/components/ActionButton';
import { ScreenHeader } from '../../../shared/components/ScreenHeader';
import { MAX_UPLOAD_SIZE_BYTES } from '../../../shared/constants/assetStatuses';
import { formatBytes } from '../../../shared/utils/fileSize';
import { useAssetUpload } from '../hooks/useAssetUpload';

export function AssetPickerScreen({ settings, canUseApi, onUploaded, onOpenSettings }) {
  const upload = useAssetUpload({ settings, canUseApi, onUploaded });
  const asset = upload.pickedAsset;

  return (
    <View style={styles.container}>
      <ScreenHeader
        title="Upload"
        subtitle={`Pick one photo or video. Phase 1 limit is ${formatBytes(MAX_UPLOAD_SIZE_BYTES)}.`}
      />

      {!canUseApi ? (
        <View style={styles.notice}>
          <Text style={styles.noticeText}>Backend URL and API token are required before upload.</Text>
          <ActionButton label="Open settings" onPress={onOpenSettings} variant="secondary" />
        </View>
      ) : null}

      <ActionButton disabled={upload.status === 'picking'} label="Choose photo or video" onPress={upload.pickAsset} />

      {asset ? (
        <View style={styles.summary}>
          <Text style={styles.filename}>{asset.filename}</Text>
          <Text style={styles.meta}>{asset.type} / {formatBytes(asset.sizeBytes)}</Text>
          <Text style={styles.meta}>Duration: {formatDuration(asset.durationMs)}</Text>
          <Text style={styles.meta}>Taken at: {asset.takenAt || 'unknown'}</Text>
          <Text style={styles.meta}>Location: {formatLocation(asset)}</Text>
          <Text style={styles.meta}>EXIF: {asset.exif ? 'available' : 'not available'}</Text>
          <Text style={styles.meta}>{asset.localAssetId ? 'Local asset id available' : 'Local asset id unavailable'}</Text>
          {upload.isTooLarge ? <Text style={styles.error}>This file is above the Phase 1 limit.</Text> : null}
          {!upload.hasKnownUploadableSize && !upload.isTooLarge ? (
            <Text style={styles.error}>File size is unavailable. Upload is disabled for this asset.</Text>
          ) : null}
          <View style={styles.toggleRow}>
            <View style={styles.toggleText}>
              <Text style={styles.toggleTitle}>LOG material</Text>
              <Text style={styles.meta}>Apply backend LOG preview pipeline.</Text>
            </View>
            <Switch onValueChange={upload.setIsLog} value={upload.isLog} />
          </View>
          <ActionButton
            disabled={!upload.canUpload}
            label={upload.status === 'uploading' ? 'Uploading...' : 'Upload'}
            onPress={upload.startUpload}
          />
        </View>
      ) : null}

      {upload.error ? <Text style={styles.error}>{upload.error.message}</Text> : null}
      {upload.uploadResult?.asset ? (
        <Text style={styles.success}>Uploaded asset #{upload.uploadResult.asset.id}. Preview job was queued.</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 16,
  },
  notice: {
    gap: 10,
    borderWidth: 1,
    borderColor: '#f59e0b',
    borderRadius: 8,
    padding: 12,
    backgroundColor: '#fffbeb',
  },
  noticeText: {
    color: '#92400e',
    fontSize: 14,
  },
  summary: {
    gap: 12,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    padding: 12,
    backgroundColor: '#ffffff',
  },
  filename: {
    color: '#0f172a',
    fontSize: 16,
    fontWeight: '800',
  },
  meta: {
    color: '#475569',
    fontSize: 13,
  },
  toggleRow: {
    minHeight: 52,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  toggleText: {
    flex: 1,
    gap: 2,
  },
  toggleTitle: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700',
  },
  error: {
    color: '#b91c1c',
    fontSize: 14,
    lineHeight: 20,
  },
  success: {
    color: '#166534',
    fontSize: 14,
    lineHeight: 20,
  },
});

function formatDuration(durationMs) {
  if (!Number.isFinite(durationMs)) {
    return 'not available';
  }
  const totalSeconds = Math.max(0, Math.round(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function formatLocation(asset) {
  if (!Number.isFinite(asset.latitude) || !Number.isFinite(asset.longitude)) {
    return 'not available';
  }
  return `${asset.latitude.toFixed(5)}, ${asset.longitude.toFixed(5)}`;
}
