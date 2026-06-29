import React from 'react';
import { SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';

import { AssetPickerScreen } from '../features/asset-picker/screens/AssetPickerScreen';
import { AssetDetailScreen } from '../features/assets/screens/AssetDetailScreen';
import { AssetListScreen } from '../features/assets/screens/AssetListScreen';
import { PreviewReviewScreen } from '../features/preview-review/screens/PreviewReviewScreen';
import { useSettings } from '../features/settings/hooks/useSettings';
import { SettingsScreen } from '../features/settings/screens/SettingsScreen';

export function AppShell() {
  const settingsState = useSettings();
  const [route, setRoute] = React.useState({ screen: 'settings', selectedAssetId: null });

  const openSettings = React.useCallback(() => setRoute({ screen: 'settings', selectedAssetId: null }), []);
  const openUpload = React.useCallback(() => setRoute({ screen: 'picker', selectedAssetId: null }), []);
  const openAssets = React.useCallback(() => setRoute({ screen: 'assets', selectedAssetId: null }), []);
  const openAssetDetail = React.useCallback((assetId) => setRoute({ screen: 'assetDetail', selectedAssetId: assetId }), []);
  const openPreview = React.useCallback((assetId) => setRoute({ screen: 'previewReview', selectedAssetId: assetId }), []);

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <View style={styles.nav}>
        <NavButton active={route.screen === 'settings'} label="Settings" onPress={openSettings} />
        <NavButton active={route.screen === 'picker'} label="Upload" onPress={openUpload} />
        <NavButton active={route.screen === 'assets'} label="Assets" onPress={openAssets} />
      </View>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {route.screen === 'settings' ? <SettingsScreen settingsState={settingsState} /> : null}
        {route.screen === 'picker' ? (
          <AssetPickerScreen
            canUseApi={settingsState.canUseApi}
            onOpenSettings={openSettings}
            onUploaded={openAssetDetail}
            settings={settingsState.settings}
          />
        ) : null}
        {route.screen === 'assets' ? (
          <AssetListScreen
            canUseApi={settingsState.canUseApi}
            onOpenSettings={openSettings}
            onSelectAsset={openAssetDetail}
            settings={settingsState.settings}
          />
        ) : null}
        {route.screen === 'assetDetail' ? (
          <AssetDetailScreen
            assetId={route.selectedAssetId}
            canUseApi={settingsState.canUseApi}
            onBack={openAssets}
            onPreview={openPreview}
            settings={settingsState.settings}
          />
        ) : null}
        {route.screen === 'previewReview' ? (
          <PreviewReviewScreen
            assetId={route.selectedAssetId}
            canUseApi={settingsState.canUseApi}
            onBack={() => openAssetDetail(route.selectedAssetId)}
            settings={settingsState.settings}
          />
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function NavButton({ active, label, onPress }) {
  return (
    <Text accessibilityRole="button" onPress={onPress} style={[styles.navButton, active && styles.navButtonActive]}>
      {label}
    </Text>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#f8fafc',
  },
  nav: {
    flexDirection: 'row',
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#cbd5e1',
    paddingHorizontal: 16,
    paddingBottom: 10,
    paddingTop: 10,
    backgroundColor: '#ffffff',
  },
  navButton: {
    flex: 1,
    minHeight: 40,
    borderRadius: 8,
    overflow: 'hidden',
    paddingHorizontal: 10,
    paddingVertical: 10,
    color: '#475569',
    fontSize: 14,
    fontWeight: '800',
    textAlign: 'center',
    backgroundColor: '#f1f5f9',
  },
  navButtonActive: {
    color: '#ffffff',
    backgroundColor: '#155e75',
  },
  content: {
    padding: 16,
    paddingBottom: 40,
  },
});
