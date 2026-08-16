import {
  DEFAULT_REQUEST_TIMEOUT_MS,
  UPLOAD_REQUEST_TIMEOUT_MS,
  buildPreviewSource,
  buildProcessedResultPath,
  buildProcessedResultSource,
  getAsset,
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
      baseUrl: 'http://mediavault',
      path: '/assets',
      requiresAuth: false,
    });

    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), DEFAULT_REQUEST_TIMEOUT_MS);
  });

  it('uses the longer timeout only for uploads', async () => {
    const setTimeoutSpy = jest.spyOn(global, 'setTimeout');

    await uploadAsset({
      settings: { backendUrl: 'http://mediavault', apiToken: 'masked' },
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
        baseUrl: 'http://mediavault',
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
        baseUrl: 'http://mediavault',
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
        baseUrl: 'http://mediavault',
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
        baseUrl: 'http://mediavault',
        path: '/health',
        requiresAuth: false,
        fetchImpl,
      }),
    ).resolves.toBeNull();
  });

  it('rejects an invalid JSON endpoint before token coercion or fetch', async () => {
    const fetchImpl = jest.fn();
    const token = { toString: jest.fn(() => 'secret-token') };

    await expect(
      requestJson({
        baseUrl: 'http://public.example.com/private?secret=value',
        apiToken: token,
        path: '/assets',
        fetchImpl,
      }),
    ).rejects.toMatchObject({
      code: 'invalid_url',
      message: 'Use a private HTTP backend or a valid HTTPS backend.',
    });

    expect(token.toString).not.toHaveBeenCalled();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('rejects an invalid multipart endpoint before fetch', async () => {
    const fetchImpl = jest.spyOn(global, 'fetch');

    await expect(
      uploadAsset({
        settings: {
          backendUrl: 'http://203.0.113.10',
          apiToken: 'secret-token',
        },
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
      }),
    ).rejects.toMatchObject({ code: 'invalid_url' });

    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('rejects an invalid preview endpoint before token coercion or source creation', () => {
    const token = { toString: jest.fn(() => 'secret-token') };

    expect(() =>
      buildPreviewSource({
        baseUrl: 'http://preview.example.com',
        apiToken: token,
        assetId: 42,
      }),
    ).toThrow(expect.objectContaining({ code: 'invalid_url' }));
    expect(token.toString).not.toHaveBeenCalled();
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
    expect(asset.delete_candidate_status).toBe('not_candidate');
    expect(asset.active_processed_result).toEqual({
      result_id: resultId,
      mime_type: 'video/mp4',
      size_bytes: 123,
      sha256: 'b'.repeat(64),
      created_at: '2026-07-18T00:00:00Z',
      url: `/assets/42/results/${resultId}`,
    });
  });

  it.each([
    ['safe_to_delete_candidate', 'safe_to_delete_candidate'],
    ['not_candidate', 'not_candidate'],
    ['future_candidate', 'not_candidate'],
    [undefined, 'not_candidate'],
  ])('closes candidate status %p to %p', (input, expected) => {
    expect(sanitizeAsset({
      id: 42,
      delete_candidate_status: input,
    }).delete_candidate_status).toBe(expected);
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
          baseUrl: 'http://mediavault',
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
        baseUrl: 'http://mediavault/',
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
      uri: `http://mediavault/assets/42/results/${resultId}`,
      headers: {
        Authorization: 'Bearer secret-token',
        'X-MediaVault-Client-Version': '0.4.0',
      },
    });
  });

  it('adds the 0.4.0 client header only to Phase 2 asset detail reads', async () => {
    const fetchImpl = jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      text: jest.fn().mockResolvedValue(JSON.stringify({ id: 42 })),
    });

    await getAsset({ backendUrl: 'http://mediavault', apiToken: 'secret-token' }, 42);

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://mediavault/assets/42',
      expect.objectContaining({
        headers: expect.objectContaining({
          'X-MediaVault-Client-Version': '0.4.0',
        }),
      }),
    );
  });

  it('rejects an invalid processed-result endpoint before token coercion', () => {
    const resultId = 'e'.repeat(32);
    const token = { toString: jest.fn(() => 'secret-token') };

    expect(() =>
      buildProcessedResultSource({
        baseUrl: 'http://results.example.com',
        apiToken: token,
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
    ).toThrow(expect.objectContaining({ code: 'invalid_url' }));
    expect(token.toString).not.toHaveBeenCalled();
  });

  it('sanitizes formal Apple Log fallback and ordinary preview claims', () => {
    const resultId = 'a'.repeat(32);
    const previewId = 'b'.repeat(32);
    const detector = {
      detection_status: 'apple_log',
      source_profile: 'apple-log-1',
      detector_rule_version: 'rule-v2',
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
      source_profile: 'apple-log-1',
      requested_preset_id: 'generated-apple-log-rec709',
      applied_preset_id: 'compress-only',
      color_transform_status: 'unavailable',
      result,
    });

    expect(sanitizeFormalPreview({
      ...fallback,
      detection_status: 'unknown',
      source_profile: null,
      requested_preset_id: 'compress-only',
      applied_preset_id: 'compress-only',
      color_transform_status: 'not_requested',
      color_transform_error_code: null,
    }, 42)).toMatchObject({
      detection_status: 'unknown',
      color_transform_status: 'not_requested',
    });
  });

  it('accepts the exact Apple Log 2 compress-only fallback tuple', () => {
    const resultId = '2'.repeat(32);
    const preview = sanitizeFormalPreview({
      schema_version: 1,
      state: 'ready',
      generation: 1,
      detection_status: 'apple_log',
      source_profile: 'apple-log-2',
      detector_rule_version: 'rule-v2',
      detector_manifest_sha256: '3'.repeat(64),
      detector_evidence_sha256: '4'.repeat(64),
      requested_preset_id: 'generated-apple-log2-rec709',
      applied_preset_id: 'compress-only',
      applied_preset_display_name: null,
      preset_version: null,
      manifest_sha256: null,
      lut_sha256: null,
      transform_kind: 'none',
      color_transform_status: 'unavailable',
      color_transform_error_code: 'lut_preset_unavailable',
      preview_id: '5'.repeat(32),
      result: {
        result_id: resultId,
        mime_type: 'video/mp4',
        size_bytes: 12,
        sha256: '6'.repeat(64),
        created_at: '2026-08-14T00:00:00Z',
        url: `/assets/42/results/${resultId}`,
      },
      failure_code: null,
    }, 42);

    expect(preview).toMatchObject({
      source_profile: 'apple-log-2',
      requested_preset_id: 'generated-apple-log2-rec709',
      applied_preset_id: 'compress-only',
      color_transform_status: 'unavailable',
    });
  });

  it.each([
    ['cross-profile preset', { requested_preset_id: 'generated-apple-log2-rec709' }],
    ['unknown profile', { source_profile: 'apple-log-3' }],
    ['missing profile', { source_profile: null }],
    ['reserved applied preset', { applied_preset_id: 'generated-apple-log-rec709' }],
    ['LUT transform', { transform_kind: 'lut' }],
    ['applied LUT identity', { lut_sha256: '9'.repeat(64) }],
  ])('rejects an Apple Log fallback with %s', (_name, mutation) => {
    const resultId = 'a'.repeat(32);
    const fallback = {
      schema_version: 1,
      state: 'ready',
      generation: 1,
      detection_status: 'apple_log',
      source_profile: 'apple-log-1',
      detector_rule_version: 'rule-v2',
      detector_manifest_sha256: 'b'.repeat(64),
      detector_evidence_sha256: 'c'.repeat(64),
      requested_preset_id: 'generated-apple-log-rec709',
      applied_preset_id: 'compress-only',
      applied_preset_display_name: null,
      preset_version: null,
      manifest_sha256: null,
      lut_sha256: null,
      transform_kind: 'none',
      color_transform_status: 'unavailable',
      color_transform_error_code: 'lut_preset_unavailable',
      preview_id: 'd'.repeat(32),
      result: {
        result_id: resultId,
        mime_type: 'video/mp4',
        size_bytes: 12,
        sha256: 'e'.repeat(64),
        created_at: '2026-08-14T00:00:00Z',
        url: `/assets/42/results/${resultId}`,
      },
      failure_code: null,
    };

    expect(() => sanitizeFormalPreview({ ...fallback, ...mutation }, 42)).toThrow(
      'formal preview response',
    );
  });

  it('rejects future Apple Log applied claims and unknown failure codes', () => {
    const resultId = 'a'.repeat(32);
    const applied = {
      schema_version: 1,
      state: 'ready',
      generation: 1,
      detection_status: 'apple_log',
      source_profile: 'apple-log-1',
      detector_rule_version: 'rule-v2',
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
    expect(() => sanitizeFormalPreview(applied, 42)).toThrow(
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
