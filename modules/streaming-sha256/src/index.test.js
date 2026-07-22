import { requireNativeModule } from 'expo';

import { sha256File, sha256Range } from './index';

jest.mock('expo', () => ({
  requireNativeModule: jest.fn(),
}));

describe('streaming-sha256 native bridge', () => {
  const nativeModule = {
    sha256File: jest.fn(),
    sha256Range: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
    requireNativeModule.mockReturnValue(nativeModule);
  });

  it('loads the expected native module and forwards whole-file arguments', async () => {
    nativeModule.sha256File.mockResolvedValue('a'.repeat(64));

    await expect(sha256File('file:///video.mov')).resolves.toBe('a'.repeat(64));
    expect(requireNativeModule).toHaveBeenCalledWith('StreamingSha256');
    expect(nativeModule.sha256File).toHaveBeenCalledWith('file:///video.mov');
  });

  it('forwards exact range arguments and propagates native rejection', async () => {
    nativeModule.sha256Range.mockResolvedValueOnce('b'.repeat(64));
    await expect(sha256Range('file:///video.mov', 8, 16)).resolves.toBe('b'.repeat(64));
    expect(nativeModule.sha256Range).toHaveBeenCalledWith('file:///video.mov', 8, 16);

    const nativeError = new Error('native range failed');
    nativeModule.sha256Range.mockRejectedValueOnce(nativeError);
    await expect(sha256Range('file:///video.mov', 0, 4)).rejects.toBe(nativeError);
  });
});
