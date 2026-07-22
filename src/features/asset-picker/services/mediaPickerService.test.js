import * as FileSystem from 'expo-file-system/legacy';
import * as ImagePicker from 'expo-image-picker';

import { normalizeTakenAtFromExif, pickSingleMediaAsset } from './mediaPickerService';

jest.mock('expo-image-picker', () => ({
  requestMediaLibraryPermissionsAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));
jest.mock('expo-file-system/legacy', () => ({
  getInfoAsync: jest.fn(),
}));

describe('normalizeTakenAtFromExif', () => {
  it('uses DateTimeOriginal before DateTime and keeps a valid offset', () => {
    expect(
      normalizeTakenAtFromExif({
        DateTimeOriginal: '2026:07:11 12:34:56',
        DateTime: '2026:01:01 00:00:00',
        OffsetTimeOriginal: '+09:00',
      }),
    ).toBe('2026-07-11T12:34:56+09:00');
  });

  it('uses DateTime when DateTimeOriginal is absent', () => {
    expect(normalizeTakenAtFromExif({ DateTime: '2026:07:11 12:34:56' })).toBe('2026-07-11T12:34:56');
  });

  it('does not infer or accept invalid datetime and offset values', () => {
    expect(normalizeTakenAtFromExif({ DateTimeOriginal: '2026-07-11T12:34:56' })).toBeNull();
    expect(normalizeTakenAtFromExif({ DateTimeOriginal: '2026:02:30 12:34:56' })).toBeNull();
    expect(
      normalizeTakenAtFromExif({ DateTimeOriginal: '2026:07:11 12:34:56', OffsetTimeOriginal: '+25:00' }),
    ).toBe('2026-07-11T12:34:56');
    expect(normalizeTakenAtFromExif({ OffsetTimeOriginal: '+09:00' })).toBeNull();
  });
});

describe('pickSingleMediaAsset', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    ImagePicker.requestMediaLibraryPermissionsAsync.mockResolvedValue({ granted: true });
  });

  it('returns explicit permission-denied and user-cancelled results', async () => {
    ImagePicker.requestMediaLibraryPermissionsAsync.mockResolvedValueOnce({ granted: false });
    await expect(pickSingleMediaAsset()).resolves.toEqual({
      canceled: true,
      reason: 'permission_denied',
    });
    expect(ImagePicker.launchImageLibraryAsync).not.toHaveBeenCalled();

    ImagePicker.launchImageLibraryAsync.mockResolvedValueOnce({ canceled: true, assets: [] });
    await expect(pickSingleMediaAsset()).resolves.toEqual({
      canceled: true,
      reason: 'user_cancelled',
    });
  });

  it('normalizes picker metadata without a FileSystem lookup when file size is present', async () => {
    ImagePicker.launchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [{
        uri: 'file:///photo.jpg',
        assetId: 'library-42',
        type: 'image',
        fileName: 'photo.jpg',
        mimeType: 'image/jpeg',
        fileSize: 123,
        duration: null,
        exif: {
          DateTimeOriginal: '2026:07:11 12:34:56',
          OffsetTimeOriginal: 'Z',
          GPSLatitude: 35.6,
          GPSLongitude: 139.7,
        },
      }],
    });

    await expect(pickSingleMediaAsset()).resolves.toMatchObject({
      canceled: false,
      asset: {
        localAssetId: 'library-42',
        type: 'image',
        filename: 'photo.jpg',
        sizeBytes: 123,
        takenAt: '2026-07-11T12:34:56Z',
        latitude: 35.6,
        longitude: 139.7,
      },
    });
    expect(FileSystem.getInfoAsync).not.toHaveBeenCalled();
  });

  it('falls back to FileSystem size, MIME type, and a decoded URI filename', async () => {
    FileSystem.getInfoAsync.mockResolvedValue({ exists: true, size: 456 });
    ImagePicker.launchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [{
        uri: 'file:///folder/My%20Clip.mov',
        mimeType: 'video/quicktime',
        exif: { GPSLatitude: 'invalid', GPSLongitude: null },
      }],
    });

    await expect(pickSingleMediaAsset()).resolves.toMatchObject({
      asset: {
        type: 'video',
        filename: 'My Clip.mov',
        sizeBytes: 456,
        latitude: null,
        longitude: null,
      },
    });
    expect(FileSystem.getInfoAsync).toHaveBeenCalledWith('file:///folder/My%20Clip.mov', { size: true });
  });

  it('returns an unknown size and safe default filename when file inspection fails', async () => {
    FileSystem.getInfoAsync.mockRejectedValue(new Error('native details'));
    ImagePicker.launchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///opaque-reference', mimeType: 'image/heic' }],
    });

    await expect(pickSingleMediaAsset()).resolves.toMatchObject({
      asset: {
        type: 'image',
        filename: 'selected-image.jpg',
        sizeBytes: null,
      },
    });
  });
});
