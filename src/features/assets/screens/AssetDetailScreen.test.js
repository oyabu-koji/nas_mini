import { fireEvent, render } from '@testing-library/react-native';

import { AssetDetailScreen } from './AssetDetailScreen';

jest.mock('../hooks/useAssets', () => ({
  useAssetDetail: jest.fn(),
}));
jest.mock('../../processed-results/hooks/useProcessedResultSave', () => ({
  useProcessedResultSave: jest.fn(),
}));
jest.mock('../../managed-renditions/hooks/useManagedRendition', () => ({
  useManagedRendition: jest.fn(),
}));
jest.mock('../../original-deletion/hooks/useDeletionCapability', () => ({
  useDeletionCapability: jest.fn(),
}));
jest.mock('../../original-deletion/hooks/useOriginalDeletion', () => ({
  useOriginalDeletion: jest.fn(),
}));

const { useAssetDetail } = require('../hooks/useAssets');
const { useProcessedResultSave } = require('../../processed-results/hooks/useProcessedResultSave');
const { useManagedRendition } = require('../../managed-renditions/hooks/useManagedRendition');
const { useDeletionCapability } = require('../../original-deletion/hooks/useDeletionCapability');
const { useOriginalDeletion } = require('../../original-deletion/hooks/useOriginalDeletion');

describe('AssetDetailScreen LOG safety gate', () => {
  beforeEach(() => {
    useDeletionCapability.mockReturnValue({
      capabilities: {
        features: {
          formalAppleLogPreview: true,
          safeDeleteCandidate: true,
        },
      },
      status: 'ready',
      error: null,
      refreshCapabilities: jest.fn().mockResolvedValue(null),
    });
    useOriginalDeletion.mockReturnValue({
      canDelete: false,
      status: 'idle',
      error: null,
      requestDeletion: jest.fn(),
    });
    useManagedRendition.mockReturnValue({
      eligible: false,
      catalogStatus: 'idle',
      capabilities: {
        features: { formalAppleLogPreview: true },
      },
      presets: [],
      selectedPresetId: null,
      submitStatus: 'idle',
      rendition: null,
      error: null,
      selectPreset: jest.fn(),
      submit: jest.fn(),
      retry: jest.fn(),
      reloadCatalog: jest.fn(),
    });
    useProcessedResultSave.mockReturnValue({
      canSave: false,
      status: 'idle',
      error: null,
      save: jest.fn(),
      cancel: jest.fn(),
      canCancel: false,
    });
  });

  it('does not open a preview for a LOG asset even if an unsafe status is returned', async () => {
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'clip.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: true,
        transfer_status: 'uploaded',
        verification_status: 'server_hash_recorded',
        preview_status: 'preview_ready',
        review_status: 'not_reviewed',
        active_processed_result: {
          result_id: 'a'.repeat(32),
          mime_type: 'video/mp4',
          size_bytes: 10,
          sha256: 'b'.repeat(64),
          created_at: '2026-07-18T00:00:00Z',
          url: `/assets/42/results/${'a'.repeat(32)}`,
        },
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });
    const onPreview = jest.fn();
    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={onPreview}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    await fireEvent.press(view.getByText('Open preview'));

    expect(onPreview).not.toHaveBeenCalled();
    expect(view.getByText('Legacy LOG hint (audit only): yes')).toBeTruthy();
    expect(view.queryByText('Save processed video')).toBeNull();
    expect(view.queryByText('Render rendition')).toBeNull();
  });

  it('shows loading and safe error states while wiring back and retry commands', async () => {
    const onBack = jest.fn();
    const loadAsset = jest.fn();
    useAssetDetail.mockReturnValue({
      asset: null,
      status: 'loading',
      error: null,
      loadAsset,
    });
    const loadingView = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={onBack}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );
    expect(loadingView.getByText('Loading asset...')).toBeTruthy();
    await fireEvent.press(loadingView.getByText('Back to assets'));
    expect(onBack).toHaveBeenCalledTimes(1);
    await loadingView.unmount();

    useAssetDetail.mockReturnValue({
      asset: null,
      status: 'error',
      error: { message: 'Asset unavailable' },
      loadAsset,
    });
    const errorView = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );
    expect(errorView.getByText('Asset unavailable')).toBeTruthy();
    await fireEvent.press(errorView.getByText('Refresh'));
    expect(loadAsset).toHaveBeenCalledTimes(1);
  });

  it('shows mapping_unavailable from route state without changing upload success', async () => {
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'clip.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: false,
        transfer_status: 'uploaded',
        verification_status: 'server_hash_recorded',
        preview_status: 'preview_generating',
        review_status: 'not_reviewed',
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });
    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        mappingUnavailable
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    expect(view.getByText('Local asset mapping is unavailable for this upload.')).toBeTruthy();
    expect(view.getByText('uploaded')).toBeTruthy();
  });

  it('shows a save action only for a validated active processed result', async () => {
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'clip.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: false,
        transfer_status: 'uploaded',
        verification_status: 'file_verified',
        preview_status: 'preview_ready',
        review_status: 'not_reviewed',
        active_processed_result: {
          result_id: 'a'.repeat(32),
          mime_type: 'video/mp4',
          size_bytes: 10,
          sha256: 'b'.repeat(64),
          url: `/assets/42/results/${'a'.repeat(32)}`,
        },
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });
    const save = jest.fn();
    useProcessedResultSave.mockReturnValue({
      canSave: true,
      status: 'idle',
      error: null,
      save,
      cancel: jest.fn(),
      canCancel: false,
    });

    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    await fireEvent.press(view.getByText('Save processed video'));

    expect(save).toHaveBeenCalledTimes(1);
  });

  it('uses a ready formal result for Apple Log preview and save actions', async () => {
    const formalResult = {
      result_id: 'c'.repeat(32),
      mime_type: 'video/mp4',
      size_bytes: 11,
      sha256: 'd'.repeat(64),
      url: `/assets/42/results/${'c'.repeat(32)}`,
    };
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'apple-log.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: true,
        transfer_status: 'uploaded',
        verification_status: 'file_verified',
        preview_status: 'preview_ready',
        review_status: 'not_reviewed',
        active_processed_result: {
          ...formalResult,
          result_id: 'e'.repeat(32),
        },
        formal_preview: {
          state: 'ready',
          detection_status: 'apple_log',
          source_profile: 'apple-log-1',
          color_transform_status: 'unavailable',
          result: formalResult,
        },
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });
    useProcessedResultSave.mockReturnValue({
      canSave: true,
      status: 'idle',
      error: null,
      save: jest.fn(),
      cancel: jest.fn(),
      canCancel: false,
    });
    const onPreview = jest.fn();

    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={onPreview}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    await fireEvent.press(view.getByText('Open preview'));
    expect(onPreview).toHaveBeenCalledWith(42);
    expect(view.getByText('Apple Log 1 (unconverted)')).toBeTruthy();
    expect(view.getByText('Save processed video')).toBeTruthy();
    expect(useProcessedResultSave).toHaveBeenLastCalledWith(
      expect.objectContaining({ result: formalResult }),
    );
  });

  it('shows the exact Apple Log 2 unconverted profile label', async () => {
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'clip.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: true,
        transfer_status: 'uploaded',
        verification_status: 'file_verified',
        preview_status: 'preview_ready',
        review_status: 'not_reviewed',
        formal_preview: {
          state: 'ready',
          detection_status: 'apple_log',
          source_profile: 'apple-log-2',
          color_transform_status: 'unavailable',
          result: null,
        },
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });

    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    expect(view.getByText('Apple Log 2 (unconverted)')).toBeTruthy();
    expect(view.queryByText('Apple Log (unconverted)')).toBeNull();
    expect(view.queryByText('Color transform applied')).toBeNull();
  });

  it('renders the server managed preset control for an eligible video', async () => {
    const submit = jest.fn();
    useManagedRendition.mockReturnValue({
      eligible: true,
      catalogStatus: 'ready',
      capabilities: {
        features: { formalAppleLogPreview: true },
      },
      presets: [{
        presetId: 'identity-v1',
        displayName: 'Identity test',
        presetKind: 'generated-identity',
        version: '1',
        targetColorSpace: null,
      }],
      selectedPresetId: 'identity-v1',
      submitStatus: 'idle',
      rendition: null,
      error: null,
      selectPreset: jest.fn(),
      submit,
      retry: jest.fn(),
      reloadCatalog: jest.fn(),
    });
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'clip.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: false,
        transfer_status: 'uploaded',
        verification_status: 'file_verified',
        preview_status: 'preview_ready',
        review_status: 'not_reviewed',
        active_processed_result: {
          result_id: 'a'.repeat(32),
          mime_type: 'video/mp4',
          size_bytes: 10,
          sha256: 'b'.repeat(64),
          url: `/assets/42/results/${'a'.repeat(32)}`,
        },
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });

    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    expect(view.getByText('Identity test')).toBeTruthy();
    await fireEvent.press(view.getByText('Render rendition'));
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      'safe_to_delete_candidate',
      'Ready for explicit iPhone deletion',
    ],
    [
      'not_candidate',
      'Not ready for iPhone deletion',
    ],
    [
      'future_value',
      'Not ready for iPhone deletion',
    ],
  ])('shows candidate state %s with matching accessibility text', async (
    deleteCandidateStatus,
    expectedText,
  ) => {
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'clip.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: false,
        transfer_status: 'uploaded',
        verification_status: 'file_verified',
        preview_status: 'preview_ready',
        review_status: 'preview_confirmed',
        delete_candidate_status: deleteCandidateStatus,
        formal_preview: { state: 'ready', result: null },
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });

    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    expect(view.getByText(expectedText).props.accessibilityLabel).toBe(expectedText);
  });

  it('refreshes asset, deletion capability, and managed catalog together', async () => {
    const loadAsset = jest.fn().mockResolvedValue(null);
    const refreshCapabilities = jest.fn().mockResolvedValue(null);
    const reloadCatalog = jest.fn().mockResolvedValue(null);
    useAssetDetail.mockReturnValue({
      asset: null,
      status: 'error',
      error: null,
      loadAsset,
    });
    useDeletionCapability.mockReturnValue({
      capabilities: null,
      status: 'error',
      error: null,
      refreshCapabilities,
    });
    useManagedRendition.mockReturnValue({
      eligible: false,
      catalogStatus: 'idle',
      capabilities: null,
      presets: [],
      selectedPresetId: null,
      submitStatus: 'idle',
      rendition: null,
      error: null,
      selectPreset: jest.fn(),
      submit: jest.fn(),
      retry: jest.fn(),
      reloadCatalog,
    });
    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    await fireEvent.press(view.getByText('Refresh'));

    expect(loadAsset).toHaveBeenCalledTimes(1);
    expect(refreshCapabilities).toHaveBeenCalledTimes(1);
    expect(reloadCatalog).toHaveBeenCalledTimes(1);
  });

  it('keeps candidate readiness distinct from terminal local deletion', async () => {
    useAssetDetail.mockReturnValue({
      asset: {
        id: 42,
        type: 'video',
        filename: 'clip.mov',
        size_bytes: 10,
        server_sha256: 'hash',
        taken_at: null,
        is_log: false,
        transfer_status: 'uploaded',
        verification_status: 'file_verified',
        preview_status: 'preview_ready',
        review_status: 'preview_confirmed',
        delete_candidate_status: 'safe_to_delete_candidate',
        formal_preview: { state: 'ready', result: null },
      },
      status: 'ready',
      error: null,
      loadAsset: jest.fn(),
    });
    useOriginalDeletion.mockReturnValue({
      canDelete: false,
      status: 'deleted',
      error: null,
      requestDeletion: jest.fn(),
    });

    const view = await render(
      <AssetDetailScreen
        assetId={42}
        canUseApi
        onBack={jest.fn()}
        onPreview={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    expect(view.getByText('Ready for explicit iPhone deletion')).toBeTruthy();
    expect(view.getByText('iPhone original deleted.')).toBeTruthy();
    expect(view.queryByText('Delete iPhone original')).toBeNull();
  });
});
