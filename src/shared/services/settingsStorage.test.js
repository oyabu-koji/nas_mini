import AsyncStorage from '@react-native-async-storage/async-storage';

import { getBackendUrl, saveBackendUrl } from './settingsStorage';

describe('settingsStorage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns an empty URL when AsyncStorage has no value', async () => {
    AsyncStorage.getItem.mockResolvedValue(null);

    await expect(getBackendUrl()).resolves.toBe('');
    expect(AsyncStorage.getItem).toHaveBeenCalledWith('mediavault.backendUrl');
  });

  it('normalizes and persists an accepted backend URL', async () => {
    AsyncStorage.setItem.mockResolvedValue();

    await expect(saveBackendUrl('  http://100.64.0.1:8000/  ')).resolves.toBe('http://100.64.0.1:8000');
    expect(AsyncStorage.setItem).toHaveBeenCalledWith(
      'mediavault.backendUrl',
      'http://100.64.0.1:8000',
    );
  });

  it('rejects an invalid URL without writing AsyncStorage', async () => {
    await expect(saveBackendUrl('http://public.example.com')).rejects.toMatchObject({
      code: 'invalid_url',
    });
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
  });

  it('propagates AsyncStorage read and write failures', async () => {
    AsyncStorage.getItem.mockRejectedValueOnce(new Error('read unavailable'));
    AsyncStorage.setItem.mockRejectedValueOnce(new Error('write unavailable'));

    await expect(getBackendUrl()).rejects.toThrow('read unavailable');
    await expect(saveBackendUrl('http://mediavault')).rejects.toThrow('write unavailable');
  });
});
