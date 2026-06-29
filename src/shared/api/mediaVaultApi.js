import {
  createAppError,
  createHttpError,
  createNetworkError,
  createTimeoutError,
  messageForErrorCode,
} from '../utils/errors';

const DEFAULT_REQUEST_TIMEOUT_MS = 15000;

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
    response = await fetch(joinApiUrl(baseUrl, path), {
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
    throw createHttpError(response.status);
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
  });

  return sanitizeUploadResponse(payload);
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
  return safeAsset;
}
