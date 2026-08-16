import React from 'react';
import { act, render } from '@testing-library/react-native';

import { createAppError } from '../../../shared/utils/errors';
import { usePreviewReview } from './usePreviewReview';

jest.mock('../../../shared/api/mediaVaultApi', () => ({
  buildPreviewSource: jest.fn(() => ({ uri: 'http://remote/image', headers: { Authorization: 'Bearer token' } })),
  buildPreviewVideoSource: jest.fn(() => ({ uri: 'http://remote/video', headers: { Authorization: 'Bearer token' } })),
  confirmPreview: jest.fn(),
}));
jest.mock('../../assets/hooks/useAssets', () => ({
  useAssetDetail: jest.fn(),
}));
jest.mock('../services/previewCacheService', () => ({
  downloadPreviewToCache: jest.fn(),
}));

const api = require('../../../shared/api/mediaVaultApi');
const { useAssetDetail } = require('../../assets/hooks/useAssets');
const { downloadPreviewToCache } = require('../services/previewCacheService');

const settings = { backendUrl: 'http://mediavault', apiToken: 'token' };
const loadAsset = jest.fn();
const readyVideo = {
  id: 42,
  filename: 'clip.mov',
  type: 'video',
  is_log: false,
  preview_status: 'preview_ready',
  review_status: 'not_reviewed',
};

function HookHarness() {
  global.latestPreviewReview = usePreviewReview(settings, true, 42);
  return null;
}

function mockAsset(asset, overrides = {}) {
  useAssetDetail.mockReturnValue({
    asset,
    status: asset ? 'ready' : 'loading',
    error: null,
    loadAsset,
    ...overrides,
  });
}

describe('usePreviewReview', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    loadAsset.mockResolvedValue(readyVideo);
    api.confirmPreview.mockResolvedValue({ review_status: 'preview_confirmed' });
    downloadPreviewToCache.mockResolvedValue('file:///cache/preview.mp4');
    mockAsset(readyVideo);
  });

  afterEach(() => {
    delete global.latestPreviewReview;
  });

  it('blocks LOG and unready assets from sources and actions', async () => {
    mockAsset({ ...readyVideo, is_log: true });
    const view = await render(<HookHarness />);
    expect(global.latestPreviewReview.canReview).toBe(false);
    expect(global.latestPreviewReview.videoSource).toBeNull();

    await act(async () => {
      await global.latestPreviewReview.confirm();
      await global.latestPreviewReview.cachePreview();
    });
    expect(api.confirmPreview).not.toHaveBeenCalled();
    expect(downloadPreviewToCache).not.toHaveBeenCalled();

    mockAsset({ ...readyVideo, preview_status: 'preview_generating' });
    await view.rerender(<HookHarness />);
    expect(global.latestPreviewReview.canReview).toBe(false);
  });

  it('uses formal authority for Apple Log fallback and terminal failures', async () => {
    const view = await render(<HookHarness />);
    mockAsset({
      ...readyVideo,
      is_log: true,
      formal_preview: {
        state: 'ready',
        detection_status: 'apple_log',
        source_profile: 'apple-log-1',
        color_transform_status: 'unavailable',
      },
    });
    await view.rerender(<HookHarness />);

    expect(global.latestPreviewReview.canReview).toBe(true);
    expect(global.latestPreviewReview.profileLabel).toBe('Apple Log 1 (unconverted)');
    expect(global.latestPreviewReview.transformLabel).toBe('Color transform unavailable');
    expect(global.latestPreviewReview.videoSource).not.toBeNull();

    mockAsset({
      ...readyVideo,
      formal_preview: {
        state: 'failed',
        failure_code: 'formal_preview_render_failed',
      },
    });
    await view.rerender(<HookHarness />);
    expect(global.latestPreviewReview.canReview).toBe(false);
    expect(global.latestPreviewReview.stateMessage).toBe(
      'The formal preview could not be rendered.',
    );
  });

  it('uses fixed Apple Log 2, ordinary, and unknown labels without an applied branch', async () => {
    const view = await render(<HookHarness />);
    mockAsset({
      ...readyVideo,
      formal_preview: {
        state: 'ready',
        detection_status: 'apple_log',
        source_profile: 'apple-log-2',
        color_transform_status: 'unavailable',
      },
    });
    await view.rerender(<HookHarness />);
    expect(global.latestPreviewReview.profileLabel).toBe('Apple Log 2 (unconverted)');
    expect(global.latestPreviewReview.transformLabel).toBe('Color transform unavailable');

    mockAsset({
      ...readyVideo,
      formal_preview: {
        state: 'ready',
        detection_status: 'not_log',
        color_transform_status: 'not_requested',
      },
    });
    await view.rerender(<HookHarness />);
    expect(global.latestPreviewReview.profileLabel).toBe('Ordinary video');

    mockAsset({
      ...readyVideo,
      formal_preview: {
        state: 'ready',
        detection_status: 'unknown',
        color_transform_status: 'not_requested',
      },
    });
    await view.rerender(<HookHarness />);
    expect(global.latestPreviewReview.profileLabel).toBe(
      'Video profile unknown (unconverted)',
    );

    expect(global.latestPreviewReview.profileLabel).not.toContain('Apple Log (unconverted)');
    expect(global.latestPreviewReview.transformLabel).toBeNull();
  });

  it('builds remote video and image sources and switches to cached source', async () => {
    const view = await render(<HookHarness />);
    expect(global.latestPreviewReview.videoSource).toEqual({
      uri: 'http://remote/video',
      headers: { Authorization: 'Bearer token' },
    });
    expect(global.latestPreviewReview.imageSource).toBeNull();

    await act(async () => {
      await global.latestPreviewReview.cachePreview();
    });
    expect(downloadPreviewToCache).toHaveBeenCalledWith({ settings, assetId: 42, extension: 'mp4' });
    expect(global.latestPreviewReview.videoSource).toEqual({ uri: 'file:///cache/preview.mp4' });
    expect(global.latestPreviewReview.cacheStatus).toBe('ready');

    mockAsset({ ...readyVideo, type: 'image', filename: 'still.jpg' });
    await view.rerender(<HookHarness />);
    expect(global.latestPreviewReview.imageSource).toEqual({ uri: 'file:///cache/preview.mp4' });
  });

  it('confirms a review then reloads the current asset', async () => {
    await render(<HookHarness />);

    await act(async () => {
      await global.latestPreviewReview.confirm();
    });

    expect(api.confirmPreview).toHaveBeenCalledWith(settings, 42);
    expect(loadAsset).toHaveBeenCalledTimes(1);
    expect(global.latestPreviewReview.confirmStatus).toBe('confirmed');
  });

  it('exposes safe confirm and cache failures for retry', async () => {
    api.confirmPreview.mockRejectedValueOnce(createAppError('network_unreachable', 'Backend unavailable'));
    downloadPreviewToCache.mockRejectedValueOnce(createAppError('storage_or_cache_error', 'Cache unavailable'));
    await render(<HookHarness />);

    await act(async () => {
      await global.latestPreviewReview.confirm();
      await global.latestPreviewReview.cachePreview();
    });

    expect(global.latestPreviewReview.confirmStatus).toBe('error');
    expect(global.latestPreviewReview.confirmError).toMatchObject({ code: 'network_unreachable' });
    expect(global.latestPreviewReview.cacheStatus).toBe('error');
    expect(global.latestPreviewReview.cacheError).toMatchObject({ code: 'storage_or_cache_error' });
  });
});
