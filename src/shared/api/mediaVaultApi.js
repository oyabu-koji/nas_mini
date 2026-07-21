import {
  createAppError,
  createHttpError,
  createNetworkError,
  createTimeoutError,
  messageForErrorCode,
} from '../utils/errors';
import { File } from 'expo-file-system';
import { fetch as expoFetch } from 'expo/fetch';

export const DEFAULT_REQUEST_TIMEOUT_MS = 15000;
export const UPLOAD_REQUEST_TIMEOUT_MS = 600000;
export const SESSION_REQUEST_TIMEOUT_MS = 60000;
export const SESSION_CHUNK_TIMEOUT_MS = 600000;

const RESULT_ID_PATTERN = /^[0-9a-f]{32}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

export function normalizeBaseUrl(input) {
  const trimmed = String(input ?? '').trim();
  if (!trimmed) {
    throw createAppError('missing_settings', messageForErrorCode('missing_settings'));
  }
  if (!trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
    throw createAppError('invalid_url', messageForErrorCode('invalid_url'));
  }
  return trimmed.replace(/\/+$/, '');
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
  const requestHeaders = {
    Accept: 'application/json',
    ...headers,
    ...(requiresAuth ? createAuthHeaders(apiToken) : {}),
  };

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetchImpl(joinApiUrl(baseUrl, path), {
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
  const apiToken = String(settings?.apiToken ?? '').trim();
  if (!apiToken) {
    throw createAppError('missing_settings', messageForErrorCode('missing_settings'));
  }
  return requestJson({
    baseUrl: settings.backendUrl,
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
  return {
    uri: buildPreviewUrl(baseUrl, assetId),
    headers: createAuthHeaders(apiToken),
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
  return {
    uri: buildProcessedResultUrl(baseUrl, assetId, safeResult.result_id),
    headers: createAuthHeaders(apiToken),
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
  if (Object.prototype.hasOwnProperty.call(safeAsset, 'active_processed_result')) {
    safeAsset.active_processed_result = sanitizeProcessedResult(
      safeAsset.active_processed_result,
      safeAsset.id,
    );
  }
  return safeAsset;
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
