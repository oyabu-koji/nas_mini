import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { createAppError } from '../../../shared/utils/errors';
import { useAssetDetail, useAssetList } from './useAssets';

jest.mock('../../../shared/api/mediaVaultApi', () => ({
  getAsset: jest.fn(),
  listAssets: jest.fn(),
}));

const { getAsset, listAssets } = require('../../../shared/api/mediaVaultApi');
const settings = { backendUrl: 'http://backend.test', apiToken: 'token' };

function ListHarness({ canUseApi = true }) {
  global.latestAssetList = useAssetList(settings, canUseApi, { autoLoad: false });
  return null;
}

function DetailHarness({ assetId = 42, autoPoll = true }) {
  global.latestAssetDetail = useAssetDetail(settings, true, assetId, { autoPoll });
  return null;
}

describe('useAssetList', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    delete global.latestAssetList;
  });

  it('keeps API-disabled state empty without making a request', async () => {
    await render(<ListHarness canUseApi={false} />);
    let result;

    await act(async () => {
      result = await global.latestAssetList.refreshAssets();
    });

    expect(result).toEqual({ success: false, isLatest: true });
    expect(global.latestAssetList).toMatchObject({ items: [], status: 'idle', error: null });
    expect(listAssets).not.toHaveBeenCalled();
  });

  it('publishes successful items and a safe request error', async () => {
    listAssets.mockResolvedValueOnce({ items: [{ id: 1, filename: 'clip.mov' }] });
    await render(<ListHarness />);

    await act(async () => {
      await global.latestAssetList.refreshAssets();
    });
    expect(global.latestAssetList.status).toBe('ready');
    expect(global.latestAssetList.items).toEqual([{ id: 1, filename: 'clip.mov' }]);

    listAssets.mockRejectedValueOnce(createAppError('network_unreachable', 'Backend unavailable'));
    await act(async () => {
      await global.latestAssetList.refreshAssets();
    });
    expect(global.latestAssetList.status).toBe('error');
    expect(global.latestAssetList.error).toMatchObject({ code: 'network_unreachable' });
  });

  it('ignores a stale response when a newer refresh finishes first', async () => {
    let resolveFirst;
    let resolveSecond;
    listAssets
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveFirst = resolve;
      }))
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveSecond = resolve;
      }));
    await render(<ListHarness />);
    let firstRequest;
    let secondRequest;

    await act(async () => {
      firstRequest = global.latestAssetList.refreshAssets();
      secondRequest = global.latestAssetList.refreshAssets();
      await Promise.resolve();
    });
    await act(async () => {
      resolveSecond({ items: [{ id: 2 }] });
      await secondRequest;
    });
    await act(async () => {
      resolveFirst({ items: [{ id: 1 }] });
      await firstRequest;
    });

    expect(await firstRequest).toEqual({ success: true, isLatest: false });
    expect(global.latestAssetList.items).toEqual([{ id: 2 }]);
  });
});

describe('useAssetDetail', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    jest.useRealTimers();
    delete global.latestAssetDetail;
  });

  it('does not request a missing asset ID', async () => {
    await render(<DetailHarness assetId={null} />);
    await act(async () => Promise.resolve());

    expect(getAsset).not.toHaveBeenCalled();
    expect(global.latestAssetDetail).toMatchObject({ asset: null, status: 'idle', error: null });
  });

  it('loads an asset and exposes a safe error on refresh failure', async () => {
    const asset = { id: 42, preview_status: 'preview_ready' };
    getAsset.mockResolvedValueOnce(asset);
    await render(<DetailHarness autoPoll={false} />);
    await waitFor(() => expect(global.latestAssetDetail.status).toBe('ready'));
    expect(global.latestAssetDetail.asset).toBe(asset);

    getAsset.mockRejectedValueOnce(createAppError('not_found', 'Asset not found'));
    await act(async () => {
      await global.latestAssetDetail.loadAsset();
    });
    expect(global.latestAssetDetail.status).toBe('error');
    expect(global.latestAssetDetail.error).toMatchObject({ code: 'not_found' });
  });

  it('polls generating previews every two seconds and clears the timer on unmount', async () => {
    jest.useFakeTimers();
    const generating = { id: 42, preview_status: 'preview_generating' };
    const ready = { id: 42, preview_status: 'preview_ready' };
    getAsset.mockResolvedValueOnce(generating).mockResolvedValueOnce(ready);
    const view = await render(<DetailHarness />);
    await waitFor(() => expect(global.latestAssetDetail.asset).toBe(generating));

    await act(async () => {
      await jest.advanceTimersByTimeAsync(2000);
    });
    await waitFor(() => expect(global.latestAssetDetail.asset).toBe(ready));
    expect(getAsset).toHaveBeenCalledTimes(2);

    await view.unmount();
    await jest.advanceTimersByTimeAsync(4000);
    expect(getAsset).toHaveBeenCalledTimes(2);
  });
});
