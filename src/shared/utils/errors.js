export class AppError extends Error {
  constructor(code, message, options = {}) {
    super(message);
    this.name = 'AppError';
    this.code = code;
    this.status = options.status ?? null;
    this.retryable = options.retryable ?? false;
  }
}

export function createAppError(code, message, options) {
  return new AppError(code, message, options);
}

export function classifyHttpStatus(status) {
  if (status === 401) {
    return 'unauthorized';
  }
  if (status === 403) {
    return 'forbidden';
  }
  if (status === 404) {
    return 'not_found';
  }
  if (status === 409) {
    return 'preview_not_ready';
  }
  if (status === 413) {
    return 'too_large';
  }
  if (status === 416) {
    return 'range_not_satisfiable';
  }
  if (status === 422) {
    return 'validation_error';
  }
  if (status >= 500) {
    return 'server_error';
  }
  return 'unknown';
}

export function messageForErrorCode(code) {
  const messages = {
    missing_settings: 'Backend URL and API token are required.',
    invalid_url: 'Enter a valid http:// backend URL.',
    unauthorized: 'API token is missing or invalid.',
    forbidden: 'This API token cannot access the backend.',
    not_found: 'The requested asset or preview was not found.',
    too_large: 'This file is larger than the Phase 1 upload limit.',
    preview_not_ready: 'Preview is not ready yet.',
    validation_error: 'The selected file metadata could not be accepted.',
    range_not_satisfiable: 'Preview playback requested an invalid range.',
    network_unreachable: 'Backend is unreachable. Check Tailscale, URL, and server status.',
    timeout: 'The request timed out.',
    native_hash_unavailable: 'Video hashing requires a Development Build.',
    native_hash_invalid_range: 'The selected video range is invalid.',
    native_hash_invalid_result: 'Video hashing returned an invalid result.',
    media_unavailable: 'The selected video is not available locally.',
    resumable_video_requires_library_asset: 'Select this video from the photo library before uploading.',
    resumable_upload_source_changed: 'The selected video changed. Start a new upload to continue.',
    session_cancelled: 'This upload was cancelled. Start a new upload to continue.',
    session_expired: 'This upload expired. Start a new upload to continue.',
    server_error: 'Backend returned an internal error.',
    preview_failed: 'Preview generation failed.',
    storage_or_cache_error: 'Preview cache could not be prepared.',
    unknown: 'Something went wrong.',
  };

  return messages[code] ?? messages.unknown;
}

export function toDisplayError(error) {
  if (error instanceof AppError) {
    return {
      code: error.code,
      message: error.message,
      retryable: error.retryable,
    };
  }

  return {
    code: 'unknown',
    message: messageForErrorCode('unknown'),
    retryable: true,
  };
}

export function createHttpError(status, serverCode = null, retryable = null) {
  const code = serverCode || classifyHttpStatus(status);
  return new AppError(code, messageForErrorCode(code), {
    status,
    retryable: retryable ?? (code === 'network_unreachable' || code === 'server_error' || code === 'preview_not_ready'),
  });
}

export function createNetworkError() {
  return new AppError('network_unreachable', messageForErrorCode('network_unreachable'), {
    retryable: true,
  });
}

export function createTimeoutError() {
  return new AppError('timeout', messageForErrorCode('timeout'), {
    retryable: true,
  });
}
