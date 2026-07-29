import { getMediaVaultCapabilities, sanitizeCapabilities } from './capabilitiesApi';

const settings = { backendUrl: 'http://mediavault', apiToken: 'secret-token' };

function payload(overrides = {}) {
  const base = {
    api_version: 'v1',
    minimum_client_version: null,
    formal_preview_schema_version: 1,
    features: {
      processed_result_delivery: false,
      managed_preview_presets: false,
      custom_lut: false,
      generated_apple_log_conversion: false,
      numeric_rendition_progress: false,
      detector_certified: false,
      formal_apple_log_preview: false,
      safe_delete_candidate: false,
    },
  };
  return {
    ...base,
    ...overrides,
    features: {
      ...base.features,
      ...(overrides.features ?? {}),
    },
  };
}

function response(value) {
  return {
    ok: true,
    status: 200,
    text: jest.fn().mockResolvedValue(JSON.stringify(value)),
  };
}

describe('capabilitiesApi', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it.each([
    ['pre-Phase 2', payload()],
    ['Phase 2B', payload({
      minimum_client_version: '0.2.0',
      features: {
        detector_certified: true,
        formal_apple_log_preview: true,
      },
    })],
    ['Phase 2C', payload({
      minimum_client_version: '0.3.0',
      features: {
        detector_certified: true,
        formal_apple_log_preview: true,
        safe_delete_candidate: true,
      },
    })],
  ])('sanitizes a %s response', (_name, value) => {
    const result = sanitizeCapabilities(value);
    expect(result.features.safeDeleteCandidate).toBe(
      value.features.safe_delete_candidate,
    );
  });

  it.each([
    payload({
      features: { safe_delete_candidate: undefined },
    }),
    payload({
      features: { safe_delete_candidate: 'true' },
    }),
    payload({
      minimum_client_version: '0.3.0',
      features: { safe_delete_candidate: true },
    }),
    payload({
      minimum_client_version: '0.2.0',
      features: {
        formal_apple_log_preview: true,
        safe_delete_candidate: true,
      },
    }),
  ])('rejects missing, malformed, or contradictory Phase 2C data', (value) => {
    expect(() => sanitizeCapabilities(value)).toThrow('capabilities');
  });

  it('rejects a server minimum newer than this client', () => {
    expect(() => sanitizeCapabilities(payload({
      minimum_client_version: '0.3.1',
    }))).toThrow('compatible');
  });

  it('fetches the shared read-only capability endpoint', async () => {
    global.fetch.mockResolvedValue(response(payload()));

    await expect(getMediaVaultCapabilities(settings)).resolves.toMatchObject({
      apiVersion: 'v1',
    });
    expect(global.fetch).toHaveBeenCalledWith(
      'http://mediavault/api/v1/capabilities',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer secret-token',
        }),
      }),
    );
  });
});
