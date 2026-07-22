import * as SecureStore from 'expo-secure-store';

import { getApiToken, saveApiToken } from './secureTokenStorage';

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
}));

describe('secureTokenStorage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns an empty token when SecureStore has no value', async () => {
    SecureStore.getItemAsync.mockResolvedValue(null);

    await expect(getApiToken()).resolves.toBe('');
    expect(SecureStore.getItemAsync).toHaveBeenCalledWith('mediavault.apiToken');
  });

  it('trims and persists replacement tokens', async () => {
    SecureStore.setItemAsync.mockResolvedValue();

    await expect(saveApiToken('  replacement-token  ')).resolves.toBe('replacement-token');
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith('mediavault.apiToken', 'replacement-token');
  });

  it('propagates SecureStore read and write failures', async () => {
    SecureStore.getItemAsync.mockRejectedValueOnce(new Error('read unavailable'));
    SecureStore.setItemAsync.mockRejectedValueOnce(new Error('write unavailable'));

    await expect(getApiToken()).rejects.toThrow('read unavailable');
    await expect(saveApiToken('token')).rejects.toThrow('write unavailable');
  });
});
