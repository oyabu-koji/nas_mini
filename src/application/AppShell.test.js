import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { AppShell } from './AppShell';

jest.mock('../features/settings/hooks/useSettings', () => ({
  useSettings: jest.fn(),
}));
jest.mock('../features/processed-results/hooks/useProcessedResultSave', () => ({
  useProcessedResultStartupCleanup: jest.fn(),
}));
jest.mock('../features/settings/screens/SettingsScreen', () => ({
  SettingsScreen: () => {
    const ReactModule = require('react');
    const { Text } = require('react-native');
    return ReactModule.createElement(Text, null, 'Settings screen');
  },
}));
jest.mock('../features/asset-picker/screens/AssetPickerScreen', () => ({
  AssetPickerScreen: ({ onOpenAssets, onOpenSettings, onUploaded }) => {
    const ReactModule = require('react');
    const { Text, View } = require('react-native');
    return ReactModule.createElement(
      View,
      null,
      ReactModule.createElement(Text, null, 'Picker screen'),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: () => onUploaded(42) }, 'Upload complete'),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: onOpenAssets }, 'Picker assets'),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: onOpenSettings }, 'Picker settings'),
    );
  },
}));
jest.mock('../features/assets/screens/AssetListScreen', () => ({
  AssetListScreen: ({ onPendingAcknowledged, onSelectAsset }) => {
    const ReactModule = require('react');
    const { Text, View } = require('react-native');
    return ReactModule.createElement(
      View,
      null,
      ReactModule.createElement(Text, null, 'Assets screen'),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: () => onSelectAsset(7) }, 'Select asset'),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: onPendingAcknowledged }, 'Acknowledge pending'),
    );
  },
}));
jest.mock('../features/assets/screens/AssetDetailScreen', () => ({
  AssetDetailScreen: ({ assetId, mappingUnavailable, onBack, onPreview }) => {
    const ReactModule = require('react');
    const { Text, View } = require('react-native');
    return ReactModule.createElement(
      View,
      null,
      ReactModule.createElement(Text, null, `Detail ${assetId}${mappingUnavailable ? ' unavailable' : ''}`),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: () => onPreview(assetId) }, 'Open preview'),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: onBack }, 'Detail back'),
    );
  },
}));
jest.mock('../features/preview-review/screens/PreviewReviewScreen', () => ({
  PreviewReviewScreen: ({ assetId, onBack }) => {
    const ReactModule = require('react');
    const { Text, View } = require('react-native');
    return ReactModule.createElement(
      View,
      null,
      ReactModule.createElement(Text, null, `Preview ${assetId}`),
      ReactModule.createElement(Text, { accessibilityRole: 'button', onPress: onBack }, 'Preview back'),
    );
  },
}));

const { useSettings } = require('../features/settings/hooks/useSettings');
const { useProcessedResultStartupCleanup } = require('../features/processed-results/hooks/useProcessedResultSave');

describe('AppShell', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useSettings.mockReturnValue({
      settings: { backendUrl: 'http://mediavault', apiToken: 'token' },
      canUseApi: true,
    });
  });

  it('starts in Settings and wires startup processed-result cleanup', async () => {
    await render(<AppShell />);

    expect(screen.getByText('Settings screen')).toBeTruthy();
    expect(useSettings).toHaveBeenCalledTimes(1);
    expect(useProcessedResultStartupCleanup).toHaveBeenCalledTimes(1);
  });

  it('navigates through upload, detail, preview, back, and asset list routes', async () => {
    await render(<AppShell />);

    await fireEvent.press(screen.getByRole('button', { name: 'Upload' }));
    expect(screen.getByText('Picker screen')).toBeTruthy();

    await fireEvent.press(screen.getByRole('button', { name: 'Upload complete' }));
    expect(screen.getByText('Detail 42')).toBeTruthy();

    await fireEvent.press(screen.getByRole('button', { name: 'Open preview' }));
    expect(screen.getByText('Preview 42')).toBeTruthy();

    await fireEvent.press(screen.getByRole('button', { name: 'Preview back' }));
    expect(screen.getByText('Detail 42')).toBeTruthy();
    await fireEvent.press(screen.getByRole('button', { name: 'Detail back' }));
    expect(screen.getByText('Assets screen')).toBeTruthy();

    await fireEvent.press(screen.getByRole('button', { name: 'Select asset' }));
    expect(screen.getByText('Detail 7')).toBeTruthy();
  });

  it('supports top navigation and pending acknowledgement return to upload', async () => {
    await render(<AppShell />);

    await fireEvent.press(screen.getByRole('button', { name: 'Assets' }));
    expect(screen.getByText('Assets screen')).toBeTruthy();
    await fireEvent.press(screen.getByRole('button', { name: 'Acknowledge pending' }));
    expect(screen.getByText('Picker screen')).toBeTruthy();

    await fireEvent.press(screen.getByRole('button', { name: 'Picker settings' }));
    expect(screen.getByText('Settings screen')).toBeTruthy();
  });
});
