import AsyncStorage from '@react-native-async-storage/async-storage';
import { uuid } from 'expo-modules-core';

import { sanitizeRendition } from './managedRenditionApi';

const KEY_PREFIX = 'mediavault.managedRendition.v1';
const ID_PATTERN = /^[0-9a-f]{32}$/;
const PRESET_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function generateClientRenditionRequestId(randomUUIDImpl = uuid.v4) {
  if (typeof randomUUIDImpl !== 'function') {
    throw new Error('secure UUID API is unavailable');
  }
  const normalized = String(randomUUIDImpl()).replaceAll('-', '').toLowerCase();
  if (!ID_PATTERN.test(normalized)) {
    throw new Error('secure UUID API returned an invalid value');
  }
  return normalized;
}

export async function readManagedRenditionRecord(assetId) {
  const key = storageKey(assetId);
  const raw = await AsyncStorage.getItem(key);
  if (!raw) {
    return null;
  }
  try {
    return normalizeRecord(JSON.parse(raw), normalizeAssetId(assetId));
  } catch {
    await AsyncStorage.removeItem(key);
    return null;
  }
}

export async function writePendingManagedRendition({
  assetId,
  clientRequestId,
  requestedPresetId,
  selectionSequence,
  now = () => new Date().toISOString(),
}) {
  const normalizedAssetId = normalizeAssetId(assetId);
  const record = normalizeRecord({
    assetId: normalizedAssetId,
    clientRequestId,
    requestedPresetId,
    renditionId: null,
    selectionSequence,
    rendition: null,
    createdAt: now(),
    updatedAt: now(),
  }, normalizedAssetId);
  await AsyncStorage.setItem(storageKey(normalizedAssetId), JSON.stringify(record));
  return record;
}

export async function updateManagedRenditionRecord({
  assetId,
  clientRequestId,
  selectionSequence,
  rendition,
  now = () => new Date().toISOString(),
}) {
  const current = await readManagedRenditionRecord(assetId);
  if (
    !current
    || current.clientRequestId !== clientRequestId
    || current.selectionSequence !== selectionSequence
  ) {
    throw new Error('managed rendition record identity changed');
  }
  const safeRendition = rendition?.renditionId
    ? normalizeStoredRendition(rendition, current.assetId)
    : sanitizeRendition(rendition, current.assetId);
  if (
    safeRendition.clientRequestId !== current.clientRequestId
    || safeRendition.requestedPresetId !== current.requestedPresetId
  ) {
    throw new Error('managed rendition response identity changed');
  }
  const updated = normalizeRecord({
    ...current,
    renditionId: safeRendition.renditionId,
    rendition: safeRendition,
    updatedAt: now(),
  }, current.assetId);
  await AsyncStorage.setItem(storageKey(current.assetId), JSON.stringify(updated));
  return updated;
}

export async function removeManagedRenditionRecord(assetId) {
  await AsyncStorage.removeItem(storageKey(assetId));
}

function normalizeRecord(value, expectedAssetId) {
  if (!value || typeof value !== 'object') {
    throw new Error('managed rendition record is invalid');
  }
  const assetId = normalizeAssetId(value.assetId);
  const clientRequestId = String(value.clientRequestId ?? '');
  const requestedPresetId = String(value.requestedPresetId ?? '');
  const renditionId = value.renditionId == null ? null : String(value.renditionId);
  const selectionSequence = Number(value.selectionSequence);
  if (
    assetId !== expectedAssetId
    || !ID_PATTERN.test(clientRequestId)
    || !PRESET_ID_PATTERN.test(requestedPresetId) || requestedPresetId.length > 64
    || (renditionId != null && !ID_PATTERN.test(renditionId))
    || !Number.isSafeInteger(selectionSequence) || selectionSequence < 1
    || typeof value.createdAt !== 'string' || !value.createdAt
    || typeof value.updatedAt !== 'string' || !value.updatedAt
  ) {
    throw new Error('managed rendition record is invalid');
  }
  const rendition = value.rendition == null ? null : normalizeStoredRendition(value.rendition, assetId);
  if (
    rendition
    && (
      rendition.renditionId !== renditionId
      || rendition.clientRequestId !== clientRequestId
      || rendition.requestedPresetId !== requestedPresetId
    )
  ) {
    throw new Error('managed rendition record is invalid');
  }
  return {
    assetId,
    clientRequestId,
    requestedPresetId,
    renditionId,
    selectionSequence,
    rendition,
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
  };
}

function normalizeStoredRendition(value, assetId) {
  return sanitizeRendition({
    rendition_id: value.renditionId,
    asset_id: value.assetId,
    client_rendition_request_id: value.clientRequestId,
    selection_generation: value.selectionGeneration,
    requested_preset_id: value.requestedPresetId,
    applied_preset_id: value.appliedPresetId,
    state: value.state,
    color_transform_status: value.colorTransformStatus,
    error_code: value.errorCode,
    result_id: value.resultId,
    created_at: value.createdAt,
    updated_at: value.updatedAt,
  }, assetId);
}

function storageKey(assetId) {
  return `${KEY_PREFIX}.${normalizeAssetId(assetId)}`;
}

function normalizeAssetId(value) {
  const normalized = Number(value);
  if (!Number.isSafeInteger(normalized) || normalized <= 0) {
    throw new Error('managed rendition asset ID is invalid');
  }
  return normalized;
}
