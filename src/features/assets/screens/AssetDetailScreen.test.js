import { fireEvent, render } from '@testing-library/react-native';

import { AssetDetailScreen } from './AssetDetailScreen';

jest.mock('../hooks/useAssets', () => ({
  useAssetDetail: jest.fn(),
}));

const { useAssetDetail } = require('../hooks/useAssets');

describe('AssetDetailScreen LOG safety gate', () => {
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

    fireEvent.press(view.getByText('Open preview'));

    expect(onPreview).not.toHaveBeenCalled();
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
});
