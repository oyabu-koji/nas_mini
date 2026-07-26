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
  review_status: 'preview_confirmed',
  formal_preview: { state: 'ready' },
};
const validCapabilities = {
  features: { formalAppleLogPreview: true },
};

function Harness({ asset = readyAsset, capabilities = validCapabilities }) {
  global.latestOriginalDeletion = useOriginalDeletion({ asset, capabilities });
  return null;
}

describe('useOriginalDeletion', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mapping.getLocalAssetMappingState.mockResolvedValue({
      status: 'available',
      mapping: { localAssetId: 'local-video-42' },
    });
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
      <Harness capabilities={{ features: { formalAppleLogPreview: false } }} />,
    );
    expect(global.latestOriginalDeletion.canDelete).toBe(false);

    await view.rerender(<Harness capabilities={validCapabilities} />);
    expect(global.latestOriginalDeletion.canDelete).toBe(true);
  });

  it('requires explicit confirmation before invoking local deletion', async () => {
    await render(<Harness />);
    await waitFor(() => expect(global.latestOriginalDeletion.canDelete).toBe(true));

    global.latestOriginalDeletion.requestDeletion();
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

    global.latestOriginalDeletion.requestDeletion();
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

    global.latestOriginalDeletion.requestDeletion();
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

    global.latestOriginalDeletion.requestDeletion();
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
});
