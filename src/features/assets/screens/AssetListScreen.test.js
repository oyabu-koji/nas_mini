import AsyncStorage from '@react-native-async-storage/async-storage';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

import { AssetListScreen } from './AssetListScreen';

jest.mock('../../../shared/api/mediaVaultApi', () => ({
  listAssets: jest.fn(),
}));

const { listAssets } = require('../../../shared/api/mediaVaultApi');

describe('AssetListScreen result unknown acknowledgement', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('starts the asset list request only after pending state is loaded and enables acknowledgement after success', async () => {
    let resolvePending;
    AsyncStorage.getItem.mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePending = resolve;
      }),
    );
    listAssets.mockResolvedValue({ items: [] });
    const onPendingAcknowledged = jest.fn();
    const view = await render(
      <AssetListScreen
        canUseApi
        onOpenSettings={jest.fn()}
        onPendingAcknowledged={onPendingAcknowledged}
        onSelectAsset={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    expect(listAssets).not.toHaveBeenCalled();
    await act(async () => {
      resolvePending('{"kind":"global_pending"}');
    });
    await waitFor(() => expect(listAssets).toHaveBeenCalledTimes(1));
    const acknowledge = view.getByText('I reviewed the asset list');

    await act(async () => {
      fireEvent.press(acknowledge);
    });

    expect(AsyncStorage.removeItem).toHaveBeenCalledWith('mediavault.uploadResultUnknown');
    expect(onPendingAcknowledged).toHaveBeenCalledTimes(1);
  });

  it('does not allow acknowledgement when the post-pending list request fails', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce('{"kind":"global_pending"}');
    listAssets.mockRejectedValue(new Error('unavailable'));
    const view = await render(
      <AssetListScreen
        canUseApi
        onOpenSettings={jest.fn()}
        onPendingAcknowledged={jest.fn()}
        onSelectAsset={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    const acknowledge = await waitFor(() => view.getByText('I reviewed the asset list'));
    fireEvent.press(acknowledge);

    expect(AsyncStorage.removeItem).not.toHaveBeenCalled();
  });

  it('routes API-disabled users to Settings without calling the backend', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce(null);
    const onOpenSettings = jest.fn();
    const view = await render(
      <AssetListScreen
        canUseApi={false}
        onOpenSettings={onOpenSettings}
        onPendingAcknowledged={jest.fn()}
        onSelectAsset={jest.fn()}
        settings={{ backendUrl: '', apiToken: '' }}
      />,
    );

    expect(view.getByText('Backend URL and API token are required before loading assets.')).toBeTruthy();
    await fireEvent.press(view.getByText('Open settings'));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(listAssets).not.toHaveBeenCalled();
  });

  it('renders refreshed assets and forwards selection', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce(null);
    listAssets.mockResolvedValueOnce({
      items: [{
        id: 42,
        filename: 'clip.mov',
        type: 'video',
        size_bytes: 1024,
        created_at: '2026-07-22T00:00:00Z',
        preview_status: 'preview_ready',
        review_status: 'not_reviewed',
      }],
    });
    const onSelectAsset = jest.fn();
    const view = await render(
      <AssetListScreen
        canUseApi
        onOpenSettings={jest.fn()}
        onPendingAcknowledged={jest.fn()}
        onSelectAsset={onSelectAsset}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    await waitFor(() => expect(view.getByText('clip.mov')).toBeTruthy());
    await fireEvent.press(view.getByText('clip.mov'));
    expect(onSelectAsset).toHaveBeenCalledWith(42);
  });

  it('shows a safe list error then retries successfully', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce(null);
    listAssets
      .mockRejectedValueOnce(new Error('private adapter detail'))
      .mockResolvedValueOnce({ items: [] });
    const view = await render(
      <AssetListScreen
        canUseApi
        onOpenSettings={jest.fn()}
        onPendingAcknowledged={jest.fn()}
        onSelectAsset={jest.fn()}
        settings={{ backendUrl: 'http://mediavault', apiToken: 'masked' }}
      />,
    );

    await waitFor(() => expect(view.getByText('Something went wrong.')).toBeTruthy());
    await fireEvent.press(view.getByText('Refresh'));
    await waitFor(() => expect(listAssets).toHaveBeenCalledTimes(2));
    expect(view.getByText('No assets found.')).toBeTruthy();
  });
});
