import {
  DEFAULT_REQUEST_TIMEOUT_MS,
  UPLOAD_REQUEST_TIMEOUT_MS,
  buildProcessedResultPath,
  buildProcessedResultSource,
  requestJson,
  sanitizeAsset,
  sanitizeProcessedResult,
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

  it('retains only a canonical processed-result identity on asset detail', () => {
    const resultId = 'a'.repeat(32);
    const asset = sanitizeAsset({
      id: 42,
      original_path: 'originals/private.mov',
      active_processed_result: {
        id: 'internal-id-must-not-escape',
        result_id: resultId,
        mime_type: 'video/mp4',
        size_bytes: 123,
        sha256: 'b'.repeat(64),
        created_at: '2026-07-18T00:00:00Z',
        url: `/assets/42/results/${resultId}`,
      },
    });

    expect(asset.original_path).toBeUndefined();
    expect(asset.active_processed_result).toEqual({
      result_id: resultId,
      mime_type: 'video/mp4',
      size_bytes: 123,
      sha256: 'b'.repeat(64),
      created_at: '2026-07-18T00:00:00Z',
      url: `/assets/42/results/${resultId}`,
    });
  });

  it('rejects absolute, query, fragment, and path-mismatched result URLs before building an authenticated source', () => {
    const resultId = 'c'.repeat(32);
    const baseResult = {
      result_id: resultId,
      mime_type: 'video/mp4',
      size_bytes: 1,
      sha256: 'd'.repeat(64),
      created_at: '2026-07-18T00:00:00Z',
    };
    const unsafeUrls = [
      `https://attacker.invalid/assets/42/results/${resultId}`,
      `/assets/42/results/${resultId}?token=leak`,
      `/assets/42/results/${resultId}#fragment`,
      `/assets/99/results/${resultId}`,
    ];

    unsafeUrls.forEach((url) => {
      expect(sanitizeProcessedResult({ ...baseResult, url }, 42)).toBeNull();
      expect(() =>
        buildProcessedResultSource({
          baseUrl: 'http://backend.test',
          apiToken: 'secret-token',
          assetId: 42,
          result: { ...baseResult, url },
        }),
      ).toThrow('processed video identity');
    });
  });

  it('rebuilds the canonical same-origin result path from validated IDs', () => {
    const resultId = 'e'.repeat(32);

    expect(buildProcessedResultPath(42, resultId)).toBe(`/assets/42/results/${resultId}`);
    expect(
      buildProcessedResultSource({
        baseUrl: 'http://backend.test/',
        apiToken: 'secret-token',
        assetId: 42,
        result: {
          result_id: resultId,
          mime_type: 'video/mp4',
          size_bytes: 1,
          sha256: 'f'.repeat(64),
          created_at: '2026-07-18T00:00:00Z',
          url: `/assets/42/results/${resultId}`,
        },
      }),
    ).toEqual({
      uri: `http://backend.test/assets/42/results/${resultId}`,
      headers: { Authorization: 'Bearer secret-token' },
    });
  });
});
