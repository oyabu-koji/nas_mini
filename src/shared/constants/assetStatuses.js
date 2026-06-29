export const MAX_UPLOAD_SIZE_BYTES = 104857600;

export const PREVIEW_STATUS = {
  NOT_STARTED: 'not_started',
  GENERATING: 'preview_generating',
  READY: 'preview_ready',
  FAILED: 'failed',
};

export const REVIEW_STATUS = {
  NOT_REVIEWED: 'not_reviewed',
  PREVIEW_CONFIRMED: 'preview_confirmed',
};

export const ASSET_TYPE = {
  IMAGE: 'image',
  VIDEO: 'video',
};

export const STATUS_LABELS = {
  local_only: 'local only',
  uploading: 'uploading',
  uploaded: 'uploaded',
  failed: 'failed',
  not_started: 'not started',
  server_hash_recorded: 'server hash recorded',
  preview_generating: 'preview generating',
  preview_ready: 'preview ready',
  not_reviewed: 'not reviewed',
  preview_confirmed: 'preview confirmed',
  not_candidate: 'not candidate',
};

export function getStatusLabel(status) {
  return STATUS_LABELS[status] ?? String(status ?? 'unknown');
}

export function getStatusTone(status) {
  if (status === PREVIEW_STATUS.READY || status === REVIEW_STATUS.PREVIEW_CONFIRMED || status === 'uploaded') {
    return 'success';
  }
  if (status === PREVIEW_STATUS.GENERATING || status === 'uploading') {
    return 'pending';
  }
  if (status === PREVIEW_STATUS.FAILED || status === 'failed') {
    return 'danger';
  }
  return 'neutral';
}
