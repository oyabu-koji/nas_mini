import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react-native';

import { AssetPickerScreen } from './AssetPickerScreen';

jest.mock('../hooks/useAssetUpload', () => ({
  useAssetUpload: jest.fn(),
}));

const { useAssetUpload } = require('../hooks/useAssetUpload');

const videoAsset = {
  type: 'video',
  filename: 'clip.mov',
  sizeBytes: 16,
  durationMs: 1000,
  localAssetId: 'library-123',
  takenAt: null,
  latitude: null,
  longitude: null,
  exif: null,
};

function uploadState(overrides = {}) {
  return {
    pickedAsset: videoAsset,
    isLog: false,
    setIsLog: jest.fn(),
    status: 'uploading_chunks',
    error: null,
    uploadResult: null,
    pendingUpload: null,
    pendingLoading: false,
    maxUploadSizeBytes: 104857600,
    isTooLarge: false,
    hasKnownUploadableSize: true,
    canPickAsset: false,
    canUpload: false,
    pickAsset: jest.fn(),
    startUpload: jest.fn(),
    resumableVideo: {
      progress: { uploadedBytes: 8, totalBytes: 16 },
      canCancel: true,
      cancelUpload: jest.fn(),
    },
    ...overrides,
  };
}

describe('AssetPickerScreen video upload state', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows chunk progress and an explicit cancellation command without an original deletion action', async () => {
    const state = uploadState();
    useAssetUpload.mockReturnValue(state);

    await render(<AssetPickerScreen settings={{}} canUseApi />);

    expect(screen.getByText('Uploading video...')).toBeTruthy();
    expect(screen.getByText('8 B / 16 B')).toBeTruthy();
    await act(async () => {
      fireEvent.press(screen.getByText('Cancel upload'));
    });
    expect(state.resumableVideo.cancelUpload).toHaveBeenCalled();
    expect(screen.queryByText(/delete/i)).toBeNull();
  });

  it('uses an explicit resume command for retryable session state', async () => {
    useAssetUpload.mockReturnValue(uploadState({ status: 'retryable_failed', canUpload: true }));
    await render(<AssetPickerScreen settings={{}} canUseApi />);
    expect(screen.getByText('Resume upload')).toBeTruthy();
  });

  it('uses an explicit new-upload command for expired sessions', async () => {
    useAssetUpload.mockReturnValue(uploadState({ status: 'expired', canUpload: true }));
    await render(<AssetPickerScreen settings={{}} canUseApi />);
    expect(screen.getByText('Start new upload')).toBeTruthy();
    expect(screen.getByText('Upload expired')).toBeTruthy();
  });

  it('describes LOG as a legacy hint while preserving the toggle behavior', async () => {
    const state = uploadState({ status: 'idle', canPickAsset: true, canUpload: true });
    useAssetUpload.mockReturnValue(state);
    await render(<AssetPickerScreen settings={{}} canUseApi />);

    expect(
      screen.getByText('Stored as a legacy hint. Apple Log detection is automatic.'),
    ).toBeTruthy();
    expect(screen.queryByText('Apply backend LOG preview pipeline.')).toBeNull();
    await act(async () => {
      fireEvent(screen.getByRole('switch'), 'valueChange', true);
    });
    expect(state.setIsLog).toHaveBeenCalledWith(true);
  });
});
