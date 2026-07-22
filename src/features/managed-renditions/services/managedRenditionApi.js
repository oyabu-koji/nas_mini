import { requestJson } from '../../../shared/api/mediaVaultApi';
import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

const ID_PATTERN = /^[0-9a-f]{32}$/;
const PRESET_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const PRESET_KINDS = new Set(['compress-only', 'generated-identity', 'generated-test', 'custom']);
const RENDITION_STATES = new Set([
  'queued', 'validating', 'rendering', 'finalizing', 'ready', 'failed', 'superseded',
]);
const TRANSFORM_STATES = new Set(['not_requested', 'unavailable', 'applied', 'failed']);

export async function getManagedCapabilities(settings) {
  return sanitizeCapabilities(await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: '/api/v1/capabilities',
  }));
}

export async function listManagedPresets(settings) {
  return sanitizePresetCatalog(await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: '/api/v1/presets',
  }));
}

export async function createManagedRendition({ settings, assetId, clientRequestId, presetId }) {
  return sanitizeRendition(await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: `/api/v1/assets/${normalizeAssetId(assetId)}/renditions`,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_rendition_request_id: normalizeId(clientRequestId),
      preset_id: normalizePresetId(presetId),
    }),
  }), assetId);
}

export async function getManagedRendition({ settings, assetId, renditionId }) {
  return sanitizeRendition(await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: `/api/v1/assets/${normalizeAssetId(assetId)}/renditions/${normalizeId(renditionId)}`,
  }), assetId);
}

export function sanitizeCapabilities(value) {
  const features = value?.features;
  if (
    !value || value.api_version !== 'v1' || !features
    || typeof features.processed_result_delivery !== 'boolean'
    || typeof features.managed_preview_presets !== 'boolean'
    || typeof features.custom_lut !== 'boolean'
    || typeof features.generated_apple_log_conversion !== 'boolean'
    || typeof features.numeric_rendition_progress !== 'boolean'
    || (value.minimum_client_version != null && typeof value.minimum_client_version !== 'string')
  ) {
    throw domainError('managed_capabilities_invalid');
  }
  if (!features.managed_preview_presets) {
    throw domainError('incompatible_client');
  }
  return {
    apiVersion: 'v1',
    minimumClientVersion: value.minimum_client_version,
    features: {
      processedResultDelivery: features.processed_result_delivery,
      managedPreviewPresets: features.managed_preview_presets,
      customLut: features.custom_lut,
      generatedAppleLogConversion: features.generated_apple_log_conversion,
      numericRenditionProgress: features.numeric_rendition_progress,
    },
  };
}

export function sanitizePresetCatalog(value) {
  if (!Array.isArray(value?.items)) {
    throw domainError('managed_catalog_invalid');
  }
  const items = value.items.map(sanitizePreset).filter(Boolean);
  const ids = new Set(items.map((item) => item.presetId));
  const compressItems = items.filter((item) => item.presetId === 'compress-only');
  if (compressItems.length !== 1 || ids.size !== items.length || compressItems[0].presetKind !== 'compress-only') {
    throw domainError('managed_catalog_invalid');
  }
  return items;
}

export function sanitizePreset(value) {
  if (!value || !PRESET_KINDS.has(value.preset_kind)) {
    return null;
  }
  const presetId = normalizePresetIdOrNull(value.preset_id);
  if (
    !presetId || value.enabled !== true || value.available !== true
    || !safeText(value.display_name, 120)
    || !safeText(value.version, 64)
    || !safeText(value.source_reference, 256)
    || !safeText(value.terms_reference, 256)
    || (value.target_color_space != null && !safeText(value.target_color_space, 64))
  ) {
    return null;
  }
  return {
    presetId,
    displayName: value.display_name,
    presetKind: value.preset_kind,
    version: value.version,
    targetColorSpace: value.target_color_space ?? null,
    sourceReference: value.source_reference,
    termsReference: value.terms_reference,
  };
}

export function sanitizeRendition(value, expectedAssetId) {
  const assetId = normalizeAssetId(expectedAssetId);
  if (
    !value || !ID_PATTERN.test(value.rendition_id)
    || value.asset_id !== assetId
    || !ID_PATTERN.test(value.client_rendition_request_id)
    || !Number.isSafeInteger(value.selection_generation) || value.selection_generation < 1
    || !normalizePresetIdOrNull(value.requested_preset_id)
    || !RENDITION_STATES.has(value.state)
    || (value.applied_preset_id != null && !normalizePresetIdOrNull(value.applied_preset_id))
    || (value.color_transform_status != null && !TRANSFORM_STATES.has(value.color_transform_status))
    || (value.error_code != null && !safeText(value.error_code, 100))
    || (value.result_id != null && !ID_PATTERN.test(value.result_id))
    || !safeText(value.created_at, 100) || !safeText(value.updated_at, 100)
  ) {
    throw domainError('managed_rendition_invalid');
  }
  const terminal = ['ready', 'failed', 'superseded'].includes(value.state);
  if (
    (['ready', 'superseded'].includes(value.state) && (!value.result_id || !value.applied_preset_id))
    || (value.state === 'failed' && (!value.error_code || value.result_id != null))
    || (!terminal && (value.result_id != null || value.error_code != null))
  ) {
    throw domainError('managed_rendition_invalid');
  }
  return {
    renditionId: value.rendition_id,
    assetId,
    clientRequestId: value.client_rendition_request_id,
    selectionGeneration: value.selection_generation,
    requestedPresetId: value.requested_preset_id,
    appliedPresetId: value.applied_preset_id ?? null,
    state: value.state,
    colorTransformStatus: value.color_transform_status ?? null,
    errorCode: value.error_code ?? null,
    resultId: value.result_id ?? null,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  };
}

function normalizeAssetId(value) {
  const normalized = Number(value);
  if (!Number.isSafeInteger(normalized) || normalized <= 0) {
    throw domainError('managed_rendition_invalid');
  }
  return normalized;
}

function normalizeId(value) {
  const normalized = String(value ?? '').toLowerCase();
  if (!ID_PATTERN.test(normalized)) {
    throw domainError('managed_request_id_unavailable');
  }
  return normalized;
}

function normalizePresetId(value) {
  const normalized = normalizePresetIdOrNull(value);
  if (!normalized) {
    throw domainError('managed_catalog_invalid');
  }
  return normalized;
}

function normalizePresetIdOrNull(value) {
  const normalized = String(value ?? '');
  return normalized.length <= 64 && PRESET_ID_PATTERN.test(normalized) ? normalized : null;
}

function safeText(value, maximum) {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum && !/[\u0000-\u001f\u007f]/.test(value);
}

function domainError(code) {
  return createAppError(code, messageForErrorCode(code));
}
