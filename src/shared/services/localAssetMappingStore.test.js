import AsyncStorage from '@react-native-async-storage/async-storage';

import { getLocalAssetMappingState } from './localAssetMappingStore';

describe('getLocalAssetMappingState', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('reports mapping_unavailable without inferring a local asset', async () => {
    AsyncStorage.getItem.mockResolvedValueOnce('{}');

    await expect(getLocalAssetMappingState(42)).resolves.toEqual({
      status: 'mapping_unavailable',
      mapping: null,
    });
  });
});
