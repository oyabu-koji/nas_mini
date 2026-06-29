import { Image, StyleSheet, Text, View } from 'react-native';
import { VideoView, useVideoPlayer } from 'expo-video';

import { ActionButton } from '../../../shared/components/ActionButton';
import { ScreenHeader } from '../../../shared/components/ScreenHeader';
import { StatusPill } from '../../../shared/components/StatusPill';
import { ASSET_TYPE, REVIEW_STATUS } from '../../../shared/constants/assetStatuses';
import { usePreviewReview } from '../hooks/usePreviewReview';

export function PreviewReviewScreen({ settings, canUseApi, assetId, onBack }) {
  const review = usePreviewReview(settings, canUseApi, assetId);

  return (
    <View style={styles.container}>
      <ScreenHeader title="Preview Review" subtitle={assetId ? `Confirm backend preview for asset #${assetId}` : null} />
      <ActionButton label="Back to detail" onPress={onBack} variant="secondary" />

      {review.assetError ? <Text style={styles.error}>{review.assetError.message}</Text> : null}
      {!review.asset && review.assetStatus === 'loading' ? <Text style={styles.meta}>Loading preview...</Text> : null}

      {review.asset ? (
        <View style={styles.detail}>
          <Text style={styles.filename}>{review.asset.filename}</Text>
          <View style={styles.statusRow}>
            <StatusPill status={review.asset.preview_status} />
            <StatusPill status={review.asset.review_status} />
          </View>

          {!review.canReview ? <Text style={styles.meta}>Preview is not ready.</Text> : null}
          {review.canReview && review.asset.type === ASSET_TYPE.VIDEO ? (
            <VideoPreview source={review.videoSource} />
          ) : null}
          {review.canReview && review.asset.type === ASSET_TYPE.IMAGE ? (
            <Image source={review.imageSource} style={styles.imagePreview} resizeMode="contain" />
          ) : null}

          {review.canReview ? (
            <View style={styles.actions}>
              <ActionButton
                disabled={review.confirmStatus === 'saving' || review.asset.review_status === REVIEW_STATUS.PREVIEW_CONFIRMED}
                label={review.asset.review_status === REVIEW_STATUS.PREVIEW_CONFIRMED ? 'Confirmed' : 'Confirm preview'}
                onPress={review.confirm}
              />
              <ActionButton
                disabled={review.cacheStatus === 'loading'}
                label={review.cacheStatus === 'loading' ? 'Preparing cache...' : 'Cache fallback'}
                onPress={review.cachePreview}
                variant="secondary"
              />
            </View>
          ) : null}

          {review.confirmError ? <Text style={styles.error}>{review.confirmError.message}</Text> : null}
          {review.cacheError ? <Text style={styles.error}>{review.cacheError.message}</Text> : null}
          {review.cachedPreviewUri ? <Text style={styles.meta}>Using cached preview for playback/display.</Text> : null}
        </View>
      ) : null}
    </View>
  );
}

function VideoPreview({ source }) {
  const player = useVideoPlayer(source, (createdPlayer) => {
    createdPlayer.loop = false;
  });

  return (
    <VideoView
      allowsFullscreen
      contentFit="contain"
      nativeControls
      player={player}
      style={styles.videoPreview}
    />
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 14,
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
  statusRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  videoPreview: {
    width: '100%',
    aspectRatio: 16 / 9,
    borderRadius: 8,
    backgroundColor: '#020617',
  },
  imagePreview: {
    width: '100%',
    aspectRatio: 1,
    borderRadius: 8,
    backgroundColor: '#f1f5f9',
  },
  actions: {
    gap: 10,
  },
  meta: {
    color: '#475569',
    fontSize: 13,
    lineHeight: 19,
  },
  error: {
    color: '#b91c1c',
    fontSize: 14,
    lineHeight: 20,
  },
});
