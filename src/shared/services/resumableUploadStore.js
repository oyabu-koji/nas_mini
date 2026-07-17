import AsyncStorage from '@react-native-async-storage/async-storage';

const RESUMABLE_UPLOAD_KEY = 'mediavault.resumableVideoUpload';

/**
 * @typedef {Object} ResumableUploadRecord
 * @property {string} localAssetId
 * @property {string} clientUploadId
 * @property {string | null} sessionId
 * @property {number} sizeBytes
 * @property {string} expectedFileSha256
 * @property {number} uploadedBytes
 */

/**
 * Persist a resume record before the session create request is sent.
 * @param {ResumableUploadRecord} record
 * @returns {Promise<ResumableUploadRecord>}
 */
export async function saveResumableUploadRecord(record) {
  const normalized = normalizeRecord(record);
  await AsyncStorage.setItem(RESUMABLE_UPLOAD_KEY, JSON.stringify(normalized));
  return normalized;
}

/** @returns {Promise<ResumableUploadRecord | null>} */
export async function readResumableUploadRecord() {
  const raw = await AsyncStorage.getItem(RESUMABLE_UPLOAD_KEY);
  if (!raw) {
    return null;
  }
  try {
    return normalizeRecord(JSON.parse(raw));
  } catch {
    await AsyncStorage.removeItem(RESUMABLE_UPLOAD_KEY);
    return null;
  }
}

export async function updateResumableUploadSessionId(clientUploadId, sessionId) {
  const record = await readMatchingRecord(clientUploadId);
  const updated = normalizeRecord({ ...record, sessionId });
  await AsyncStorage.setItem(RESUMABLE_UPLOAD_KEY, JSON.stringify(updated));
  return updated;
}

export async function updateResumableUploadProgress(clientUploadId, uploadedBytes) {
  const record = await readMatchingRecord(clientUploadId);
  const updated = normalizeRecord({ ...record, uploadedBytes });
  await AsyncStorage.setItem(RESUMABLE_UPLOAD_KEY, JSON.stringify(updated));
  return updated;
}

export async function removeResumableUploadRecord(clientUploadId) {
  const record = await readResumableUploadRecord();
  if (record && (!clientUploadId || record.clientUploadId === clientUploadId)) {
    await AsyncStorage.removeItem(RESUMABLE_UPLOAD_KEY);
  }
}

async function readMatchingRecord(clientUploadId) {
  const record = await readResumableUploadRecord();
  if (!record || record.clientUploadId !== clientUploadId) {
    throw new Error('resumable upload record is unavailable');
  }
  return record;
}

function normalizeRecord(value) {
  if (!value || typeof value !== 'object') {
    throw new Error('resumable upload record is invalid');
  }
  const localAssetId = normalizeText(value.localAssetId);
  const clientUploadId = normalizeText(value.clientUploadId);
  const sessionId = value.sessionId == null ? null : normalizeText(value.sessionId);
  const expectedFileSha256 = String(value.expectedFileSha256 ?? '').toLowerCase();
  const sizeBytes = Number(value.sizeBytes);
  const uploadedBytes = Number(value.uploadedBytes ?? 0);
  if (
    !localAssetId || !clientUploadId || !/^[0-9a-f]{64}$/.test(expectedFileSha256)
    || !Number.isSafeInteger(sizeBytes) || sizeBytes <= 0
    || !Number.isSafeInteger(uploadedBytes) || uploadedBytes < 0 || uploadedBytes > sizeBytes
  ) {
    throw new Error('resumable upload record is invalid');
  }
  return {
    localAssetId,
    clientUploadId,
    sessionId,
    sizeBytes,
    expectedFileSha256,
    uploadedBytes,
  };
}

function normalizeText(value) {
  const normalized = String(value ?? '').trim();
  return normalized || null;
}
