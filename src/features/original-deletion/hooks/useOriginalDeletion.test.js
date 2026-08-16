import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';
import { Alert } from 'react-native';

import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';
import { useOriginalDeletion } from './useOriginalDeletion';

jest.mock('../../../shared/services/localAssetMappingStore', () => ({
  getLocalAssetMappingState: jest.fn(),
}));
jest.mock('../services/originalDeletionMediaLibraryService', () => ({
  deleteOriginalAsset: jest.fn(),
}));
jest.mock('../services/originalDeletionStore', () => ({
  readOriginalDeletionOutcome: jest.fn(),
  writeOriginalDeletionOutcome: jest.fn(),
}));

const mapping = require('../../../shared/services/localAssetMappingStore');
const media = require('../services/originalDeletionMediaLibraryService');
const store = require('../services/originalDeletionStore');

const readyAsset = {
  id: 42,
  filename: 'clip.mov',
  type: 'video',
  verification_status: 'file_verified',
  preview_status: 'preview_ready',
  review_status: 'preview_confirmed',
  delete_candidate_status: 'safe_to_delete_candidate',
  formal_preview: { state: 'ready' },
};
const validCapabilities = {
  features: {
    formalAppleLogPreview: true,
    safeDeleteCandidate: true,
  },
};

const refreshAsset = jest.fn();
const refreshCapabilities = jest.fn();

function Harness({ asset = readyAsset, capabilities = validCapabilities }) {
  global.latestOriginalDeletion = useOriginalDeletion({
    asset,
    capabilities,
    refreshAsset,
    refreshCapabilities,
  });
  return null;
}

describe('useOriginalDeletion', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mapping.getLocalAssetMappingState.mockResolvedValue({
      status: 'available',
      mapping: {
        backendAssetId: 42,
        localAssetId: 'local-video-42',
      },
    });
    refreshAsset.mockResolvedValue(readyAsset);
    refreshCapabilities.mockResolvedValue(validCapabilities);
    store.readOriginalDeletionOutcome.mockResolvedValue(null);
    store.writeOriginalDeletionOutcome.mockImplementation(async (value) => ({
      ...value,
      updatedAt: '2026-07-24T00:00:00Z',
    }));
    media.deleteOriginalAsset.mockResolvedValue({ status: 'deleted' });
    jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.restoreAllMocks();
    delete global.latestOriginalDeletion;
  });

  it('requires formal ready, confirmation, and a local mapping', async () => {
    const view = await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await view.rerender(
      <Harness asset={{ ...readyAsset, review_status: 'not_reviewed' }} />,
    );
    expect(global.latestOriginalDeletion.canDelete).toBe(false);

    await view.rerender(
      <Harness asset={{ ...readyAsset, formal_preview: { state: 'failed' } }} />,
    );
    expect(global.latestOriginalDeletion.canDelete).toBe(false);
  });

  it('requires a verified and compatible Phase 2B capability', async () => {
    const view = await render(<Harness capabilities={null} />);
    await waitFor(() => expect(global.latestOriginalDeletion.status).toBe('idle'));
    expect(global.latestOriginalDeletion.canDelete).toBe(false);

    await view.rerender(
      <Harness capabilities={{
        features: {
          formalAppleLogPreview: false,
          safeDeleteCandidate: true,
        },
      }} />,
    );
    expect(global.latestOriginalDeletion.canDelete).toBe(false);

    await view.rerender(<Harness capabilities={validCapabilities} />);
    expect(global.latestOriginalDeletion.canDelete).toBe(true);
  });

  it('requires explicit confirmation before invoking local deletion', async () => {
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });
    const [title, message, buttons] = Alert.alert.mock.calls[0];
    expect(title).toBe('Delete iPhone original?');
    expect(message).toContain('clip.mov');
    expect(message).toContain('Backend originals and processed videos are kept');
    expect(buttons.map((button) => button.text)).toEqual(['Cancel', 'Delete']);
    expect(media.deleteOriginalAsset).not.toHaveBeenCalled();
    expect(store.writeOriginalDeletionOutcome).not.toHaveBeenCalled();
  });

  it('deletes only after confirmation and persists the terminal success', async () => {
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });
    const buttons = Alert.alert.mock.calls[0][2];
    await act(async () => {
      await buttons[1].onPress();
    });

    expect(media.deleteOriginalAsset).toHaveBeenCalledWith({
      localAssetId: 'local-video-42',
    });
    expect(store.writeOriginalDeletionOutcome).toHaveBeenCalledWith({
      backendAssetId: 42,
      status: 'deleted',
    });
    expect(global.latestOriginalDeletion.status).toBe('deleted');
    expect(global.latestOriginalDeletion.canDelete).toBe(false);
  });

  it('persists only the stable failure code and keeps deletion retryable', async () => {
    media.deleteOriginalAsset.mockRejectedValue(createAppError(
      'original_delete_permission_denied',
      messageForErrorCode('original_delete_permission_denied'),
    ));
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });
    const buttons = Alert.alert.mock.calls[0][2];
    await act(async () => {
      await buttons[1].onPress();
    });

    expect(store.writeOriginalDeletionOutcome).toHaveBeenCalledWith({
      backendAssetId: 42,
      status: 'failed',
      errorCode: 'original_delete_permission_denied',
    });
    expect(global.latestOriginalDeletion.status).toBe('failed');
    expect(global.latestOriginalDeletion.error).toMatchObject({
      code: 'original_delete_permission_denied',
    });
    expect(global.latestOriginalDeletion.error.message).toBe(
      'Photo library permission is required to delete the iPhone original.',
    );
    expect(global.latestOriginalDeletion.canDelete).toBe(true);
  });

  it('keeps native deletion terminal when success persistence fails', async () => {
    store.writeOriginalDeletionOutcome.mockRejectedValue(createAppError(
      'original_delete_state_unavailable',
      messageForErrorCode('original_delete_state_unavailable'),
    ));
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });
    const buttons = Alert.alert.mock.calls[0][2];
    await act(async () => {
      await buttons[1].onPress();
    });

    expect(media.deleteOriginalAsset).toHaveBeenCalledTimes(1);
    expect(store.writeOriginalDeletionOutcome).toHaveBeenCalledTimes(1);
    expect(global.latestOriginalDeletion.status).toBe('deleted');
    expect(global.latestOriginalDeletion.canDelete).toBe(false);
    expect(global.latestOriginalDeletion.error).toMatchObject({
      code: 'original_delete_state_unavailable',
    });
  });

  it.each([
    ['asset refresh failure', null, validCapabilities],
    ['capability refresh failure', readyAsset, null],
    [
      'candidate demotion',
      { ...readyAsset, delete_candidate_status: 'not_candidate' },
      validCapabilities,
    ],
    [
      'capability disablement',
      readyAsset,
      {
        features: {
          formalAppleLogPreview: true,
          safeDeleteCandidate: false,
        },
      },
    ],
  ])('stops before confirmation after %s', async (_name, nextAsset, nextCapabilities) => {
    refreshAsset.mockResolvedValue(nextAsset);
    refreshCapabilities.mockResolvedValue(nextCapabilities);
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });

    expect(Alert.alert).toHaveBeenCalledWith(
      'Deletion no longer available',
      expect.any(String),
    );
    expect(media.deleteOriginalAsset).not.toHaveBeenCalled();
  });

  it('stops local deletion when the versioned asset refresh is rejected with 409', async () => {
    refreshAsset.mockRejectedValue(createAppError(
      'incompatible_client',
      messageForErrorCode('incompatible_client'),
    ));
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });

    expect(Alert.alert).toHaveBeenCalledWith(
      'Deletion no longer available',
      expect.any(String),
    );
    expect(media.deleteOriginalAsset).not.toHaveBeenCalled();
  });

  it('rejects a refreshed asset or local mapping with a different backend identity', async () => {
    refreshAsset.mockResolvedValue({ ...readyAsset, id: 43 });
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });

    expect(Alert.alert).toHaveBeenCalledWith(
      'Deletion no longer available',
      expect.any(String),
    );
    expect(media.deleteOriginalAsset).not.toHaveBeenCalled();
  });

  it('keeps the Phase 1 direct path independent from Phase 2 feature flags', async () => {
    const phase1Asset = {
      ...readyAsset,
      type: 'image',
      verification_status: 'server_hash_recorded',
      delete_candidate_status: 'not_candidate',
      formal_preview: null,
    };
    const phase1Capabilities = {
      features: {
        formalAppleLogPreview: false,
        safeDeleteCandidate: false,
      },
    };
    refreshAsset.mockResolvedValue(phase1Asset);
    refreshCapabilities.mockResolvedValue(phase1Capabilities);
    await render(
      <Harness
        asset={phase1Asset}
        capabilities={phase1Capabilities}
      />,
    );
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });

    expect(Alert.alert.mock.calls[0][0]).toBe('Delete iPhone original?');
  });

  it('keeps the Phase 1 direct path available when capability refresh fails', async () => {
    const phase1Asset = {
      ...readyAsset,
      type: 'image',
      verification_status: 'server_hash_recorded',
      delete_candidate_status: 'not_candidate',
      formal_preview: null,
    };
    refreshAsset.mockResolvedValue(phase1Asset);
    refreshCapabilities.mockRejectedValue(new Error('capability unavailable'));
    await render(<Harness asset={phase1Asset} capabilities={null} />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });

    expect(Alert.alert.mock.calls[0][0]).toBe('Delete iPhone original?');
    expect(media.deleteOriginalAsset).not.toHaveBeenCalled();
  });

  it('rechecks the current asset identity in the destructive action', async () => {
    const view = await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));
    await act(async () => {
      await global.latestOriginalDeletion.requestDeletion();
    });
    const destructiveAction = Alert.alert.mock.calls[0][2][1].onPress;

    await view.rerender(
      <Harness asset={{ ...readyAsset, id: 43 }} />,
    );
    await act(async () => {
      await destructiveAction();
    });

    expect(media.deleteOriginalAsset).not.toHaveBeenCalled();
  });
});
