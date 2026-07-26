import {
  DEFAULT_REQUEST_TIMEOUT_MS,
  UPLOAD_REQUEST_TIMEOUT_MS,
  buildProcessedResultPath,
  buildProcessedResultSource,
  requestJson,
  sanitizeAsset,
  sanitizeFormalPreview,
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
    jest.useRealTimers();
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

  it('maps transport and timeout failures without exposing adapter details', async () => {
    const networkFetch = jest.fn().mockRejectedValue(new Error('private network detail'));
    await expect(
      requestJson({
        baseUrl: 'http://backend.test',
        path: '/assets',
        requiresAuth: false,
        fetchImpl: networkFetch,
      }),
    ).rejects.toMatchObject({ code: 'network_unreachable', retryable: true });

    jest.useFakeTimers();
    const timeoutFetch = jest.fn((_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        const error = new Error('private timeout detail');
        error.name = 'AbortError';
        reject(error);
      });
    }));
    const pendingTimeout = expect(
      requestJson({
        baseUrl: 'http://backend.test',
        path: '/assets',
        requiresAuth: false,
        timeoutMs: 10,
        fetchImpl: timeoutFetch,
      }),
    ).rejects.toMatchObject({ code: 'timeout', retryable: true });
    await jest.advanceTimersByTimeAsync(10);
    await pendingTimeout;
  });

  it('preserves a stable server error code and retryability on HTTP failure', async () => {
    const fetchImpl = jest.fn().mockResolvedValue({
      ok: false,
      status: 409,
      text: jest.fn().mockResolvedValue(JSON.stringify({
        code: 'rendition_precondition_changed',
        retryable: true,
      })),
    });

    await expect(
      requestJson({
        baseUrl: 'http://backend.test',
        apiToken: 'token',
        path: '/renditions',
        fetchImpl,
      }),
    ).rejects.toMatchObject({
      code: 'rendition_precondition_changed',
      status: 409,
      retryable: true,
      message: 'The active processed video changed. Retry the same request.',
    });
  });

  it('returns null for a successful response with malformed JSON', async () => {
    const fetchImpl = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: jest.fn().mockResolvedValue('{not-json'),
    });

    await expect(
      requestJson({
        baseUrl: 'http://backend.test',
        path: '/health',
        requiresAuth: false,
        fetchImpl,
      }),
    ).resolves.toBeNull();
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
      headers: {
        Authorization: 'Bearer secret-token',
        'X-MediaVault-Client-Version': '0.2.0',
      },
    });
  });

  it('sanitizes formal Apple Log fallback and ordinary preview claims', () => {
    const resultId = 'a'.repeat(32);
    const previewId = 'b'.repeat(32);
    const detector = {
      detection_status: 'apple_log',
      source_profile: null,
      detector_rule_version: 'rule-v1',
      detector_manifest_sha256: 'c'.repeat(64),
      detector_evidence_sha256: 'd'.repeat(64),
    };
    const result = {
      result_id: resultId,
      mime_type: 'video/mp4',
      size_bytes: 12,
      sha256: 'e'.repeat(64),
      created_at: '2026-07-24T00:00:00Z',
      url: `/assets/42/results/${resultId}`,
    };
    const fallback = sanitizeFormalPreview({
      schema_version: 1,
      state: 'ready',
      generation: 1,
      ...detector,
      requested_preset_id: 'generated-apple-log-rec709',
      applied_preset_id: 'compress-only',
      applied_preset_display_name: null,
      preset_version: null,
      manifest_sha256: null,
      lut_sha256: null,
      transform_kind: 'none',
      color_transform_status: 'unavailable',
      color_transform_error_code: 'lut_preset_unavailable',
      preview_id: previewId,
      result,
      failure_code: null,
    }, 42);
    expect(fallback).toMatchObject({
      state: 'ready',
      detection_status: 'apple_log',
      requested_preset_id: 'generated-apple-log-rec709',
      applied_preset_id: 'compress-only',
      color_transform_status: 'unavailable',
      result,
    });

    expect(sanitizeFormalPreview({
      ...fallback,
      detection_status: 'unknown',
      requested_preset_id: 'compress-only',
      applied_preset_id: 'compress-only',
      color_transform_status: 'not_requested',
      color_transform_error_code: null,
    }, 42)).toMatchObject({
      detection_status: 'unknown',
      color_transform_status: 'not_requested',
    });
  });

  it('requires complete future LUT identity and fixed formal failure codes', () => {
    const resultId = 'a'.repeat(32);
    const applied = {
      schema_version: 1,
      state: 'ready',
      generation: 1,
      detection_status: 'apple_log',
      source_profile: null,
      detector_rule_version: 'rule-v1',
      detector_manifest_sha256: 'b'.repeat(64),
      detector_evidence_sha256: 'c'.repeat(64),
      requested_preset_id: 'generated-apple-log-rec709',
      applied_preset_id: 'generated-apple-log-rec709',
      applied_preset_display_name: 'Apple Log to Rec.709',
      preset_version: '1',
      manifest_sha256: 'd'.repeat(64),
      lut_sha256: 'e'.repeat(64),
      transform_kind: 'lut',
      color_transform_status: 'applied',
      color_transform_error_code: null,
      preview_id: 'f'.repeat(32),
      result: {
        result_id: resultId,
        mime_type: 'video/mp4',
        size_bytes: 12,
        sha256: '1'.repeat(64),
        created_at: '2026-07-24T00:00:00Z',
        url: `/assets/42/results/${resultId}`,
      },
      failure_code: null,
    };
    expect(sanitizeFormalPreview(applied, 42).color_transform_status).toBe('applied');
    expect(() => sanitizeFormalPreview({ ...applied, lut_sha256: null }, 42)).toThrow(
      'formal preview response',
    );
    expect(() => sanitizeFormalPreview({
      schema_version: 1,
      state: 'failed',
      generation: 1,
      detection_status: null,
      source_profile: null,
      detector_rule_version: null,
      detector_manifest_sha256: null,
      detector_evidence_sha256: null,
      requested_preset_id: null,
      applied_preset_id: null,
      transform_kind: null,
      color_transform_status: null,
      color_transform_error_code: null,
      preview_id: null,
      result: null,
      failure_code: 'raw-error',
    }, 42)).toThrow('formal preview response');
  });
});
