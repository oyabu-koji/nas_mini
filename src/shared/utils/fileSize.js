import { MAX_UPLOAD_SIZE_BYTES } from '../constants/assetStatuses';

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return 'Unknown size';
  }

  if (bytes < 1024) {
    return `${bytes} B`;
  }

  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[unitIndex]}`;
}

export function isUploadTooLarge(sizeBytes) {
  return Number.isFinite(sizeBytes) && sizeBytes > MAX_UPLOAD_SIZE_BYTES;
}

export function canUploadSize(sizeBytes) {
  return Number.isFinite(sizeBytes) && sizeBytes >= 0 && !isUploadTooLarge(sizeBytes);
}
