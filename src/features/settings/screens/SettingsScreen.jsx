import { StyleSheet, Text, TextInput, View } from 'react-native';

import { ActionButton } from '../../../shared/components/ActionButton';
import { ScreenHeader } from '../../../shared/components/ScreenHeader';

export function SettingsScreen({ settingsState }) {
  const {
    backendUrl,
    setBackendUrl,
    apiTokenInput,
    setApiTokenInput,
    hasSavedToken,
    status,
    message,
    saveSettings,
    runConnectionCheck,
  } = settingsState;

  const isBusy = status === 'saving' || status === 'checking' || status === 'loading';
  return (
    <View style={styles.container}>
      <ScreenHeader
        title="Settings"
        subtitle="Use the MBA or Mac mini Tailscale URL. Do not use 127.0.0.1 from iPhone."
      />

      <View style={styles.field}>
        <Text style={styles.label}>Backend URL</Text>
        <TextInput
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          onChangeText={setBackendUrl}
          placeholder="http://100.x.x.x:8000"
          style={styles.input}
          value={backendUrl}
        />
        <Text style={styles.hint}>
          Private HTTP: Tailscale IPv4, single-label MagicDNS, or .local. HTTPS is also supported.
        </Text>
      </View>

      <View style={styles.field}>
        <Text style={styles.label}>API token</Text>
        <TextInput
          autoCapitalize="none"
          autoCorrect={false}
          onChangeText={setApiTokenInput}
          placeholder={hasSavedToken ? 'Saved. Enter a new token to replace it.' : 'test-token'}
          secureTextEntry
          style={styles.input}
          value={apiTokenInput}
        />
        <Text style={styles.hint}>{hasSavedToken ? 'A token is saved in SecureStore.' : 'No token saved yet.'}</Text>
      </View>

      <View style={styles.actions}>
        <ActionButton disabled={isBusy} label={status === 'saving' ? 'Saving...' : 'Save'} onPress={saveSettings} />
        <ActionButton
          disabled={isBusy}
          label={status === 'checking' ? 'Checking...' : 'Check health'}
          onPress={runConnectionCheck}
          variant="secondary"
        />
      </View>

      {message ? <Text style={[styles.message, status === 'error' && styles.error]}>{message}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 16,
  },
  field: {
    gap: 6,
  },
  label: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700',
  },
  input: {
    minHeight: 46,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    paddingHorizontal: 12,
    color: '#0f172a',
    backgroundColor: '#ffffff',
  },
  hint: {
    color: '#64748b',
    fontSize: 12,
  },
  actions: {
    gap: 10,
  },
  message: {
    color: '#155e75',
    fontSize: 14,
    lineHeight: 20,
  },
  error: {
    color: '#b91c1c',
  },
});
