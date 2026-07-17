import {
  DEFAULT_REQUEST_TIMEOUT_MS,
  UPLOAD_REQUEST_TIMEOUT_MS,
  requestJson,
  uploadAsset,
} from './mediaVaultApi';

describe('mediaVaultApi timeouts', () => {
  beforeEach(() => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      text: jest.fn().mockResolvedValue('{}'),
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('keeps the default timeout for ordinary JSON requests', async () => {
    const setTimeoutSpy = jest.spyOn(global, 'setTimeout');

    await requestJson({
      baseUrl: 'http://backend.test',
      path: '/assets',
      requiresAuth: false,
    });

    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), DEFAULT_REQUEST_TIMEOUT_MS);
  });

  it('uses the longer timeout only for uploads', async () => {
    const setTimeoutSpy = jest.spyOn(global, 'setTimeout');

    await uploadAsset({
      settings: { backendUrl: 'http://backend.test', apiToken: 'masked' },
      pickedAsset: {
        uri: 'media-reference',
        filename: 'clip.mov',
        type: 'video',
        mimeType: 'video/quicktime',
        takenAt: null,
        latitude: null,
        longitude: null,
        exif: null,
      },
      isLog: false,
    });

    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), UPLOAD_REQUEST_TIMEOUT_MS);
  });
});
