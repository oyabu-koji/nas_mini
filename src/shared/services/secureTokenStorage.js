import * as SecureStore from 'expo-secure-store';

const API_TOKEN_KEY = 'mediavault.apiToken';

export async function getApiToken() {
  return (await SecureStore.getItemAsync(API_TOKEN_KEY)) ?? '';
}

export async function saveApiToken(apiToken) {
  const normalized = String(apiToken ?? '').trim();
  await SecureStore.setItemAsync(API_TOKEN_KEY, normalized);
  return normalized;
}
