import { deleteOriginalAsset } from './originalDeletionMediaLibraryService';

function mediaLibrary(overrides = {}) {
  return {
    requestPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
    deleteAssetsAsync: jest.fn().mockResolvedValue(true),
    ...overrides,
  };
}

describe('originalDeletionMediaLibraryService', () => {
  it('deletes only the validated mapped local asset', async () => {
    const library = mediaLibrary();

    await expect(deleteOriginalAsset({
      localAssetId: 'local-video-42',
      mediaLibrary: library,
    })).resolves.toEqual({ status: 'deleted' });

    expect(library.deleteAssetsAsync).toHaveBeenCalledWith(['local-video-42']);
  });

  it('maps permission, cancellation, unavailable API, and native failures', async () => {
    await expect(deleteOriginalAsset({
      localAssetId: 'local-video',
      mediaLibrary: mediaLibrary({
        requestPermissionsAsync: jest.fn().mockResolvedValue({ status: 'denied' }),
      }),
    })).rejects.toMatchObject({ code: 'original_delete_permission_denied' });
    await expect(deleteOriginalAsset({
      localAssetId: 'local-video',
      mediaLibrary: mediaLibrary({
        deleteAssetsAsync: jest.fn().mockResolvedValue(false),
      }),
    })).rejects.toMatchObject({ code: 'original_delete_cancelled' });
    await expect(deleteOriginalAsset({
      localAssetId: 'local-video',
      mediaLibrary: {},
    })).rejects.toMatchObject({ code: 'original_delete_api_unavailable' });
    await expect(deleteOriginalAsset({
      localAssetId: 'local-video',
      mediaLibrary: mediaLibrary({
        deleteAssetsAsync: jest.fn().mockRejectedValue({
          code: 'E_ASSET_NOT_FOUND',
        }),
      }),
    })).rejects.toMatchObject({ code: 'original_delete_asset_unavailable' });
    await expect(deleteOriginalAsset({
      localAssetId: 'local-video',
      mediaLibrary: mediaLibrary({
        deleteAssetsAsync: jest.fn().mockRejectedValue(new Error('private native details')),
      }),
    })).rejects.toMatchObject({
      code: 'original_delete_failed',
      message: 'The iPhone original could not be deleted.',
    });
    await expect(deleteOriginalAsset({
      localAssetId: 'local-video',
      mediaLibrary: mediaLibrary({
        deleteAssetsAsync: jest.fn().mockRejectedValue({
          code: 'original_delete_private_native_detail',
          message: 'private native details',
        }),
      }),
    })).rejects.toMatchObject({
      code: 'original_delete_failed',
      message: 'The iPhone original could not be deleted.',
    });
  });
});
