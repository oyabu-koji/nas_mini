import AsyncStorage from '@react-native-async-storage/async-storage';

import { createAppError, messageForErrorCode } from '../../../shared/utils/errors';

export const PROCESSED_RESULT_SAVE_STORE_KEY = 'mediavault.processedResultSaves';

const RESULT_ID_PATTERN = /^[0-9a-f]{32}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const ERROR_CODE_PATTERN = /^[a-z0-9_]{1,100}$/;
const SAVE_STATUSES = new Set(['downloading', 'saved', 'failed', 'unknown']);

export async function getProcessedResultSave({ backendAssetId, backendResultId, resultSha256 }) {
  const records = await readProcessedResultSaves();
  return records[processedResultSaveKey({ backendAssetId, backendResultId, resultSha256 })] ?? null;
}

export async function listProcessedResultSaves() {
  return Object.values(await readProcessedResultSaves());
}

export async function writeProcessedResultDownload({ backendAssetId, backendResultId, resultSha256 }) {
  return writeProcessedResultSave({
    backendAssetId,
    backendResultId,
    resultSha256,
    saveStatus: 'downloading',
  });
}

export async function writeUnknownProcessedResultSave({
  backendAssetId,
  backendResultId,
  resultSha256,
  saveAttemptedAt = new Date().toISOString(),
}) {
  return writeProcessedResultSave({
    backendAssetId,
    backendResultId,
    resultSha256,
    saveStatus: 'unknown',
    saveAttemptedAt,
  });
}

export async function markProcessedResultSaved({
  backendAssetId,
  backendResultId,
  resultSha256,
  savedLocalAssetIdentifier,
}) {
  return writeProcessedResultSave({
    backendAssetId,
    backendResultId,
    resultSha256,
    saveStatus: 'saved',
    savedLocalAssetIdentifier,
  });
}

export async function markProcessedResultFailed({
  backendAssetId,
  backendResultId,
  resultSha256,
  lastErrorCode,
}) {
  return writeProcessedResultSave({
    backendAssetId,
    backendResultId,
    resultSha256,
    saveStatus: 'failed',
    lastErrorCode,
  });
}

export async function writeProcessedResultSave(input) {
  const normalized = normalizeRecord(input);
  const key = processedResultSaveKey(normalized);
  const records = await readProcessedResultSaves();
  const existing = records[key] ?? null;
  if (!isAllowedTransition(existing?.saveStatus, normalized.saveStatus)) {
    throw createAppError(
      'processed_result_save_state_unavailable',
      messageForErrorCode('processed_result_save_state_unavailable'),
    );
  }

  const record = {
    ...existing,
    ...normalized,
    savedLocalAssetIdentifier:
      normalized.saveStatus === 'saved'
        ? normalized.savedLocalAssetIdentifier
        : existing?.savedLocalAssetIdentifier ?? null,
    saveAttemptedAt:
      normalized.saveStatus === 'unknown'
        ? normalized.saveAttemptedAt
        : existing?.saveAttemptedAt ?? null,
    lastErrorCode: normalized.lastErrorCode ?? null,
    updatedAt: new Date().toISOString(),
  };
  records[key] = record;
  await AsyncStorage.setItem(PROCESSED_RESULT_SAVE_STORE_KEY, JSON.stringify(records));
  return record;
}

export function processedResultSaveKey({ backendAssetId, backendResultId, resultSha256 }) {
  const assetId = normalizeBackendAssetId(backendAssetId);
  const resultId = normalizeResultId(backendResultId);
  const sha256 = normalizeSha256(resultSha256);
  return `${assetId}:${resultId}:${sha256}`;
}

async function readProcessedResultSaves() {
  const raw = await AsyncStorage.getItem(PROCESSED_RESULT_SAVE_STORE_KEY);
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(([key, value]) => key === safeRecordKey(value)),
    );
  } catch {
    return {};
  }
}

function safeRecordKey(value) {
  try {
    return processedResultSaveKey(value);
  } catch {
    return null;
  }
}

function normalizeRecord(input) {
  const saveStatus = String(input?.saveStatus ?? '');
  if (!SAVE_STATUSES.has(saveStatus)) {
    throw createAppError(
      'processed_result_save_state_unavailable',
      messageForErrorCode('processed_result_save_state_unavailable'),
    );
  }

  const record = {
    backendAssetId: normalizeBackendAssetId(input?.backendAssetId),
    backendResultId: normalizeResultId(input?.backendResultId),
    resultSha256: normalizeSha256(input?.resultSha256),
    saveStatus,
    savedLocalAssetIdentifier: null,
    saveAttemptedAt: null,
    lastErrorCode: normalizeErrorCode(input?.lastErrorCode),
  };
  if (saveStatus === 'saved') {
    record.savedLocalAssetIdentifier = normalizeLocalAssetIdentifier(input?.savedLocalAssetIdentifier);
  }
  if (saveStatus === 'unknown') {
    record.saveAttemptedAt = normalizeIsoDate(input?.saveAttemptedAt);
  }
  return record;
}

function normalizeBackendAssetId(value) {
  const numericValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isSafeInteger(numericValue) || numericValue <= 0) {
    throw createAppError(
      'processed_result_invalid_identity',
      messageForErrorCode('processed_result_invalid_identity'),
    );
  }
  return numericValue;
}

function normalizeResultId(value) {
  const normalized = String(value ?? '');
  if (!RESULT_ID_PATTERN.test(normalized)) {
    throw createAppError(
      'processed_result_invalid_identity',
      messageForErrorCode('processed_result_invalid_identity'),
    );
  }
  return normalized;
}

function normalizeSha256(value) {
  const normalized = String(value ?? '');
  if (!SHA256_PATTERN.test(normalized)) {
    throw createAppError(
      'processed_result_invalid_identity',
      messageForErrorCode('processed_result_invalid_identity'),
    );
  }
  return normalized;
}

function normalizeLocalAssetIdentifier(value) {
  const normalized = String(value ?? '').trim();
  if (!normalized || normalized.length > 256) {
    throw createAppError(
      'processed_result_save_state_unavailable',
      messageForErrorCode('processed_result_save_state_unavailable'),
    );
  }
  return normalized;
}

function normalizeIsoDate(value) {
  const normalized = String(value ?? '').trim();
  if (!normalized || Number.isNaN(Date.parse(normalized))) {
    throw createAppError(
      'processed_result_save_state_unavailable',
      messageForErrorCode('processed_result_save_state_unavailable'),
    );
  }
  return normalized;
}

function normalizeErrorCode(value) {
  if (value == null) {
    return null;
  }
  const normalized = String(value);
  if (!ERROR_CODE_PATTERN.test(normalized)) {
    throw createAppError(
      'processed_result_save_state_unavailable',
      messageForErrorCode('processed_result_save_state_unavailable'),
    );
  }
  return normalized;
}

function isAllowedTransition(previousStatus, nextStatus) {
  if (!previousStatus) {
    return nextStatus === 'downloading' || nextStatus === 'unknown' || nextStatus === 'failed';
  }
  const allowed = {
    downloading: new Set(['downloading', 'unknown', 'failed']),
    unknown: new Set(['unknown', 'saved', 'failed', 'downloading']),
    failed: new Set(['failed', 'downloading', 'unknown']),
    saved: new Set(['saved', 'downloading']),
  };
  return allowed[previousStatus]?.has(nextStatus) ?? false;
}
