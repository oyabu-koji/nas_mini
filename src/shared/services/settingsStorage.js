import AsyncStorage from '@react-native-async-storage/async-storage';

import { validateAndNormalizeBackendUrl } from './backendEndpointPolicy';

const BACKEND_URL_KEY = 'mediavault.backendUrl';

export async function getBackendUrl() {
  return (await AsyncStorage.getItem(BACKEND_URL_KEY)) ?? '';
}

export async function saveBackendUrl(backendUrl) {
  const normalized = validateAndNormalizeBackendUrl(backendUrl);
  await AsyncStorage.setItem(BACKEND_URL_KEY, normalized);
  return normalized;
}
