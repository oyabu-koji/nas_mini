import { StyleSheet, Text, View } from 'react-native';

import { ActionButton } from '../../../shared/components/ActionButton';
import { ScreenHeader } from '../../../shared/components/ScreenHeader';
import { StatusPill } from '../../../shared/components/StatusPill';
import { PREVIEW_STATUS } from '../../../shared/constants/assetStatuses';
import { formatBytes } from '../../../shared/utils/fileSize';
import { useProcessedResultSave } from '../../processed-results/hooks/useProcessedResultSave';
import { PresetSelector } from '../../managed-renditions/components/PresetSelector';
import { useManagedRendition } from '../../managed-renditions/hooks/useManagedRendition';
import { useAssetDetail } from '../hooks/useAssets';

export function AssetDetailScreen({ settings, canUseApi, assetId, mappingUnavailable = false, onBack, onPreview }) {
  const { asset, status, error, loadAsset } = useAssetDetail(settings, canUseApi, assetId, { autoPoll: true });
  const processedResultSave = useProcessedResultSave({
    settings,
    assetId,
    result: asset?.active_processed_result ?? null,
    onSuperseded: loadAsset,
  });
  const managedRendition = useManagedRendition({
    settings,
    canUseApi,
    asset,
    loadAsset,
  });

  return (
    <View style={styles.container}>
      <ScreenHeader title="Asset Detail" subtitle={assetId ? `Backend asset #${assetId}` : 'No asset selected.'} />
      <View style={styles.actions}>
        <ActionButton label="Back to assets" onPress={onBack} variant="secondary" />
        <ActionButton disabled={status === 'loading'} label="Refresh" onPress={loadAsset} variant="secondary" />
      </View>

      {error ? <Text style={styles.error}>{error.message}</Text> : null}
      {!asset && status === 'loading' ? <Text style={styles.meta}>Loading asset...</Text> : null}

      {asset ? (
        <View style={styles.detail}>
          <Text style={styles.filename}>{asset.filename}</Text>
          <Text style={styles.meta}>{asset.type} / {formatBytes(asset.size_bytes)}</Text>
          <Text style={styles.meta}>SHA256: {asset.server_sha256}</Text>
          <Text style={styles.meta}>Taken at: {asset.taken_at || 'unknown'}</Text>
          <Text style={styles.meta}>LOG: {asset.is_log ? 'yes' : 'no'}</Text>
          <View style={styles.statusGrid}>
            <StatusBlock label="Transfer" status={asset.transfer_status} />
            <StatusBlock label="Verification" status={asset.verification_status} />
            <StatusBlock label="Preview" status={asset.preview_status} />
            <StatusBlock label="Review" status={asset.review_status} />
          </View>

          {asset.preview_status === PREVIEW_STATUS.GENERATING ? (
            <Text style={styles.meta}>Preview is generating. Manual refresh is available; lightweight polling is active while this screen is open.</Text>
          ) : null}
          {asset.preview_status === PREVIEW_STATUS.FAILED ? (
            <Text style={styles.error}>Preview generation failed. Retry API is outside this feature.</Text>
          ) : null}
          {mappingUnavailable ? <Text style={styles.meta}>Local asset mapping is unavailable for this upload.</Text> : null}
          <ActionButton
            disabled={asset.is_log || asset.preview_status !== PREVIEW_STATUS.READY}
            label="Open preview"
            onPress={() => onPreview(asset.id)}
          />
          <PresetSelector managed={managedRendition} />

          {!asset.is_log && asset.active_processed_result ? (
            <View style={styles.processedResult}>
              <Text style={styles.sectionLabel}>Processed video</Text>
              <Text style={styles.meta}>{asset.active_processed_result.mime_type} / {formatBytes(asset.active_processed_result.size_bytes)}</Text>
              <Text style={styles.meta}>SHA256: {asset.active_processed_result.sha256}</Text>
              {processedResultSave.status !== 'idle' && processedResultSave.status !== 'saved' ? (
                <Text style={styles.meta}>{saveStatusLabel(processedResultSave.status)}</Text>
              ) : null}
              {processedResultSave.error ? <Text style={styles.error}>{processedResultSave.error.message}</Text> : null}
              {processedResultSave.status === 'saved' ? <Text style={styles.saved}>Saved to photo library.</Text> : null}
              {processedResultSave.status === 'superseded' ? (
                <ActionButton label="Refresh" onPress={loadAsset} variant="secondary" />
              ) : (
                <ActionButton
                  disabled={!processedResultSave.canSave || processedResultSave.status === 'saved' || processedResultSave.canCancel}
                  label={processedResultSave.status === 'unknown' ? 'Save again' : 'Save processed video'}
                  onPress={processedResultSave.save}
                />
              )}
              {processedResultSave.canCancel ? (
                <ActionButton label="Cancel save" onPress={processedResultSave.cancel} variant="secondary" />
              ) : null}
            </View>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

function saveStatusLabel(status) {
  const labels = {
    downloading: 'Downloading processed video...',
    verifying: 'Verifying processed video...',
    requesting_library_permission: 'Requesting photo library permission...',
    saving_to_library: 'Saving to photo library...',
    failed: 'Save failed.',
    unknown: 'Save outcome needs confirmation.',
  };
  return labels[status] ?? '';
}

function StatusBlock({ label, status }) {
  return (
    <View style={styles.statusBlock}>
      <Text style={styles.statusLabel}>{label}</Text>
      <StatusPill status={status} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 14,
  },
  actions: {
    flexDirection: 'row',
    gap: 10,
  },
  detail: {
    gap: 12,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    padding: 12,
    backgroundColor: '#ffffff',
  },
  filename: {
    color: '#0f172a',
    fontSize: 17,
    fontWeight: '800',
  },
  meta: {
    color: '#475569',
    fontSize: 13,
    lineHeight: 19,
  },
  statusGrid: {
    gap: 10,
  },
  statusBlock: {
    gap: 5,
  },
  statusLabel: {
    color: '#334155',
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  sectionLabel: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '800',
  },
  processedResult: {
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: '#cbd5e1',
    paddingTop: 12,
  },
  error: {
    color: '#b91c1c',
    fontSize: 14,
    lineHeight: 20,
  },
  saved: {
    color: '#166534',
    fontSize: 14,
    lineHeight: 20,
  },
});
