import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  generateClientRenditionRequestId,
  readManagedRenditionRecord,
  removeManagedRenditionRecord,
  updateManagedRenditionRecord,
  writePendingManagedRendition,
} from './managedRenditionStore';

const clientRequestId = 'a'.repeat(32);

function rendition(overrides = {}) {
  return {
    rendition_id: 'b'.repeat(32),
    asset_id: 42,
    client_rendition_request_id: clientRequestId,
    selection_generation: 1,
    requested_preset_id: 'compress-only',
    applied_preset_id: null,
    state: 'queued',
    color_transform_status: null,
    error_code: null,
    result_id: null,
    created_at: '2026-07-21T00:00:00Z',
    updated_at: '2026-07-21T00:00:00Z',
    path: '/must/not/persist',
  };
}

describe('managedRenditionStore', () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    jest.clearAllMocks();
  });

  it('normalizes a secure platform UUID and never uses a random fallback', () => {
    expect(generateClientRenditionRequestId()).toMatch(/^[0-9a-f]{32}$/);
    expect(generateClientRenditionRequestId(
      () => '123E4567-E89B-12D3-A456-426614174000',
    )).toBe('123e4567e89b12d3a456426614174000');
    expect(() => generateClientRenditionRequestId(null)).toThrow('secure UUID');
    expect(() => generateClientRenditionRequestId(() => 'unsafe')).toThrow('invalid');
  });

  it('writes request identity before POST and then persists only safe rendition fields', async () => {
    const pending = await writePendingManagedRendition({
      assetId: 42,
      clientRequestId,
      requestedPresetId: 'compress-only',
      selectionSequence: 1,
      now: () => '2026-07-21T00:00:00Z',
      apiToken: 'must-not-persist',
      backendUrl: 'must-not-persist',
    });
    expect(pending.renditionId).toBeNull();

    const updated = await updateManagedRenditionRecord({
      assetId: 42,
      clientRequestId,
      selectionSequence: 1,
      rendition: rendition(),
      now: () => '2026-07-21T00:01:00Z',
    });
    const raw = await AsyncStorage.getItem('mediavault.managedRendition.v1.42');

    expect(updated.rendition.renditionId).toBe('b'.repeat(32));
    expect(raw).not.toContain('must-not-persist');
    expect(raw).not.toContain('/must/not/persist');
    expect(raw).not.toContain('sha256');
  });

  it('rejects stale response identities and keeps the current selection', async () => {
    await writePendingManagedRendition({
      assetId: 42,
      clientRequestId,
      requestedPresetId: 'compress-only',
      selectionSequence: 2,
    });

    await expect(updateManagedRenditionRecord({
      assetId: 42,
      clientRequestId,
      selectionSequence: 1,
      rendition: rendition(),
    })).rejects.toThrow('identity changed');
    expect((await readManagedRenditionRecord(42)).selectionSequence).toBe(2);
  });

  it('cleans corrupt asset-scoped storage and supports explicit removal', async () => {
    await AsyncStorage.setItem('mediavault.managedRendition.v1.42', '{broken');
    await expect(readManagedRenditionRecord(42)).resolves.toBeNull();

    await writePendingManagedRendition({
      assetId: 42,
      clientRequestId,
      requestedPresetId: 'compress-only',
      selectionSequence: 1,
    });
    await removeManagedRenditionRecord(42);
    await expect(readManagedRenditionRecord(42)).resolves.toBeNull();
  });
});
