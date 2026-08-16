export function formalPreviewProfileLabel(formalPreview) {
  if (!formalPreview || formalPreview.state !== 'ready') {
    return null;
  }
  if (
    formalPreview.detection_status === 'apple_log'
    && formalPreview.color_transform_status === 'unavailable'
  ) {
    if (formalPreview.source_profile === 'apple-log-1') {
      return 'Apple Log 1 (unconverted)';
    }
    if (formalPreview.source_profile === 'apple-log-2') {
      return 'Apple Log 2 (unconverted)';
    }
    return null;
  }
  if (formalPreview.detection_status === 'not_log') {
    return 'Ordinary video';
  }
  if (formalPreview.detection_status === 'unknown') {
    return 'Video profile unknown (unconverted)';
  }
  return null;
}
