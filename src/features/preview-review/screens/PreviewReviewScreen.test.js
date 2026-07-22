import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { PreviewReviewScreen } from './PreviewReviewScreen';

jest.mock('expo-video', () => ({
  VideoView: (props) => {
    const { View } = require('react-native');
    return <View {...props} testID="video-view" />;
  },
  useVideoPlayer: jest.fn((source, configure) => {
    const player = { source };
    configure(player);
    return player;
  }),
}));
jest.mock('../hooks/usePreviewReview', () => ({
  usePreviewReview: jest.fn(),
}));

const { usePreviewReview } = require('../hooks/usePreviewReview');

const readyVideo = {
  id: 42,
  filename: 'clip.mov',
  type: 'video',
  is_log: false,
  preview_status: 'preview_ready',
  review_status: 'not_reviewed',
};

function reviewState(overrides = {}) {
  return {
    asset: readyVideo,
    assetStatus: 'ready',
    assetError: null,
    canReview: true,
    videoSource: { uri: 'http://backend.test/video' },
    imageSource: null,
    confirmStatus: 'idle',
    confirmError: null,
    cacheStatus: 'idle',
    cacheError: null,
    cachedPreviewUri: null,
    confirm: jest.fn(),
    cachePreview: jest.fn(),
    ...overrides,
  };
}

describe('PreviewReviewScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows loading and error states and returns to detail', async () => {
    const onBack = jest.fn();
    usePreviewReview.mockReturnValue(reviewState({
      asset: null,
      assetStatus: 'loading',
      assetError: { message: 'Preview unavailable' },
      canReview: false,
    }));
    await render(<PreviewReviewScreen settings={{}} canUseApi assetId={42} onBack={onBack} />);

    expect(screen.getByText('Loading preview...')).toBeTruthy();
    expect(screen.getByText('Preview unavailable')).toBeTruthy();
    await fireEvent.press(screen.getByRole('button', { name: 'Back to detail' }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('renders video and forwards confirm/cache commands', async () => {
    const state = reviewState();
    usePreviewReview.mockReturnValue(state);
    await render(<PreviewReviewScreen settings={{}} canUseApi assetId={42} onBack={jest.fn()} />);

    const video = screen.getByTestId('video-view');
    expect(video.props.player.source).toEqual(state.videoSource);
    expect(video.props.player.loop).toBe(false);
    await fireEvent.press(screen.getByRole('button', { name: 'Confirm preview' }));
    await fireEvent.press(screen.getByRole('button', { name: 'Cache fallback' }));
    expect(state.confirm).toHaveBeenCalledTimes(1);
    expect(state.cachePreview).toHaveBeenCalledTimes(1);
  });

  it('renders an image and disables completed or busy actions with safe errors', async () => {
    const state = reviewState({
      asset: { ...readyVideo, type: 'image', filename: 'still.jpg', review_status: 'preview_confirmed' },
      videoSource: null,
      imageSource: { uri: 'file:///cache/still.jpg' },
      confirmStatus: 'confirmed',
      cacheStatus: 'loading',
      confirmError: { message: 'Confirm failed' },
      cacheError: { message: 'Cache failed' },
      cachedPreviewUri: 'file:///cache/still.jpg',
    });
    usePreviewReview.mockReturnValue(state);
    await render(<PreviewReviewScreen settings={{}} canUseApi assetId={42} onBack={jest.fn()} />);

    expect(screen.getByText('still.jpg')).toBeTruthy();
    expect(screen.queryByTestId('video-view')).toBeNull();
    expect(screen.getByRole('button', { name: 'Confirmed' }).props.accessibilityState.disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Preparing cache...' }).props.accessibilityState.disabled).toBe(true);
    expect(screen.getByText('Confirm failed')).toBeTruthy();
    expect(screen.getByText('Cache failed')).toBeTruthy();
    expect(screen.getByText('Using cached preview for playback/display.')).toBeTruthy();
  });

  it('explains when a LOG or otherwise unready preview cannot be reviewed', async () => {
    usePreviewReview.mockReturnValue(reviewState({
      asset: { ...readyVideo, is_log: true, preview_status: 'failed' },
      canReview: false,
      videoSource: null,
    }));
    await render(<PreviewReviewScreen settings={{}} canUseApi assetId={42} onBack={jest.fn()} />);

    expect(screen.getByText('Preview is not ready.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Confirm preview' })).toBeNull();
  });
});
