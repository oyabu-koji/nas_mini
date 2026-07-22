import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { createAppError } from '../../../shared/utils/errors';
import { useSettings } from './useSettings';

jest.mock('../../../shared/api/mediaVaultApi', () => ({
  checkHealth: jest.fn(),
}));
jest.mock('../../../shared/services/settingsStorage', () => ({
  getBackendUrl: jest.fn(),
  saveBackendUrl: jest.fn(),
}));
jest.mock('../../../shared/services/secureTokenStorage', () => ({
  getApiToken: jest.fn(),
  saveApiToken: jest.fn(),
}));

const { checkHealth } = require('../../../shared/api/mediaVaultApi');
const backendStorage = require('../../../shared/services/settingsStorage');
const tokenStorage = require('../../../shared/services/secureTokenStorage');

function HookHarness() {
  global.latestSettingsState = useSettings();
  return null;
}

describe('useSettings', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    backendStorage.getBackendUrl.mockResolvedValue('http://100.64.0.1:8000');
    tokenStorage.getApiToken.mockResolvedValue('saved-token');
    backendStorage.saveBackendUrl.mockImplementation(async (value) => String(value ?? '').trim());
    tokenStorage.saveApiToken.mockImplementation(async (value) => String(value ?? '').trim());
    checkHealth.mockResolvedValue({ status: 'ok' });
  });

  afterEach(() => {
    delete global.latestSettingsState;
  });

  it('loads saved settings and derives API availability', async () => {
    await render(<HookHarness />);

    await waitFor(() => expect(global.latestSettingsState.status).toBe('idle'));
    expect(global.latestSettingsState.settings).toEqual({
      backendUrl: 'http://100.64.0.1:8000',
      apiToken: 'saved-token',
    });
    expect(global.latestSettingsState.hasSavedToken).toBe(true);
    expect(global.latestSettingsState.canUseApi).toBe(true);
  });

  it('does not commit a delayed storage result after unmount', async () => {
    let resolveBackendUrl;
    let resolveApiToken;
    const backendUrlPromise = new Promise((resolve) => {
      resolveBackendUrl = resolve;
    });
    const apiTokenPromise = new Promise((resolve) => {
      resolveApiToken = resolve;
    });
    backendStorage.getBackendUrl.mockReturnValue(backendUrlPromise);
    tokenStorage.getApiToken.mockReturnValue(apiTokenPromise);
    const view = await render(<HookHarness />);
    const stateAtUnmount = global.latestSettingsState;

    await view.unmount();
    await act(async () => {
      resolveBackendUrl('http://late.test');
      resolveApiToken('late-token');
      await Promise.all([backendUrlPromise, apiTokenPromise]);
    });

    expect(stateAtUnmount.status).toBe('loading');
    expect(stateAtUnmount.settings).toEqual({ backendUrl: '', apiToken: '' });
  });

  it('trims and saves a URL with a replacement token', async () => {
    await render(<HookHarness />);
    await waitFor(() => expect(global.latestSettingsState.status).toBe('idle'));

    await act(async () => {
      global.latestSettingsState.setBackendUrl('  http://100.64.0.2:8000  ');
      global.latestSettingsState.setApiTokenInput('  replacement-token  ');
    });
    await act(async () => {
      await global.latestSettingsState.saveSettings();
    });

    expect(backendStorage.saveBackendUrl).toHaveBeenLastCalledWith('  http://100.64.0.2:8000  ');
    expect(tokenStorage.saveApiToken).toHaveBeenCalledWith('  replacement-token  ');
    expect(global.latestSettingsState.settings).toEqual({
      backendUrl: 'http://100.64.0.2:8000',
      apiToken: 'replacement-token',
    });
    expect(global.latestSettingsState.apiTokenInput).toBe('');
    expect(global.latestSettingsState.message).toBe('Settings saved.');
  });

  it('keeps the saved token when the replacement input is blank and rejects missing settings', async () => {
    await render(<HookHarness />);
    await waitFor(() => expect(global.latestSettingsState.status).toBe('idle'));

    await act(async () => {
      await global.latestSettingsState.saveSettings();
    });
    expect(tokenStorage.saveApiToken).not.toHaveBeenCalled();

    backendStorage.saveBackendUrl.mockResolvedValueOnce('');
    await act(async () => {
      global.latestSettingsState.setBackendUrl('');
    });
    await act(async () => {
      await global.latestSettingsState.saveSettings();
    });
    expect(global.latestSettingsState.status).toBe('error');
    expect(global.latestSettingsState.message).toBe('Backend URL and API token are required.');
  });

  it('reports storage load failures and health success or failure without leaking adapter errors', async () => {
    backendStorage.getBackendUrl.mockRejectedValueOnce(new Error('private storage detail'));
    await render(<HookHarness />);
    await waitFor(() => expect(global.latestSettingsState.status).toBe('error'));
    expect(global.latestSettingsState.message).toBe('Something went wrong.');

    await act(async () => {
      await global.latestSettingsState.runConnectionCheck();
    });
    expect(checkHealth).toHaveBeenCalledWith({ backendUrl: '', apiToken: '' });
    expect(global.latestSettingsState.message).toBe('Backend is reachable.');

    checkHealth.mockRejectedValueOnce(createAppError('network_unreachable', 'Backend unavailable'));
    await act(async () => {
      await global.latestSettingsState.runConnectionCheck();
    });
    expect(global.latestSettingsState.status).toBe('error');
    expect(global.latestSettingsState.message).toBe('Backend unavailable');
  });
});
