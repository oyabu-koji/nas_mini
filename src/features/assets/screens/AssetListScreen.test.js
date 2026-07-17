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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
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
        settings={{ backendUrl: 'http://backend.test', apiToken: 'masked' }}
      />,
    );

    const acknowledge = await waitFor(() => view.getByText('I reviewed the asset list'));
    fireEvent.press(acknowledge);

    expect(AsyncStorage.removeItem).not.toHaveBeenCalled();
  });
});
