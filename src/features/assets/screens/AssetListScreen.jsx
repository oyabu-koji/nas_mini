import { useCallback, useEffect, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { ActionButton } from '../../../shared/components/ActionButton';
import { ScreenHeader } from '../../../shared/components/ScreenHeader';
import { StatusPill } from '../../../shared/components/StatusPill';
import { formatBytes } from '../../../shared/utils/fileSize';
import {
  clearUploadResultUnknown,
  readUploadResultUnknown,
} from '../../../shared/services/uploadResultUnknownStore';
import { useAssetList } from '../hooks/useAssets';

export function AssetListScreen({ settings, canUseApi, onOpenSettings, onPendingAcknowledged, onSelectAsset }) {
  const { items, status, error, refreshAssets } = useAssetList(settings, canUseApi, { autoLoad: false });
  const [pendingUpload, setPendingUpload] = useState(null);
  const [pendingLoading, setPendingLoading] = useState(true);
  const [canAcknowledgePending, setCanAcknowledgePending] = useState(false);
  const [acknowledgementError, setAcknowledgementError] = useState(null);

  const refreshAfterPendingRead = useCallback(async () => {
    setCanAcknowledgePending(false);
    const result = await refreshAssets();
    if (pendingUpload && result.success && result.isLatest) {
      setCanAcknowledgePending(true);
    }
    return result;
  }, [pendingUpload, refreshAssets]);

  useEffect(() => {
    let isMounted = true;

    async function loadPendingThenAssets() {
      const restored = await readUploadResultUnknown();
      if (!isMounted) {
        return;
      }
      setPendingUpload(restored);
      setPendingLoading(false);
      setCanAcknowledgePending(false);
      const result = await refreshAssets();
      if (isMounted && restored && result.success && result.isLatest) {
        setCanAcknowledgePending(true);
      }
    }

    loadPendingThenAssets();
    return () => {
      isMounted = false;
    };
  }, [refreshAssets]);

  const acknowledgePending = useCallback(async () => {
    if (!pendingUpload || !canAcknowledgePending) {
      return;
    }
    setAcknowledgementError(null);
    try {
      await clearUploadResultUnknown();
      setPendingUpload(null);
      setCanAcknowledgePending(false);
      onPendingAcknowledged?.();
    } catch {
      setAcknowledgementError('Could not clear the pending upload state.');
    }
  }, [canAcknowledgePending, onPendingAcknowledged, pendingUpload]);

  return (
    <View style={styles.container}>
      <ScreenHeader title="Assets" subtitle="Review uploads saved on the backend." />

      {!canUseApi ? (
        <View style={styles.notice}>
          <Text style={styles.noticeText}>Backend URL and API token are required before loading assets.</Text>
          <ActionButton label="Open settings" onPress={onOpenSettings} variant="secondary" />
        </View>
      ) : (
        <ActionButton
          disabled={pendingLoading || status === 'loading'}
          label={status === 'loading' ? 'Refreshing...' : 'Refresh'}
          onPress={refreshAfterPendingRead}
          variant="secondary"
        />
      )}

      {error ? <Text style={styles.error}>{error.message}</Text> : null}
      {pendingUpload ? (
        <View style={styles.notice}>
          <Text style={styles.noticeText}>A previous upload may have completed. Review the refreshed list before uploading again.</Text>
          <ActionButton
            disabled={!canAcknowledgePending}
            label="I reviewed the asset list"
            onPress={acknowledgePending}
            variant="secondary"
          />
        </View>
      ) : null}
      {acknowledgementError ? <Text style={styles.error}>{acknowledgementError}</Text> : null}

      <FlatList
        data={items}
        keyExtractor={(item) => String(item.id)}
        ListEmptyComponent={
          canUseApi && status !== 'loading' ? <Text style={styles.empty}>No assets found.</Text> : null
        }
        renderItem={({ item }) => (
          <Pressable onPress={() => onSelectAsset(item.id)} style={styles.item}>
            <View style={styles.itemHeader}>
              <Text numberOfLines={1} style={styles.filename}>{item.filename}</Text>
              <Text style={styles.assetId}>#{item.id}</Text>
            </View>
            <Text style={styles.meta}>{item.type} / {formatBytes(item.size_bytes)}</Text>
            <Text style={styles.meta}>Created: {formatDateTime(item.created_at)}</Text>
            <View style={styles.statusRow}>
              <StatusPill status={item.preview_status} />
              <StatusPill status={item.review_status} />
            </View>
          </Pressable>
        )}
        scrollEnabled={false}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 14,
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
  item: {
    gap: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    marginBottom: 10,
    padding: 12,
    backgroundColor: '#ffffff',
  },
  itemHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  filename: {
    flex: 1,
    color: '#0f172a',
    fontSize: 15,
    fontWeight: '800',
  },
  assetId: {
    color: '#64748b',
    fontSize: 12,
    fontWeight: '700',
  },
  meta: {
    color: '#475569',
    fontSize: 13,
  },
  statusRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  empty: {
    color: '#64748b',
    fontSize: 14,
  },
  error: {
    color: '#b91c1c',
    fontSize: 14,
  },
});

function formatDateTime(value) {
  if (!value) {
    return 'unknown';
  }
  return String(value);
}
