import {
  createManagedRendition,
  getManagedCapabilities,
  getManagedRendition,
  listManagedPresets,
  sanitizeCapabilities,
  sanitizePresetCatalog,
  sanitizeRendition,
} from './managedRenditionApi';

const settings = { backendUrl: 'http://backend.test', apiToken: 'secret-token' };

function capabilities(overrides = {}) {
  return {
    api_version: 'v1',
    minimum_client_version: null,
    features: {
      processed_result_delivery: true,
      managed_preview_presets: true,
      custom_lut: false,
      generated_apple_log_conversion: false,
      numeric_rendition_progress: false,
    },
    unknown_server_field: 'ignored',
    ...overrides,
  };
}

function preset(overrides = {}) {
  return {
    preset_id: 'compress-only',
    display_name: 'Compress only',
    preset_kind: 'compress-only',
    enabled: true,
    available: true,
    version: '1',
    target_color_space: null,
    source_reference: 'MediaVault built-in',
    terms_reference: 'Project source',
    unknown: 'ignored',
    ...overrides,
  };
}

function rendition(overrides = {}) {
  return {
    rendition_id: 'a'.repeat(32),
    asset_id: 42,
    client_rendition_request_id: 'b'.repeat(32),
    selection_generation: 1,
    requested_preset_id: 'compress-only',
    applied_preset_id: null,
    state: 'queued',
    color_transform_status: null,
    error_code: null,
    result_id: null,
    created_at: '2026-07-21T00:00:00Z',
    updated_at: '2026-07-21T00:00:00Z',
    internal_path: '/must/not/escape',
    ...overrides,
  };
}

function response(payload, ok = true, status = 200) {
  return {
    ok,
    status,
    text: jest.fn().mockResolvedValue(JSON.stringify(payload)),
  };
}

describe('managedRenditionApi', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it('sanitizes capabilities and rejects incompatible or malformed responses', () => {
    expect(sanitizeCapabilities(capabilities()).features).toEqual({
      processedResultDelivery: true,
      managedPreviewPresets: true,
      customLut: false,
      generatedAppleLogConversion: false,
      numericRenditionProgress: false,
    });
    expect(() => sanitizeCapabilities(capabilities({ api_version: 'v2' }))).toThrow('capabilities');
    expect(() => sanitizeCapabilities(capabilities({
      features: { ...capabilities().features, managed_preview_presets: false },
    }))).toThrow('compatible');
  });

  it('requires server returned compress-only and filters unknown preset kinds', () => {
    const items = sanitizePresetCatalog({
      items: [
        preset(),
        preset({ preset_id: 'identity-v1', preset_kind: 'generated-identity', display_name: 'Identity test' }),
        preset({ preset_id: 'future-kind', preset_kind: 'future', display_name: 'Unknown' }),
      ],
    });

    expect(items.map((item) => item.presetId)).toEqual(['compress-only', 'identity-v1']);
    expect(items[1]).not.toHaveProperty('unknown');
    expect(() => sanitizePresetCatalog({ items: [preset({ preset_id: 'identity-v1' })] })).toThrow('catalog');
    expect(() => sanitizePresetCatalog({ items: [preset(), preset()] })).toThrow('catalog');
  });

  it('sanitizes all phase states and rejects unsafe terminal identities', () => {
    ['queued', 'validating', 'rendering', 'finalizing'].forEach((state) => {
      expect(sanitizeRendition(rendition({ state }), 42).state).toBe(state);
    });
    const ready = sanitizeRendition(rendition({
      state: 'ready',
      applied_preset_id: 'compress-only',
      color_transform_status: 'not_requested',
      result_id: 'c'.repeat(32),
    }), 42);
    expect(ready.resultId).toBe('c'.repeat(32));
    expect(ready).not.toHaveProperty('internal_path');
    expect(() => sanitizeRendition(rendition({ asset_id: 99 }), 42)).toThrow('response');
    expect(() => sanitizeRendition(rendition({ state: 'ready' }), 42)).toThrow('response');
  });

  it('calls authenticated versioned endpoints with only the managed contract', async () => {
    global.fetch
      .mockResolvedValueOnce(response(capabilities()))
      .mockResolvedValueOnce(response({ items: [preset()] }))
      .mockResolvedValueOnce(response(rendition()))
      .mockResolvedValueOnce(response(rendition()));

    await getManagedCapabilities(settings);
    await listManagedPresets(settings);
    await createManagedRendition({
      settings,
      assetId: 42,
      clientRequestId: 'b'.repeat(32),
      presetId: 'compress-only',
    });
    await getManagedRendition({ settings, assetId: 42, renditionId: 'a'.repeat(32) });

    expect(global.fetch.mock.calls.map(([url]) => url)).toEqual([
      'http://backend.test/api/v1/capabilities',
      'http://backend.test/api/v1/presets',
      'http://backend.test/api/v1/assets/42/renditions',
      `http://backend.test/api/v1/assets/42/renditions/${'a'.repeat(32)}`,
    ]);
    const post = global.fetch.mock.calls[2][1];
    expect(JSON.parse(post.body)).toEqual({
      client_rendition_request_id: 'b'.repeat(32),
      preset_id: 'compress-only',
    });
    expect(post.headers.Authorization).toBe('Bearer secret-token');
    expect(post.body).not.toContain('path');
  });

  it('preserves stable server error codes and retryability', async () => {
    global.fetch.mockResolvedValue(response({
      code: 'rendition_precondition_changed',
      retryable: true,
    }, false, 409));

    await expect(createManagedRendition({
      settings,
      assetId: 42,
      clientRequestId: 'b'.repeat(32),
      presetId: 'compress-only',
    })).rejects.toMatchObject({
      code: 'rendition_precondition_changed',
      retryable: true,
      status: 409,
    });
  });
});
