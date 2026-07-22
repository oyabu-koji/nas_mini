import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { SettingsScreen } from './SettingsScreen';

function settingsState(overrides = {}) {
  return {
    backendUrl: 'http://100.64.0.1:8000',
    setBackendUrl: jest.fn(),
    apiTokenInput: '',
    setApiTokenInput: jest.fn(),
    hasSavedToken: true,
    status: 'idle',
    message: null,
    saveSettings: jest.fn(),
    runConnectionCheck: jest.fn(),
    ...overrides,
  };
}

describe('SettingsScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows saved-token state and forwards field and command events', async () => {
    const state = settingsState({ message: 'Settings saved.' });
    await render(<SettingsScreen settingsState={state} />);

    expect(screen.getByText('A token is saved in SecureStore.')).toBeTruthy();
    expect(screen.getByPlaceholderText('Saved. Enter a new token to replace it.')).toBeTruthy();
    await fireEvent.changeText(screen.getByPlaceholderText('http://100.x.x.x:8000'), 'http://100.64.0.2:8000');
    await fireEvent.changeText(screen.getByPlaceholderText('Saved. Enter a new token to replace it.'), 'next-token');
    await fireEvent.press(screen.getByRole('button', { name: 'Save' }));
    await fireEvent.press(screen.getByRole('button', { name: 'Check health' }));

    expect(state.setBackendUrl).toHaveBeenCalledWith('http://100.64.0.2:8000');
    expect(state.setApiTokenInput).toHaveBeenCalledWith('next-token');
    expect(state.saveSettings).toHaveBeenCalledTimes(1);
    expect(state.runConnectionCheck).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Settings saved.')).toBeTruthy();
  });

  it('disables both commands and uses progress labels while busy', async () => {
    const state = settingsState({ status: 'saving' });
    await render(<SettingsScreen settingsState={state} />);

    const saveButton = screen.getByRole('button', { name: 'Saving...' });
    const healthButton = screen.getByRole('button', { name: 'Check health' });
    expect(saveButton.props.accessibilityState.disabled).toBe(true);
    expect(healthButton.props.accessibilityState.disabled).toBe(true);
    await fireEvent.press(saveButton);
    await fireEvent.press(healthButton);
    expect(state.saveSettings).not.toHaveBeenCalled();
    expect(state.runConnectionCheck).not.toHaveBeenCalled();
  });

  it('warns for localhost and public HTTP but accepts private or HTTPS endpoints', async () => {
    const view = await render(<SettingsScreen settingsState={settingsState({ backendUrl: 'http://localhost:8000' })} />);
    expect(screen.getByText(/localhost point to the iPhone itself/)).toBeTruthy();

    await view.rerender(<SettingsScreen settingsState={settingsState({ backendUrl: 'http://example.com' })} />);
    expect(screen.getByText('Use HTTP only for LAN or Tailscale private endpoints.')).toBeTruthy();

    for (const backendUrl of ['http://10.0.0.1', 'http://192.168.1.2', 'http://172.31.0.1', 'http://host.ts.net', 'https://example.com']) {
      await view.rerender(<SettingsScreen settingsState={settingsState({ backendUrl })} />);
      expect(screen.queryByText(/Use HTTP only|point to the iPhone/)).toBeNull();
    }
  });
});
