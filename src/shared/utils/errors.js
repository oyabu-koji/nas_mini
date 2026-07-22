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
    processed_result_invalid_identity: 'The processed video identity is invalid. Refresh the asset and try again.',
    processed_result_not_found: 'The requested processed video is no longer available.',
    processed_result_not_ready: 'The processed video is not ready yet.',
    processed_result_superseded: 'A newer processed video is available. Refresh before saving.',
    processed_result_range_not_satisfiable: 'The processed video download returned an invalid range.',
    processed_result_download_failed: 'The processed video could not be downloaded.',
    processed_result_download_timeout: 'The processed video download made no progress.',
    processed_result_download_cancelled: 'The processed video download was cancelled.',
    processed_result_integrity_mismatch: 'The processed video did not pass integrity verification.',
    processed_result_unsupported_mime: 'This processed video format is not supported.',
    processed_result_cache_unavailable: 'Temporary storage is unavailable for the processed video.',
    processed_result_library_permission_denied: 'Photo library permission is required to save the processed video.',
    processed_result_library_save_failed: 'The processed video could not be saved to the photo library.',
    processed_result_save_state_unavailable: 'The save state could not be recorded safely.',
    processed_result_save_outcome_unknown: 'The save result could not be confirmed. Retry only after checking your library.',
    managed_capabilities_invalid: 'Backend capabilities could not be verified.',
    managed_catalog_invalid: 'The preset catalog is invalid. Rendering is disabled.',
    managed_rendition_invalid: 'The rendition response is invalid. Refresh before retrying.',
    managed_request_id_unavailable: 'A secure rendition request ID could not be created.',
    managed_rendition_state_unavailable: 'The rendition request state could not be saved safely.',
    incompatible_client: 'This app version is not compatible with managed presets.',
    rendition_request_conflict: 'This rendition request identity was already used for another selection.',
    rendition_asset_not_eligible: 'This asset is not eligible for managed rendering.',
    rendition_precondition_changed: 'The active processed video changed. Retry the same request.',
    rendition_relation_invalid: 'The backend could not validate the rendition job.',
    rendition_not_found: 'The rendition is no longer available.',
    lut_preset_unavailable: 'The requested preset was unavailable, so compression only was applied.',
    lut_preset_registered_invalid: 'The selected preset is registered but invalid.',
    lut_preset_source_changed: 'The selected LUT changed before it could be applied.',
    lut_application_failed: 'The selected LUT could not be applied.',
    rendition_storage_failed: 'The backend could not store the rendered video.',
    rendition_database_failed: 'The backend could not finalize the rendition state.',
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
