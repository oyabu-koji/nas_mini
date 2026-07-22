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

const { useAssetDetail } = require('../hooks/useAssets');
const { useProcessedResultSave } = require('../../processed-results/hooks/useProcessedResultSave');
const { useManagedRendition } = require('../../managed-renditions/hooks/useManagedRendition');

describe('AssetDetailScreen LOG safety gate', () => {
  beforeEach(() => {
    useManagedRendition.mockReturnValue({
      eligible: false,
      catalogStatus: 'idle',
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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
      />,
    );

    await fireEvent.press(view.getByText('Open preview'));

    expect(onPreview).not.toHaveBeenCalled();
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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
      />,
    );

    await fireEvent.press(view.getByText('Save processed video'));

    expect(save).toHaveBeenCalledTimes(1);
  });

  it('renders the server managed preset control for an eligible video', async () => {
    const submit = jest.fn();
    useManagedRendition.mockReturnValue({
      eligible: true,
      catalogStatus: 'ready',
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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
      />,
    );

    expect(view.getByText('Identity test')).toBeTruthy();
    await fireEvent.press(view.getByText('Render rendition'));
    expect(submit).toHaveBeenCalledTimes(1);
  });
});
