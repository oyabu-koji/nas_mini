import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { useDeletionCapability } from './useDeletionCapability';

jest.mock('../../../shared/api/capabilitiesApi', () => ({
  getMediaVaultCapabilities: jest.fn(),
}));

const api = require('../../../shared/api/capabilitiesApi');
const settings = { backendUrl: 'http://mediavault', apiToken: 'secret-token' };
const capabilities = {
  features: {
    formalAppleLogPreview: true,
    safeDeleteCandidate: true,
  },
};

function Harness({ canUseApi = true }) {
  global.latestDeletionCapability = useDeletionCapability({ settings, canUseApi });
  return null;
}

describe('useDeletionCapability', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    api.getMediaVaultCapabilities.mockResolvedValue(capabilities);
  });

  afterEach(() => {
    delete global.latestDeletionCapability;
  });

  it('loads independently on mount and returns the refreshed value', async () => {
    await render(<Harness />);

    await waitFor(() => {
      expect(global.latestDeletionCapability.status).toBe('ready');
    });
    expect(global.latestDeletionCapability.capabilities).toBe(capabilities);
    expect(api.getMediaVaultCapabilities).toHaveBeenCalledWith(settings);
  });

  it('clears the last known capability when refresh fails', async () => {
    await render(<Harness />);
    await waitFor(() => {
      expect(global.latestDeletionCapability.capabilities).toBe(capabilities);
    });
    api.getMediaVaultCapabilities.mockRejectedValue(new Error('offline'));

    let result;
    await act(async () => {
      result = await global.latestDeletionCapability.refreshCapabilities();
    });

    expect(result).toBeNull();
    expect(global.latestDeletionCapability.capabilities).toBeNull();
    expect(global.latestDeletionCapability.status).toBe('error');
  });

  it('does not fetch when the API is unavailable', async () => {
    await render(<Harness canUseApi={false} />);

    await waitFor(() => {
      expect(global.latestDeletionCapability.status).toBe('idle');
    });
    expect(global.latestDeletionCapability.capabilities).toBeNull();
    expect(api.getMediaVaultCapabilities).not.toHaveBeenCalled();
  });
});
