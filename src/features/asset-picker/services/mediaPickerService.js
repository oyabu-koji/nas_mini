import * as ImagePicker from 'expo-image-picker';
import * as FileSystem from 'expo-file-system/legacy';

import { ASSET_TYPE } from '../../../shared/constants/assetStatuses';

export async function pickSingleMediaAsset() {
  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) {
    return {
      canceled: true,
      reason: 'permission_denied',
    };
  }

  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images', 'videos'],
    allowsMultipleSelection: false,
    allowsEditing: false,
    exif: true,
    quality: 1,
  });

  if (result.canceled || !result.assets?.length) {
    return {
      canceled: true,
      reason: 'user_cancelled',
    };
  }

  return {
    canceled: false,
    asset: await normalizePickedAsset(result.assets[0]),
  };
}

async function normalizePickedAsset(asset) {
  const type = normalizeAssetType(asset.type, asset.mimeType);
  const sizeBytes = await resolveFileSize(asset);

  return {
    uri: asset.uri,
    localAssetId: asset.assetId ?? null,
    type,
    filename: asset.fileName || fallbackFilename(type, asset.uri),
    mimeType: asset.mimeType || null,
    sizeBytes,
    durationMs: asset.duration ?? null,
    takenAt: normalizeTakenAtFromExif(asset.exif),
    latitude: extractCoordinate(asset.exif, 'GPSLatitude'),
    longitude: extractCoordinate(asset.exif, 'GPSLongitude'),
    exif: asset.exif ?? null,
  };
}

function normalizeAssetType(type, mimeType) {
  if (type === ASSET_TYPE.IMAGE || type === ASSET_TYPE.VIDEO) {
    return type;
  }
  if (String(mimeType ?? '').startsWith('image/')) {
    return ASSET_TYPE.IMAGE;
  }
  return ASSET_TYPE.VIDEO;
}

async function resolveFileSize(asset) {
  if (Number.isFinite(asset.fileSize)) {
    return asset.fileSize;
  }
  try {
    const info = await FileSystem.getInfoAsync(asset.uri, { size: true });
    return Number.isFinite(info.size) ? info.size : null;
  } catch {
    return null;
  }
}

function fallbackFilename(type, uri) {
  const lastPathPart = String(uri ?? '').split('/').filter(Boolean).pop();
  if (lastPathPart && lastPathPart.includes('.')) {
    return decodeURIComponent(lastPathPart);
  }
  return type === ASSET_TYPE.IMAGE ? 'selected-image.jpg' : 'selected-video.mp4';
}

export function normalizeTakenAtFromExif(exif) {
  const source = exif?.DateTimeOriginal ?? exif?.DateTime;
  if (typeof source !== 'string') {
    return null;
  }
  const match = /^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(source);
  if (!match || !isValidExifDateTime(match)) {
    return null;
  }

  const normalized = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}`;
  const offset = normalizeExifOffset(exif?.OffsetTimeOriginal);
  return offset ? `${normalized}${offset}` : normalized;
}

function isValidExifDateTime(match) {
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number);
  const candidate = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return (
    candidate.getUTCFullYear() === year &&
    candidate.getUTCMonth() === month - 1 &&
    candidate.getUTCDate() === day &&
    candidate.getUTCHours() === hour &&
    candidate.getUTCMinutes() === minute &&
    candidate.getUTCSeconds() === second
  );
}

function normalizeExifOffset(value) {
  if (value === 'Z') {
    return 'Z';
  }
  if (typeof value !== 'string') {
    return null;
  }
  const match = /^([+-])(\d{2}):(\d{2})$/.exec(value);
  if (!match) {
    return null;
  }
  const hours = Number(match[2]);
  const minutes = Number(match[3]);
  return hours <= 23 && minutes <= 59 ? value : null;
}

function extractCoordinate(exif, key) {
  const value = exif?.[key];
  return Number.isFinite(value) ? value : null;
}
