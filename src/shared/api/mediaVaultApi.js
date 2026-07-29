import {
  createAppError,
  createHttpError,
  createNetworkError,
  createTimeoutError,
  messageForErrorCode,
} from '../utils/errors';
import { File } from 'expo-file-system';
import { fetch as expoFetch } from 'expo/fetch';
import {
  CLIENT_VERSION,
  CLIENT_VERSION_HEADER,
} from '../constants/clientVersion';
import { validateAndNormalizeBackendUrl } from '../services/backendEndpointPolicy';

export const DEFAULT_REQUEST_TIMEOUT_MS = 15000;
export const UPLOAD_REQUEST_TIMEOUT_MS = 600000;
export const SESSION_REQUEST_TIMEOUT_MS = 60000;
export const SESSION_CHUNK_TIMEOUT_MS = 600000;

const RESULT_ID_PATTERN = /^[0-9a-f]{32}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const PRESET_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const FORMAL_FAILURE_CODES = new Set([
  'log_detector_manifest_invalid',
  'log_detector_version_mismatch',
  'log_probe_timeout',
  'log_probe_failed',
  'log_probe_output_invalid',
  'lut_preset_registered_invalid',
  'lut_preset_source_changed',
  'lut_application_failed',
  'formal_preview_source_invalid',
  'formal_preview_render_failed',
  'formal_preview_storage_failed',
  'formal_preview_database_failed',
  'formal_preview_relation_invalid',
]);

export function normalizeBaseUrl(input) {
  return validateAndNormalizeBackendUrl(input);
}

export function joinApiUrl(baseUrl, path) {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  const normalizedPath = String(path ?? '').startsWith('/') ? String(path) : `/${path}`;
  return `${normalizedBaseUrl}${normalizedPath}`;
}

export function createAuthHeaders(apiToken) {
  const token = String(apiToken ?? '').trim();
  if (!token) {
    throw createAppError('missing_settings', messageForErrorCode('missing_settings'));
  }
  return {
    Authorization: `Bearer ${token}`,
  };
}

export function createVersionedAuthHeaders(apiToken) {
  return {
    ...createAuthHeaders(apiToken),
    [CLIENT_VERSION_HEADER]: CLIENT_VERSION,
  };
}

async function parseJsonSafely(response) {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export async function requestJson({
  baseUrl,
  apiToken,
  path,
  method = 'GET',
  body,
  headers = {},
  requiresAuth = true,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  fetchImpl = fetch,
}) {
  const requestUrl = joinApiUrl(baseUrl, path);
  const requestHeaders = {
    Accept: 'application/json',
    ...headers,
    ...(requiresAuth ? createAuthHeaders(apiToken) : {}),
  };

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetchImpl(requestUrl, {
      method,
      headers: requestHeaders,
      body,
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw createTimeoutError();
    }
    throw createNetworkError();
  } finally {
    clearTimeout(timeoutId);
  }

  const payload = await parseJsonSafely(response);
  if (!response.ok) {
    throw createHttpError(response.status, payload?.code, payload?.retryable);
  }
  return payload;
}

export async function checkHealth(settings) {
  const baseUrl = normalizeBaseUrl(settings?.backendUrl);
  const apiToken = String(settings?.apiToken ?? '').trim();
  if (!apiToken) {
    throw createAppError('missing_settings', messageForErrorCode('missing_settings'));
  }
  return requestJson({
    baseUrl,
    apiToken,
    path: '/health',
    requiresAuth: false,
  });
}

export async function uploadAsset({ settings, pickedAsset, isLog }) {
  const formData = new FormData();
  formData.append('file', {
    uri: pickedAsset.uri,
    name: pickedAsset.filename,
    type: pickedAsset.mimeType || defaultMimeTypeForAsset(pickedAsset),
  });
  formData.append('type', pickedAsset.type);
  formData.append('filename', pickedAsset.filename);
  formData.append('taken_at', pickedAsset.takenAt ?? '');
  formData.append('latitude', pickedAsset.latitude == null ? '' : String(pickedAsset.latitude));
  formData.append('longitude', pickedAsset.longitude == null ? '' : String(pickedAsset.longitude));
  formData.append('exif_json', pickedAsset.exif ? JSON.stringify(pickedAsset.exif) : '');
  formData.append('is_log', isLog ? 'true' : 'false');

  const payload = await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: '/assets/upload',
    method: 'POST',
    body: formData,
    timeoutMs: UPLOAD_REQUEST_TIMEOUT_MS,
  });

  return sanitizeUploadResponse(payload);
}

export async function createUploadSession({ settings, session }) {
  return sanitizeSession(
    await requestJson({
      baseUrl: settings.backendUrl,
      apiToken: settings.apiToken,
      path: '/upload-sessions',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(session),
      timeoutMs: SESSION_REQUEST_TIMEOUT_MS,
    }),
  );
}

export async function getUploadSession({ settings, sessionId }) {
  return sanitizeSession(
    await requestJson({
      baseUrl: settings.backendUrl,
      apiToken: settings.apiToken,
      path: `/upload-sessions/${encodeURIComponent(sessionId)}`,
      timeoutMs: SESSION_REQUEST_TIMEOUT_MS,
    }),
  );
}

export async function uploadUploadSessionChunk({
  settings,
  sessionId,
  uri,
  chunkIndex,
  offset,
  length,
  totalSize,
  sha256,
}) {
  const file = new File(uri);
  const body = file.slice(offset, offset + length);
  return requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: `/upload-sessions/${encodeURIComponent(sessionId)}/chunks/${chunkIndex}`,
    method: 'PUT',
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Range': `bytes ${offset}-${offset + length - 1}/${totalSize}`,
      'X-Chunk-SHA256': sha256,
    },
    body,
    timeoutMs: SESSION_CHUNK_TIMEOUT_MS,
    fetchImpl: expoFetch,
  });
}

export async function finalizeUploadSession({ settings, sessionId }) {
  const payload = await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: `/upload-sessions/${encodeURIComponent(sessionId)}/finalize`,
    method: 'POST',
    timeoutMs: SESSION_REQUEST_TIMEOUT_MS,
  });
  return {
    ...payload,
    session: sanitizeSession(payload?.session),
  };
}

export async function cancelUploadSession({ settings, sessionId }) {
  return sanitizeSession(
    await requestJson({
      baseUrl: settings.backendUrl,
      apiToken: settings.apiToken,
      path: `/upload-sessions/${encodeURIComponent(sessionId)}`,
      method: 'DELETE',
      timeoutMs: SESSION_REQUEST_TIMEOUT_MS,
    }),
  );
}

export async function listAssets(settings) {
  const payload = await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: '/assets',
  });
  return {
    ...payload,
    items: Array.isArray(payload?.items) ? payload.items.map(sanitizeAsset) : [],
  };
}

export async function getAsset(settings, assetId) {
  return sanitizeAsset(
    await requestJson({
      baseUrl: settings.backendUrl,
      apiToken: settings.apiToken,
      path: `/assets/${assetId}`,
    }),
  );
}

export async function confirmPreview(settings, assetId) {
  return sanitizeAsset(
    await requestJson({
      baseUrl: settings.backendUrl,
      apiToken: settings.apiToken,
      path: `/assets/${assetId}/preview-confirmation`,
      method: 'POST',
      headers: { [CLIENT_VERSION_HEADER]: CLIENT_VERSION },
    }),
  );
}

export function buildPreviewUrl(baseUrl, assetId) {
  return joinApiUrl(baseUrl, `/assets/${assetId}/preview`);
}

export function buildPreviewVideoSource({ baseUrl, apiToken, assetId }) {
  return buildPreviewSource({ baseUrl, apiToken, assetId });
}

export function buildPreviewSource({ baseUrl, apiToken, assetId }) {
  const uri = buildPreviewUrl(baseUrl, assetId);
  return {
    uri,
    headers: createVersionedAuthHeaders(apiToken),
  };
}

export function buildProcessedResultPath(assetId, resultId) {
  const safeAssetId = normalizeBackendAssetId(assetId);
  const safeResultId = normalizeResultId(resultId);
  return `/assets/${safeAssetId}/results/${safeResultId}`;
}

export function buildProcessedResultUrl(baseUrl, assetId, resultId) {
  return joinApiUrl(baseUrl, buildProcessedResultPath(assetId, resultId));
}

export function buildProcessedResultSource({ baseUrl, apiToken, assetId, result }) {
  const safeResult = sanitizeProcessedResult(result, assetId);
  if (!safeResult) {
    throw createAppError(
      'processed_result_invalid_identity',
      messageForErrorCode('processed_result_invalid_identity'),
    );
  }
  const uri = buildProcessedResultUrl(baseUrl, assetId, safeResult.result_id);
  return {
    uri,
    headers: createVersionedAuthHeaders(apiToken),
  };
}

function defaultMimeTypeForAsset(asset) {
  if (asset.type === 'image') {
    return 'image/jpeg';
  }
  return 'video/mp4';
}

function sanitizeUploadResponse(payload) {
  if (!payload) {
    return payload;
  }
  return {
    ...payload,
    asset: sanitizeAsset(payload.asset),
    job: payload.job
      ? {
          id: payload.job.id,
          job_type: payload.job.job_type,
          status: payload.job.status,
          asset_id: payload.job.asset_id,
        }
      : null,
  };
}

export function sanitizeAsset(asset) {
  if (!asset) {
    return null;
  }

  const safeAsset = { ...asset };
  delete safeAsset.original_path;
  safeAsset.delete_candidate_status = (
    safeAsset.delete_candidate_status === 'safe_to_delete_candidate'
      ? 'safe_to_delete_candidate'
      : 'not_candidate'
  );
  if (Object.prototype.hasOwnProperty.call(safeAsset, 'active_processed_result')) {
    safeAsset.active_processed_result = sanitizeProcessedResult(
      safeAsset.active_processed_result,
      safeAsset.id,
    );
  }
  if (Object.prototype.hasOwnProperty.call(safeAsset, 'formal_preview')) {
    safeAsset.formal_preview = sanitizeFormalPreview(
      safeAsset.formal_preview,
      safeAsset.id,
    );
  }
  return safeAsset;
}

export function sanitizeFormalPreview(value, assetId) {
  if (value == null) {
    return null;
  }
  const invalid = () => {
    throw createAppError(
      'formal_preview_invalid',
      messageForErrorCode('formal_preview_invalid'),
    );
  };
  if (
    typeof value !== 'object'
    || value.schema_version !== 1
    || !['generating', 'ready', 'failed'].includes(value.state)
    || !Number.isSafeInteger(value.generation)
    || value.generation < 1
  ) {
    return invalid();
  }
  const detector = sanitizeDetectorGroup(value);
  if (detector === false) {
    return invalid();
  }
  if (value.state === 'generating') {
    const hasPresetSnapshot = value.requested_preset_id != null;
    if (
      value.preview_id != null
      || value.result != null
      || value.failure_code != null
      || !nullablePresetId(value.requested_preset_id)
      || !nullablePresetId(value.applied_preset_id)
      || (
        hasPresetSnapshot
          ? !isReadyTransformClaim(value)
          : !emptyTransformGroup(value)
      )
    ) {
      return invalid();
    }
    return {
      ...formalBase(value, detector),
      requested_preset_id: value.requested_preset_id ?? null,
      applied_preset_id: value.applied_preset_id ?? null,
      applied_preset_display_name: nullableSafeText(value.applied_preset_display_name, 128),
      preset_version: nullableSafeText(value.preset_version, 64),
      manifest_sha256: nullableDigest(value.manifest_sha256),
      lut_sha256: nullableDigest(value.lut_sha256),
      transform_kind: value.transform_kind ?? null,
      color_transform_status: value.color_transform_status ?? null,
      color_transform_error_code: nullableSafeText(value.color_transform_error_code, 100),
      preview_id: null,
      result: null,
      failure_code: null,
    };
  }
  if (value.state === 'failed') {
    if (
      !FORMAL_FAILURE_CODES.has(value.failure_code)
      || value.preview_id != null
      || value.result != null
    || value.applied_preset_id != null
    || !nullablePresetId(value.requested_preset_id)
    || (value.transform_kind == null) !== (value.color_transform_status == null)
    || (value.color_transform_status != null && value.color_transform_status !== 'failed')
    || !validNullableText(value.color_transform_error_code, 100)
    ) {
      return invalid();
    }
    return {
      ...formalBase(value, detector),
      requested_preset_id: value.requested_preset_id ?? null,
      applied_preset_id: null,
      applied_preset_display_name: null,
      preset_version: null,
      manifest_sha256: null,
      lut_sha256: null,
      transform_kind: value.transform_kind ?? null,
      color_transform_status: value.color_transform_status ?? null,
      color_transform_error_code: nullableSafeText(value.color_transform_error_code, 100),
      preview_id: null,
      result: null,
      failure_code: value.failure_code,
    };
  }

  const result = sanitizeProcessedResult(value.result, assetId);
  const previewId = String(value.preview_id ?? '');
  if (
    detector == null
    || !result
    || !RESULT_ID_PATTERN.test(previewId)
    || !PRESET_ID_PATTERN.test(String(value.requested_preset_id ?? ''))
    || !PRESET_ID_PATTERN.test(String(value.applied_preset_id ?? ''))
    || value.failure_code != null
  ) {
    return invalid();
  }
  const applied = (
    value.detection_status === 'apple_log'
    && value.requested_preset_id === 'generated-apple-log-rec709'
    && value.applied_preset_id === 'generated-apple-log-rec709'
    && value.transform_kind === 'lut'
    && value.color_transform_status === 'applied'
    && value.color_transform_error_code == null
    && safeText(value.applied_preset_display_name, 128)
    && safeText(value.preset_version, 64)
    && SHA256_PATTERN.test(String(value.manifest_sha256 ?? ''))
    && SHA256_PATTERN.test(String(value.lut_sha256 ?? ''))
  );
  if (!isReadyTransformClaim(value)) {
    return invalid();
  }
  return {
    ...formalBase(value, detector),
    requested_preset_id: value.requested_preset_id,
    applied_preset_id: value.applied_preset_id,
    applied_preset_display_name: applied ? value.applied_preset_display_name : null,
    preset_version: applied ? value.preset_version : null,
    manifest_sha256: applied ? value.manifest_sha256 : null,
    lut_sha256: applied ? value.lut_sha256 : null,
    transform_kind: value.transform_kind,
    color_transform_status: value.color_transform_status,
    color_transform_error_code: value.color_transform_error_code ?? null,
    preview_id: previewId,
    result,
    failure_code: null,
  };
}

export function sanitizeProcessedResult(result, assetId) {
  if (!result || typeof result !== 'object') {
    return null;
  }

  let canonicalPath;
  try {
    canonicalPath = buildProcessedResultPath(assetId, result.result_id);
  } catch {
    return null;
  }

  if (
    result.url !== canonicalPath
    || result.mime_type !== 'video/mp4'
    || !Number.isSafeInteger(result.size_bytes)
    || result.size_bytes <= 0
    || typeof result.sha256 !== 'string'
    || !SHA256_PATTERN.test(result.sha256)
    || typeof result.created_at !== 'string'
    || !result.created_at.trim()
  ) {
    return null;
  }

  return {
    result_id: result.result_id,
    mime_type: result.mime_type,
    size_bytes: result.size_bytes,
    sha256: result.sha256,
    created_at: result.created_at,
    url: canonicalPath,
  };
}

function normalizeBackendAssetId(assetId) {
  const numericAssetId = typeof assetId === 'number' ? assetId : Number(assetId);
  if (!Number.isSafeInteger(numericAssetId) || numericAssetId <= 0) {
    throw createAppError(
      'processed_result_invalid_identity',
      messageForErrorCode('processed_result_invalid_identity'),
    );
  }
  return numericAssetId;
}

function sanitizeDetectorGroup(value) {
  const status = value.detection_status;
  const identity = [
    value.detector_rule_version,
    value.detector_manifest_sha256,
    value.detector_evidence_sha256,
  ];
  if (status == null) {
    return value.source_profile == null && identity.every((item) => item == null)
      ? null
      : false;
  }
  if (
    !['apple_log', 'not_log', 'unknown'].includes(status)
    || !safeText(value.detector_rule_version, 64)
    || !SHA256_PATTERN.test(String(value.detector_manifest_sha256 ?? ''))
    || !SHA256_PATTERN.test(String(value.detector_evidence_sha256 ?? ''))
    || (value.source_profile != null && !safeText(value.source_profile, 128))
  ) {
    return false;
  }
  return {
    detection_status: status,
    source_profile: value.source_profile ?? null,
    detector_rule_version: value.detector_rule_version,
    detector_manifest_sha256: value.detector_manifest_sha256,
    detector_evidence_sha256: value.detector_evidence_sha256,
  };
}

function formalBase(value, detector) {
  return {
    schema_version: 1,
    state: value.state,
    generation: value.generation,
    detection_status: detector?.detection_status ?? null,
    source_profile: detector?.source_profile ?? null,
    detector_rule_version: detector?.detector_rule_version ?? null,
    detector_manifest_sha256: detector?.detector_manifest_sha256 ?? null,
    detector_evidence_sha256: detector?.detector_evidence_sha256 ?? null,
  };
}

function nullablePresetId(value) {
  return value == null || PRESET_ID_PATTERN.test(String(value));
}

function nullableSafeText(value, maximum) {
  return value == null ? null : safeText(value, maximum) ? value : null;
}

function nullableDigest(value) {
  return value == null ? null : SHA256_PATTERN.test(String(value)) ? value : null;
}

function emptyAppliedIdentity(value) {
  return (
    value.applied_preset_display_name == null
    && value.preset_version == null
    && value.manifest_sha256 == null
    && value.lut_sha256 == null
  );
}

function emptyTransformGroup(value) {
  return (
    value.requested_preset_id == null
    && value.applied_preset_id == null
    && value.applied_preset_display_name == null
    && value.preset_version == null
    && value.manifest_sha256 == null
    && value.lut_sha256 == null
    && value.transform_kind == null
    && value.color_transform_status == null
    && value.color_transform_error_code == null
  );
}

function isReadyTransformClaim(value) {
  const fallback = (
    value.detection_status === 'apple_log'
    && value.requested_preset_id === 'generated-apple-log-rec709'
    && value.applied_preset_id === 'compress-only'
    && value.transform_kind === 'none'
    && value.color_transform_status === 'unavailable'
    && value.color_transform_error_code === 'lut_preset_unavailable'
    && emptyAppliedIdentity(value)
  );
  const ordinary = (
    ['not_log', 'unknown'].includes(value.detection_status)
    && value.requested_preset_id === 'compress-only'
    && value.applied_preset_id === 'compress-only'
    && value.transform_kind === 'none'
    && value.color_transform_status === 'not_requested'
    && value.color_transform_error_code == null
    && emptyAppliedIdentity(value)
  );
  const applied = (
    value.detection_status === 'apple_log'
    && value.requested_preset_id === 'generated-apple-log-rec709'
    && value.applied_preset_id === 'generated-apple-log-rec709'
    && value.transform_kind === 'lut'
    && value.color_transform_status === 'applied'
    && value.color_transform_error_code == null
    && safeText(value.applied_preset_display_name, 128)
    && safeText(value.preset_version, 64)
    && SHA256_PATTERN.test(String(value.manifest_sha256 ?? ''))
    && SHA256_PATTERN.test(String(value.lut_sha256 ?? ''))
  );
  return fallback || ordinary || applied;
}

function validNullableText(value, maximum) {
  return value == null || safeText(value, maximum);
}

function safeText(value, maximum) {
  return (
    typeof value === 'string'
    && value.length > 0
    && value.length <= maximum
    && !/[\u0000-\u001f\u007f]/.test(value)
  );
}

function normalizeResultId(resultId) {
  const normalizedResultId = String(resultId ?? '');
  if (!RESULT_ID_PATTERN.test(normalizedResultId)) {
    throw createAppError(
      'processed_result_invalid_identity',
      messageForErrorCode('processed_result_invalid_identity'),
    );
  }
  return normalizedResultId;
}

function sanitizeSession(session) {
  if (!session) {
    return null;
  }
  return {
    id: session.id,
    status: session.status,
    size_bytes: session.size_bytes,
    chunk_size_bytes: session.chunk_size_bytes,
    total_chunks: session.total_chunks,
    expected_file_sha256: session.expected_file_sha256,
    expires_at: session.expires_at,
    missing_chunk_indexes: Array.isArray(session.missing_chunk_indexes) ? session.missing_chunk_indexes : [],
    retryable: Boolean(session.retryable),
    failure_code: session.failure_code ?? null,
    asset_id: session.asset_id ?? null,
    finalization_job_id: session.finalization_job_id ?? null,
  };
}
