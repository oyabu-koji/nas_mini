import { isOriginalDeletionEligible } from './originalDeletionEligibility';

const common = {
  asset: {
    id: 42,
    type: 'video',
    verification_status: 'file_verified',
    preview_status: 'preview_ready',
    review_status: 'preview_confirmed',
    formal_preview: { state: 'ready' },
  },
  capabilities: {
    features: { formalAppleLogPreview: true },
  },
  mappingState: {
    status: 'available',
    mapping: { localAssetId: 'local-42' },
  },
  outcome: null,
  status: 'idle',
};

function eligible(overrides = {}) {
  return isOriginalDeletionEligible({
    ...common,
    ...overrides,
    asset: {
      ...common.asset,
      ...(overrides.asset ?? {}),
    },
  });
}

describe('isOriginalDeletionEligible', () => {
  it.each(['image', 'video'])(
    'allows a Phase 1 direct %s without formal capability or preview',
    (type) => {
      expect(eligible({
        asset: {
          type,
          verification_status: 'server_hash_recorded',
          formal_preview: null,
        },
        capabilities: null,
      })).toBe(true);
    },
  );

  it.each([
    { preview_status: 'preview_generating' },
    { preview_status: 'preview_failed' },
    { review_status: 'not_reviewed' },
  ])('rejects an unready or unconfirmed Phase 1 direct asset: %o', (asset) => {
    expect(eligible({
      asset: {
        ...asset,
        verification_status: 'server_hash_recorded',
      },
      capabilities: null,
    })).toBe(false);
  });

  it.each([
    {
      transform_kind: 'lut',
      color_transform_status: 'applied',
      color_transform_error_code: null,
    },
    {
      transform_kind: 'none',
      color_transform_status: 'unavailable',
      color_transform_error_code: 'lut_preset_unavailable',
    },
  ])('allows a ready compatible Phase 2 formal preview: %o', (formalPreview) => {
    expect(eligible({
      asset: {
        formal_preview: {
          state: 'ready',
          ...formalPreview,
        },
      },
    })).toBe(true);
  });

  it.each([
    null,
    { features: { formalAppleLogPreview: false } },
    { features: {} },
  ])('rejects Phase 2 when capability is unavailable: %o', (capabilities) => {
    expect(eligible({ capabilities })).toBe(false);
  });

  it.each([null, { state: 'generating' }, { state: 'failed' }])(
    'rejects Phase 2 without a ready formal preview: %o',
    (formalPreview) => {
      expect(eligible({ asset: { formal_preview: formalPreview } })).toBe(false);
    },
  );

  it('does not treat managed results or a legacy LOG hint as deletion authority', () => {
    expect(eligible({
      asset: {
        verification_status: 'uploading',
        is_log: true,
        active_processed_result: { state: 'ready' },
        active_rendition: { state: 'ready' },
      },
    })).toBe(false);
  });

  it.each([
    { mappingState: { status: 'missing', mapping: null } },
    { outcome: { status: 'deleted' } },
    { status: 'loading' },
    { status: 'deleting' },
  ])('rejects unavailable, terminal, or busy local state: %o', (override) => {
    expect(eligible(override)).toBe(false);
  });

  it.each([
    { type: 'image', verification_status: 'file_verified' },
    { type: 'audio', verification_status: 'server_hash_recorded' },
    { type: 'video', verification_status: 'unknown' },
  ])('rejects an unknown asset origin: %o', (asset) => {
    expect(eligible({ asset })).toBe(false);
  });
});
