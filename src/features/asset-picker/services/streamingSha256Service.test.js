import { createAppError } from '../../../shared/utils/errors';
import { hashFileRange, hashWholeFile } from './streamingSha256Service';

jest.mock('../../../../modules/streaming-sha256/src', () => ({
  sha256File: jest.fn(),
  sha256Range: jest.fn(),
}));

const nativeHash = require('../../../../modules/streaming-sha256/src');

describe('streamingSha256Service', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('uses the native whole-file and range APIs without loading video bytes in JavaScript', async () => {
    nativeHash.sha256File.mockResolvedValue('A'.repeat(64));
    nativeHash.sha256Range.mockResolvedValue('b'.repeat(64));

    await expect(hashWholeFile('file:///video.mov')).resolves.toBe('a'.repeat(64));
    await expect(hashFileRange('file:///video.mov', 8, 16)).resolves.toBe('b'.repeat(64));

    expect(nativeHash.sha256File).toHaveBeenCalledWith('file:///video.mov');
    expect(nativeHash.sha256Range).toHaveBeenCalledWith('file:///video.mov', 8, 16);
  });

  it('rejects invalid input and invalid native digests', async () => {
    nativeHash.sha256File.mockResolvedValue('not-a-digest');

    await expect(hashWholeFile('')).rejects.toMatchObject({ code: 'media_unavailable' });
    await expect(hashFileRange('file:///video.mov', -1, 8)).rejects.toMatchObject({ code: 'native_hash_invalid_range' });
    await expect(hashWholeFile('file:///video.mov')).rejects.toMatchObject({ code: 'native_hash_invalid_result' });
  });

  it('maps missing native module errors to a Development Build requirement', async () => {
    nativeHash.sha256File.mockRejectedValue(new Error('native module missing'));
    nativeHash.sha256Range.mockRejectedValue(createAppError('native_hash_invalid_range', 'invalid'));

    await expect(hashWholeFile('file:///video.mov')).rejects.toMatchObject({ code: 'native_hash_unavailable' });
    await expect(hashFileRange('file:///video.mov', 0, 8)).rejects.toMatchObject({ code: 'native_hash_invalid_range' });
  });
});
