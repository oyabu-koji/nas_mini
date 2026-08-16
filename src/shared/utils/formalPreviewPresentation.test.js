import { formalPreviewProfileLabel } from './formalPreviewPresentation';

describe('formalPreviewProfileLabel', () => {
  it.each([
    ['apple-log-1', 'Apple Log 1 (unconverted)'],
    ['apple-log-2', 'Apple Log 2 (unconverted)'],
  ])('maps %s fallback to its exact label', (sourceProfile, label) => {
    expect(formalPreviewProfileLabel({
      state: 'ready',
      detection_status: 'apple_log',
      source_profile: sourceProfile,
      color_transform_status: 'unavailable',
    })).toBe(label);
  });

  it.each([
    [{ state: 'ready', detection_status: 'not_log' }, 'Ordinary video'],
    [{ state: 'ready', detection_status: 'unknown' }, 'Video profile unknown (unconverted)'],
  ])('preserves the closed non-LOG label', (formalPreview, label) => {
    expect(formalPreviewProfileLabel(formalPreview)).toBe(label);
  });

  it.each([
    null,
    { state: 'generating' },
    { state: 'failed' },
    {
      state: 'ready',
      detection_status: 'apple_log',
      source_profile: 'apple-log-1',
      color_transform_status: 'applied',
    },
    {
      state: 'ready',
      detection_status: 'apple_log',
      source_profile: 'apple-log-3',
      color_transform_status: 'unavailable',
    },
  ])('does not label non-ready, applied, or invalid claims', (formalPreview) => {
    expect(formalPreviewProfileLabel(formalPreview)).toBeNull();
  });
});
