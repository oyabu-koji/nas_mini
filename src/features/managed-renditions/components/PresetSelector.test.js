import { fireEvent, render } from '@testing-library/react-native';

import { PresetSelector } from './PresetSelector';

function managed(overrides = {}) {
  return {
    eligible: true,
    catalogStatus: 'ready',
    presets: [{
      presetId: 'identity-v1',
      displayName: 'Identity test',
      presetKind: 'generated-identity',
      version: '1',
      targetColorSpace: null,
    }, {
      presetId: 'custom-look',
      displayName: 'Studio look',
      presetKind: 'custom',
      version: '3',
      targetColorSpace: 'Studio declared gamut',
    }],
    selectedPresetId: 'identity-v1',
    submitStatus: 'idle',
    rendition: null,
    error: null,
    selectPreset: jest.fn(),
    submit: jest.fn(),
    retry: jest.fn(),
    reloadCatalog: jest.fn(),
    ...overrides,
  };
}

describe('PresetSelector', () => {
  it('renders accessible server presets and an explicit render command', async () => {
    const state = managed();
    const view = await render(<PresetSelector managed={state} />);

    await fireEvent.press(view.getByText('Studio look'));
    await fireEvent.press(view.getByText('Render rendition'));

    expect(state.selectPreset).toHaveBeenCalledWith('custom-look');
    expect(state.submit).toHaveBeenCalledTimes(1);
    expect(view.getByText('Declared target: Studio declared gamut')).toBeTruthy();
    expect(view.queryByText(/Apple Log/)).toBeNull();
    expect(view.queryByText(/Rec\.709/)).toBeNull();
  });

  it('shows every server phase and requested/applied identity', async () => {
    const phases = [
      ['queued', 'Queued'],
      ['validating', 'Validating preset'],
      ['rendering', 'Rendering'],
      ['finalizing', 'Finalizing'],
      ['ready', 'Ready'],
      ['failed', 'Failed'],
      ['superseded', 'Superseded'],
    ];
    const stateFor = (phase) => managed({
      rendition: {
        state: phase,
        requestedPresetId: 'identity-v1',
        appliedPresetId: phase === 'ready' ? 'identity-v1' : null,
        colorTransformStatus: phase === 'failed' ? 'failed' : null,
        errorCode: phase === 'failed' ? 'lut_application_failed' : null,
      },
    });
    const view = await render(<PresetSelector managed={stateFor('queued')} />);
    for (const [phase, label] of phases) {
      await view.rerender(<PresetSelector managed={stateFor(phase)} />);
      expect(view.getByText(`Phase: ${label}`)).toBeTruthy();
      expect(view.getByText('Requested: identity-v1')).toBeTruthy();
      if (phase === 'ready') {
        expect(view.getByText('Applied: identity-v1')).toBeTruthy();
      }
      if (!['ready', 'failed', 'superseded'].includes(phase)) {
        expect(view.getByText('Render rendition')).toBeDisabled();
      }
    }
  });

  it('shows fallback and stable failure states without inventing compress-only', async () => {
    const fallback = await render(<PresetSelector managed={managed({
      presets: [],
      selectedPresetId: null,
      rendition: {
        state: 'ready',
        requestedPresetId: 'missing-look',
        appliedPresetId: 'compress-only',
        colorTransformStatus: 'unavailable',
        errorCode: 'lut_preset_unavailable',
      },
    })} />);

    expect(fallback.getByText('No server presets are available.')).toBeTruthy();
    expect(fallback.queryByText('Compress only')).toBeNull();
    expect(fallback.getByText(/compression only was applied/)).toBeTruthy();
    await fallback.rerender(<PresetSelector managed={managed({
      rendition: {
        state: 'failed',
        requestedPresetId: 'custom-look',
        appliedPresetId: null,
        colorTransformStatus: 'failed',
        errorCode: 'lut_preset_source_changed',
      },
    })} />);
    expect(fallback.getByText('The selected LUT changed before it could be applied.')).toBeTruthy();
    await fallback.rerender(<PresetSelector managed={managed({ eligible: false })} />);
    expect(fallback.queryByText('Render rendition')).toBeNull();
  });
});
