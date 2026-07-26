import AsyncStorage from '@react-native-async-storage/async-storage';

import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

export const ORIGINAL_DELETION_STORE_KEY = 'mediavault.originalDeletionOutcomes.v1';
const PERSISTED_FAILURE_CODES = new Set([
  'original_delete_permission_denied',
  'original_delete_cancelled',
  'original_delete_asset_unavailable',
  'original_delete_api_unavailable',
  'original_delete_failed',
]);

export async function readOriginalDeletionOutcome(backendAssetId) {
  const assetId = normalizeAssetId(backendAssetId);
  try {
    const records = await readRecords();
    return sanitizeRecord(records[String(assetId)], assetId);
  } catch (error) {
    if (error?.code) {
      throw error;
    }
    throw storeError();
  }
}

export async function writeOriginalDeletionOutcome({
  backendAssetId,
  status,
  errorCode = null,
  now = () => new Date(),
}) {
  const assetId = normalizeAssetId(backendAssetId);
  if (
    !['deleted', 'failed'].includes(status)
    || (status === 'deleted' && errorCode != null)
    || (status === 'failed' && !isStableErrorCode(errorCode))
  ) {
    throw storeError();
  }
  const record = {
    backendAssetId: assetId,
    status,
    errorCode,
    updatedAt: now().toISOString(),
  };
  try {
    const records = await readRecords();
    records[String(assetId)] = record;
    await AsyncStorage.setItem(ORIGINAL_DELETION_STORE_KEY, JSON.stringify(records));
    return record;
  } catch (error) {
    if (error?.code) {
      throw error;
    }
    throw storeError();
  }
}

async function readRecords() {
  const raw = await AsyncStorage.getItem(ORIGINAL_DELETION_STORE_KEY);
  if (!raw) {
    return {};
  }
  const value = JSON.parse(raw);
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw storeError();
  }
  return value;
}

function sanitizeRecord(value, assetId) {
  if (value == null) {
    return null;
  }
  if (
    value.backendAssetId !== assetId
    || !['deleted', 'failed'].includes(value.status)
    || typeof value.updatedAt !== 'string'
    || !value.updatedAt
    || (
      value.status === 'deleted'
        ? value.errorCode != null
        : !isStableErrorCode(value.errorCode)
    )
  ) {
    throw storeError();
  }
  return {
    backendAssetId: assetId,
    status: value.status,
    errorCode: value.errorCode ?? null,
    updatedAt: value.updatedAt,
  };
}

function normalizeAssetId(value) {
  const normalized = Number(value);
  if (!Number.isSafeInteger(normalized) || normalized <= 0) {
    throw storeError();
  }
  return normalized;
}

function isStableErrorCode(value) {
  return PERSISTED_FAILURE_CODES.has(value);
}

function storeError() {
  return createAppError(
    'original_delete_state_unavailable',
    messageForErrorCode('original_delete_state_unavailable'),
  );
}
