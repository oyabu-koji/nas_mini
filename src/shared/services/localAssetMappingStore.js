import AsyncStorage from '@react-native-async-storage/async-storage';

const LOCAL_ASSET_MAPPING_KEY = 'mediavault.localAssetMappings';

async function readMappings() {
  const raw = await AsyncStorage.getItem(LOCAL_ASSET_MAPPING_KEY);
  if (!raw) {
    return {};
  }
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

export async function saveLocalAssetMapping({ backendAssetId, localAssetId }) {
  if (!backendAssetId || !localAssetId) {
    return null;
  }

  const mappings = await readMappings();
  const mapping = {
    backendAssetId,
    localAssetId,
    updatedAt: new Date().toISOString(),
  };
  mappings[String(backendAssetId)] = mapping;
  await AsyncStorage.setItem(LOCAL_ASSET_MAPPING_KEY, JSON.stringify(mappings));
  return mapping;
}

export async function getLocalAssetMapping(backendAssetId) {
  const mappings = await readMappings();
  return mappings[String(backendAssetId)] ?? null;
}

/**
 * @param {number | string} backendAssetId
 * @returns {Promise<{ status: 'available', mapping: object } | { status: 'mapping_unavailable', mapping: null }>}
 */
export async function getLocalAssetMappingState(backendAssetId) {
  const mapping = await getLocalAssetMapping(backendAssetId);
  if (mapping) {
    return { status: 'available', mapping };
  }
  return { status: 'mapping_unavailable', mapping: null };
}
