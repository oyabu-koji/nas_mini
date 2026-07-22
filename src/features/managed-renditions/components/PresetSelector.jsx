import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ActionButton } from '../../../shared/components/ActionButton';
import { messageForErrorCode } from '../../../shared/utils/errors';

const ACTIVE_STATES = new Set(['queued', 'validating', 'rendering', 'finalizing']);

export function PresetSelector({ managed }) {
  if (!managed.eligible) {
    return null;
  }
  const active = ACTIVE_STATES.has(managed.rendition?.state);
  return (
    <View style={styles.section}>
      <Text style={styles.heading}>Managed rendition</Text>
      {managed.catalogStatus === 'loading' ? <Text style={styles.meta}>Loading presets...</Text> : null}
      {managed.catalogStatus === 'error' || managed.catalogStatus === 'incompatible' ? (
        <View style={styles.stack}>
          <Text style={styles.error}>{managed.error?.message || 'Preset catalog unavailable.'}</Text>
          <ActionButton label="Retry presets" onPress={managed.reloadCatalog} variant="secondary" />
        </View>
      ) : null}
      {managed.catalogStatus === 'ready' && managed.presets.length === 0 ? (
        <Text style={styles.error}>No server presets are available.</Text>
      ) : null}
      {managed.catalogStatus === 'ready' ? (
        <View accessibilityRole="radiogroup" style={styles.options}>
          {managed.presets.map((preset) => {
            const selected = managed.selectedPresetId === preset.presetId;
            return (
              <Pressable
                accessibilityRole="radio"
                accessibilityState={{ checked: selected, disabled: active }}
                disabled={active}
                key={preset.presetId}
                onPress={() => managed.selectPreset(preset.presetId)}
                style={[styles.option, selected && styles.optionSelected]}
              >
                <Text style={[styles.optionTitle, selected && styles.optionTitleSelected]}>{preset.displayName}</Text>
                <Text style={[styles.optionMeta, selected && styles.optionMetaSelected]}>{preset.presetKind} / v{preset.version}</Text>
                {preset.presetKind === 'custom' && preset.targetColorSpace ? (
                  <Text style={[styles.optionMeta, selected && styles.optionMetaSelected]}>Declared target: {preset.targetColorSpace}</Text>
                ) : null}
              </Pressable>
            );
          })}
        </View>
      ) : null}

      {managed.rendition ? (
        <View style={styles.status}>
          <Text style={styles.statusTitle}>Phase: {phaseLabel(managed.rendition.state)}</Text>
          <Text style={styles.meta}>Requested: {managed.rendition.requestedPresetId}</Text>
          {managed.rendition.appliedPresetId ? (
            <Text style={styles.meta}>Applied: {managed.rendition.appliedPresetId}</Text>
          ) : null}
          {managed.rendition.colorTransformStatus === 'unavailable' ? (
            <Text style={styles.notice}>{messageForErrorCode('lut_preset_unavailable')}</Text>
          ) : null}
          {managed.rendition.errorCode && managed.rendition.state === 'failed' ? (
            <Text style={styles.error}>{messageForErrorCode(managed.rendition.errorCode)}</Text>
          ) : null}
        </View>
      ) : null}
      {managed.error && managed.catalogStatus === 'ready' ? (
        <Text style={styles.error}>{managed.error.message}</Text>
      ) : null}
      {managed.submitStatus === 'retryable_failed' ? (
        <ActionButton label="Retry rendition" onPress={managed.retry} variant="secondary" />
      ) : (
        <ActionButton
          disabled={
            managed.catalogStatus !== 'ready'
            || !managed.selectedPresetId
            || managed.submitStatus === 'submitting'
            || active
          }
          label={managed.submitStatus === 'submitting' ? 'Submitting rendition...' : 'Render rendition'}
          onPress={managed.submit}
        />
      )}
    </View>
  );
}

function phaseLabel(value) {
  const labels = {
    queued: 'Queued',
    validating: 'Validating preset',
    rendering: 'Rendering',
    finalizing: 'Finalizing',
    ready: 'Ready',
    failed: 'Failed',
    superseded: 'Superseded',
  };
  return labels[value] ?? value;
}

const styles = StyleSheet.create({
  section: {
    gap: 10,
    borderTopWidth: 1,
    borderTopColor: '#cbd5e1',
    paddingTop: 12,
  },
  heading: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '800',
  },
  stack: {
    gap: 8,
  },
  options: {
    gap: 8,
  },
  option: {
    minHeight: 56,
    justifyContent: 'center',
    gap: 3,
    borderWidth: 1,
    borderColor: '#94a3b8',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 8,
    backgroundColor: '#ffffff',
  },
  optionSelected: {
    borderColor: '#155e75',
    backgroundColor: '#ecfeff',
  },
  optionTitle: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700',
  },
  optionTitleSelected: {
    color: '#134e4a',
  },
  optionMeta: {
    color: '#64748b',
    fontSize: 12,
  },
  optionMetaSelected: {
    color: '#0f766e',
  },
  status: {
    gap: 4,
  },
  statusTitle: {
    color: '#1e293b',
    fontSize: 13,
    fontWeight: '700',
  },
  meta: {
    color: '#475569',
    fontSize: 13,
    lineHeight: 18,
  },
  notice: {
    color: '#92400e',
    fontSize: 13,
    lineHeight: 18,
  },
  error: {
    color: '#b91c1c',
    fontSize: 13,
    lineHeight: 18,
  },
});
