import { normalizeTakenAtFromExif } from './mediaPickerService';

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
