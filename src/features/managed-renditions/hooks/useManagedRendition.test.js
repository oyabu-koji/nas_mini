import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { createAppError } from '../../../shared/utils/errors';
import { useManagedRendition } from './useManagedRendition';

jest.mock('../services/managedRenditionApi', () => ({
  createManagedRendition: jest.fn(),
  getManagedCapabilities: jest.fn(),
  getManagedRendition: jest.fn(),
  listManagedPresets: jest.fn(),
}));
jest.mock('../services/managedRenditionStore', () => ({
  generateClientRenditionRequestId: jest.fn(),
  readManagedRenditionRecord: jest.fn(),
  updateManagedRenditionRecord: jest.fn(),
  writePendingManagedRendition: jest.fn(),
}));

const api = require('../services/managedRenditionApi');
const store = require('../services/managedRenditionStore');

const settings = { backendUrl: 'http://backend.test', apiToken: 'secret-token' };
const asset = {
  id: 42,
  type: 'video',
  verification_status: 'file_verified',
  preview_status: 'preview_ready',
  is_log: false,
  active_processed_result: { result_id: 'f'.repeat(32) },
};
const presets = [{
  presetId: 'compress-only',
  displayName: 'Compress only',
  presetKind: 'compress-only',
  version: '1',
  targetColorSpace: null,
  sourceReference: 'MediaVault built-in',
  termsReference: 'Project source',
}, {
  presetId: 'identity-v1',
  displayName: 'Identity test',
  presetKind: 'generated-identity',
  version: '1',
  targetColorSpace: null,
  sourceReference: 'Generator',
  termsReference: 'Project source',
}];

function rendition({
  clientRequestId = 'a'.repeat(32),
  renditionId = 'b'.repeat(32),
  requestedPresetId = 'compress-only',
  state = 'queued',
  resultId = null,
  appliedPresetId = null,
  errorCode = null,
} = {}) {
  return {
    renditionId,
    assetId: 42,
    clientRequestId,
    selectionGeneration: 1,
    requestedPresetId,
    appliedPresetId,
    state,
    colorTransformStatus: state === 'ready' ? 'not_requested' : null,
    errorCode,
    resultId,
    createdAt: '2026-07-21T00:00:00Z',
    updatedAt: '2026-07-21T00:00:00Z',
  };
}

function record({
  clientRequestId = 'a'.repeat(32),
  requestedPresetId = 'compress-only',
  selectionSequence = 1,
  currentRendition = null,
} = {}) {
  return {
    assetId: 42,
    clientRequestId,
    requestedPresetId,
    renditionId: currentRendition?.renditionId ?? null,
    selectionSequence,
    rendition: currentRendition,
    createdAt: '2026-07-21T00:00:00Z',
    updatedAt: '2026-07-21T00:00:00Z',
  };
}

const defaultLoadAsset = jest.fn();

function Harness({ currentAsset = asset, loadAsset = defaultLoadAsset }) {
  global.latestManagedRendition = useManagedRendition({
    settings,
    canUseApi: true,
    asset: currentAsset,
    loadAsset,
    pollIntervalMs: 10,
  });
  return null;
}

describe('useManagedRendition', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    api.getManagedCapabilities.mockResolvedValue({ features: { managedPreviewPresets: true } });
    api.listManagedPresets.mockResolvedValue(presets);
    store.readManagedRenditionRecord.mockResolvedValue(null);
    store.generateClientRenditionRequestId.mockReturnValue('a'.repeat(32));
    store.writePendingManagedRendition.mockImplementation(async (value) => record({
      clientRequestId: value.clientRequestId,
      requestedPresetId: value.requestedPresetId,
      selectionSequence: value.selectionSequence,
    }));
    store.updateManagedRenditionRecord.mockImplementation(async (value) => record({
      clientRequestId: value.clientRequestId,
      requestedPresetId: value.rendition.requestedPresetId,
      selectionSequence: value.selectionSequence,
      currentRendition: value.rendition,
    }));
  });

  afterEach(() => {
    delete global.latestManagedRendition;
    jest.useRealTimers();
  });

  it('loads only the server catalog and does not invent a local fallback selection', async () => {
    await render(<Harness />);

    await waitFor(() => expect(global.latestManagedRendition.catalogStatus).toBe('ready'));
    expect(global.latestManagedRendition.presets).toEqual(presets);
    expect(global.latestManagedRendition.selectedPresetId).toBeNull();
  });

  it('persists a new request before POST and reuses it after a timeout', async () => {
    api.createManagedRendition
      .mockRejectedValueOnce(createAppError('timeout', 'timeout', { retryable: true }))
      .mockResolvedValueOnce(rendition());
    await render(<Harness />);
    await waitFor(() => expect(global.latestManagedRendition.catalogStatus).toBe('ready'));
    await act(async () => {
      global.latestManagedRendition.selectPreset('compress-only');
    });
    await waitFor(() => expect(global.latestManagedRendition.selectedPresetId).toBe('compress-only'));

    await act(async () => {
      await global.latestManagedRendition.submit();
    });
    await waitFor(() => expect(global.latestManagedRendition.submitStatus).toBe('retryable_failed'));
    expect(store.writePendingManagedRendition.mock.invocationCallOrder[0]).toBeLessThan(
      api.createManagedRendition.mock.invocationCallOrder[0],
    );

    await act(async () => {
      await global.latestManagedRendition.retry();
    });
    expect(api.createManagedRendition).toHaveBeenCalledTimes(2);
    expect(api.createManagedRendition.mock.calls[0][0].clientRequestId).toBe('a'.repeat(32));
    expect(api.createManagedRendition.mock.calls[1][0].clientRequestId).toBe('a'.repeat(32));
    expect(store.writePendingManagedRendition).toHaveBeenCalledTimes(1);
    expect(global.latestManagedRendition.rendition.renditionId).toBe('b'.repeat(32));
  });

  it('retries rendition_precondition_changed with the same persisted request identity', async () => {
    api.createManagedRendition
      .mockRejectedValueOnce(createAppError(
        'rendition_precondition_changed',
        'changed',
        { retryable: true },
      ))
      .mockResolvedValueOnce(rendition());
    await render(<Harness />);
    await waitFor(() => expect(global.latestManagedRendition.catalogStatus).toBe('ready'));
    await act(async () => {
      global.latestManagedRendition.selectPreset('compress-only');
    });
    await waitFor(() => expect(global.latestManagedRendition.selectedPresetId).toBe('compress-only'));

    await act(async () => {
      await global.latestManagedRendition.submit();
    });
    await act(async () => {
      await global.latestManagedRendition.retry();
    });

    expect(api.createManagedRendition.mock.calls.map(([value]) => value.clientRequestId)).toEqual([
      'a'.repeat(32),
      'a'.repeat(32),
    ]);
  });

  it('restores polling after restart, stops at ready, and confirms the exact active result', async () => {
    const queued = rendition();
    const ready = rendition({
      state: 'ready',
      resultId: 'c'.repeat(32),
      appliedPresetId: 'compress-only',
    });
    store.readManagedRenditionRecord.mockResolvedValue(record({ currentRendition: queued }));
    api.getManagedRendition.mockResolvedValue(ready);
    const loadAsset = jest.fn().mockResolvedValue({
      ...asset,
      active_processed_result: { result_id: ready.resultId },
    });
    await render(<Harness loadAsset={loadAsset} />);
    await waitFor(() => expect(global.latestManagedRendition.rendition?.state).toBe('ready'));
    expect(store.readManagedRenditionRecord).toHaveBeenCalledWith(42);
    expect(loadAsset).toHaveBeenCalledTimes(1);
    expect(global.latestManagedRendition.readyResultConfirmed).toBe(true);
    const pollCount = api.getManagedRendition.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.getManagedRendition).toHaveBeenCalledTimes(pollCount);
  });

  it('replays a response-unknown restored request with the same ID without selecting a missing preset', async () => {
    const pending = record({
      clientRequestId: 'c'.repeat(32),
      requestedPresetId: 'removed-look',
      selectionSequence: 3,
    });
    store.readManagedRenditionRecord.mockResolvedValue(pending);
    api.createManagedRendition.mockResolvedValue(rendition({
      clientRequestId: pending.clientRequestId,
      requestedPresetId: pending.requestedPresetId,
    }));
    await render(<Harness />);

    await waitFor(() => expect(global.latestManagedRendition.submitStatus).toBe('retryable_failed'));
    expect(global.latestManagedRendition.catalogStatus).toBe('ready');
    expect(global.latestManagedRendition.selectedPresetId).toBeNull();
    await act(async () => {
      await global.latestManagedRendition.retry();
    });

    expect(store.generateClientRenditionRequestId).not.toHaveBeenCalled();
    expect(store.writePendingManagedRendition).not.toHaveBeenCalled();
    expect(api.createManagedRendition).toHaveBeenCalledWith(expect.objectContaining({
      clientRequestId: pending.clientRequestId,
      presetId: pending.requestedPresetId,
    }));
  });

  it('ignores delayed storage restore after a newer explicit selection', async () => {
    let resolveRecord;
    store.readManagedRenditionRecord.mockImplementation(() => new Promise((resolve) => {
      resolveRecord = resolve;
    }));
    api.createManagedRendition.mockResolvedValue(rendition({
      requestedPresetId: 'identity-v1',
    }));
    await render(<Harness />);
    await waitFor(() => expect(global.latestManagedRendition.catalogStatus).toBe('ready'));
    await act(async () => {
      global.latestManagedRendition.selectPreset('identity-v1');
    });
    await act(async () => {
      resolveRecord(record({
        requestedPresetId: 'compress-only',
        currentRendition: rendition(),
      }));
      await Promise.resolve();
    });

    expect(global.latestManagedRendition.selectedPresetId).toBe('identity-v1');
    expect(global.latestManagedRendition.rendition).toBeNull();
    await act(async () => {
      await global.latestManagedRendition.submit();
    });
    expect(api.createManagedRendition).toHaveBeenCalledWith(expect.objectContaining({
      presetId: 'identity-v1',
    }));
  });

  it('ignores an older POST completion after a newer explicit selection', async () => {
    let resolveA;
    let resolveB;
    store.generateClientRenditionRequestId
      .mockReturnValueOnce('a'.repeat(32))
      .mockReturnValueOnce('c'.repeat(32));
    api.createManagedRendition
      .mockImplementationOnce(() => new Promise((resolve) => { resolveA = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveB = resolve; }));
    await render(<Harness />);
    await waitFor(() => expect(global.latestManagedRendition.catalogStatus).toBe('ready'));
    await act(async () => {
      global.latestManagedRendition.selectPreset('compress-only');
    });
    await waitFor(() => expect(global.latestManagedRendition.selectedPresetId).toBe('compress-only'));
    let pendingA;
    await act(async () => {
      pendingA = global.latestManagedRendition.submit();
      await Promise.resolve();
    });
    await act(async () => {
      global.latestManagedRendition.selectPreset('identity-v1');
    });
    await waitFor(() => expect(global.latestManagedRendition.selectedPresetId).toBe('identity-v1'));
    let pendingB;
    await act(async () => {
      pendingB = global.latestManagedRendition.submit();
      await Promise.resolve();
    });

    resolveB(rendition({
      clientRequestId: 'c'.repeat(32),
      renditionId: 'd'.repeat(32),
      requestedPresetId: 'identity-v1',
    }));
    await act(async () => pendingB);
    resolveA(rendition());
    await act(async () => pendingA);

    expect(global.latestManagedRendition.rendition.clientRequestId).toBe('c'.repeat(32));
    expect(global.latestManagedRendition.rendition.requestedPresetId).toBe('identity-v1');
  });

  it('does not render or poll ineligible and legacy LOG assets', async () => {
    const view = await render(<Harness currentAsset={{ ...asset, is_log: true }} />);
    await act(async () => Promise.resolve());

    expect(global.latestManagedRendition.eligible).toBe(false);
    expect(api.listManagedPresets).not.toHaveBeenCalled();
    expect(api.createManagedRendition).not.toHaveBeenCalled();
    view.unmount();
  });
});
