import React from 'react';
import { act, render, waitFor } from '@testing-library/react-native';

import { useOriginalDeletion } from '../../original-deletion/hooks/useOriginalDeletion';
import { usePreviewReview } from '../../preview-review/hooks/usePreviewReview';
import { useProcessedResultSave } from '../../processed-results/hooks/useProcessedResultSave';
import { useAssetDetail } from './useAssets';

jest.mock('../../preview-review/services/previewCacheService', () => ({
  downloadPreviewToCache: jest.fn(),
}));
jest.mock('../../processed-results/services/processedResultDownloadService', () => ({
  cleanupProcessedResultTempFile: jest.fn(),
  cleanupProcessedResultTempFiles: jest.fn(),
  downloadProcessedResult: jest.fn(),
}));
jest.mock('../../processed-results/services/processedResultMediaLibraryService', () => ({
  createProcessedResultLibraryAsset: jest.fn(),
  requestProcessedResultLibraryPermission: jest.fn(),
}));
jest.mock('../../processed-results/services/processedResultSaveStore', () => ({
  getProcessedResultSave: jest.fn(),
  listProcessedResultSaves: jest.fn(),
  markProcessedResultFailed: jest.fn(),
  markProcessedResultSaved: jest.fn(),
  writeProcessedResultDownload: jest.fn(),
  writeUnknownProcessedResultSave: jest.fn(),
}));
jest.mock('../../../shared/services/localAssetMappingStore', () => ({
  getLocalAssetMappingState: jest.fn(),
}));
jest.mock('../../original-deletion/services/originalDeletionMediaLibraryService', () => ({
  deleteOriginalAsset: jest.fn(),
}));
jest.mock('../../original-deletion/services/originalDeletionStore', () => ({
  readOriginalDeletionOutcome: jest.fn(),
  writeOriginalDeletionOutcome: jest.fn(),
}));

const previewCache = require('../../preview-review/services/previewCacheService');
const resultDownload = require('../../processed-results/services/processedResultDownloadService');
const originalDeletion = require('../../original-deletion/services/originalDeletionMediaLibraryService');

const settings = {
  backendUrl: 'http://mediavault',
  apiToken: 'secret-token',
};

function SafetyHarness() {
  const detail = useAssetDetail(settings, true, 42, { autoPoll: false });
  const review = usePreviewReview(settings, true, 42);
  const save = useProcessedResultSave({
    settings,
    assetId: 42,
    result: detail.asset?.formal_preview?.result ?? null,
  });
  const deletion = useOriginalDeletion({
    asset: detail.asset,
    capabilities: {
      features: {
        formalAppleLogPreview: true,
        safeDeleteCandidate: true,
      },
    },
    refreshAsset: detail.loadAsset,
    refreshCapabilities: jest.fn(),
  });
  global.latestFormalPreviewSafety = {
    detail,
    review,
    save,
    deletion,
  };
  return null;
}

describe('formal preview sanitizer side-effect boundary', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    const resultId = 'a'.repeat(32);
    const invalidAppliedAsset = {
      id: 42,
      type: 'video',
      filename: 'apple-log.mov',
      is_log: true,
      preview_status: 'preview_ready',
      review_status: 'preview_confirmed',
      delete_candidate_status: 'safe_to_delete_candidate',
      formal_preview: {
        schema_version: 1,
        state: 'ready',
        generation: 1,
        detection_status: 'apple_log',
        source_profile: 'apple-log-2',
        detector_rule_version: 'rule-v2',
        detector_manifest_sha256: 'b'.repeat(64),
        detector_evidence_sha256: 'c'.repeat(64),
        requested_preset_id: 'generated-apple-log2-rec709',
        applied_preset_id: 'generated-apple-log2-rec709',
        applied_preset_display_name: 'Unapproved conversion',
        preset_version: 'future-1',
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
          sha256: '0'.repeat(64),
          created_at: '2026-08-14T00:00:00Z',
          url: `/assets/42/results/${resultId}`,
        },
        failure_code: null,
      },
    };
    global.fetch = jest.fn().mockImplementation(async () => ({
      ok: true,
      status: 200,
      text: jest.fn().mockResolvedValue(JSON.stringify(invalidAppliedAsset)),
    }));
  });

  afterEach(() => {
    jest.restoreAllMocks();
    delete global.latestFormalPreviewSafety;
  });

  it('does not preview, confirm, download a result, or delete Photos after rejection', async () => {
    await render(<SafetyHarness />);
    await waitFor(() => {
      expect(global.latestFormalPreviewSafety.detail.error).toMatchObject({
        code: 'formal_preview_invalid',
      });
      expect(global.latestFormalPreviewSafety.review.assetError).toMatchObject({
        code: 'formal_preview_invalid',
      });
    });

    expect(global.latestFormalPreviewSafety.detail.asset).toBeNull();
    expect(global.latestFormalPreviewSafety.review.videoSource).toBeNull();
    expect(global.latestFormalPreviewSafety.review.imageSource).toBeNull();
    expect(global.latestFormalPreviewSafety.review.canReview).toBe(false);
    expect(global.latestFormalPreviewSafety.save.canSave).toBe(false);
    expect(global.latestFormalPreviewSafety.deletion.canDelete).toBe(false);

    await act(async () => {
      await global.latestFormalPreviewSafety.review.cachePreview();
      await global.latestFormalPreviewSafety.review.confirm();
      await global.latestFormalPreviewSafety.save.save();
      await global.latestFormalPreviewSafety.deletion.requestDeletion();
    });

    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(global.fetch.mock.calls.every(([url]) => url === 'http://mediavault/assets/42')).toBe(true);
    expect(previewCache.downloadPreviewToCache).not.toHaveBeenCalled();
    expect(resultDownload.downloadProcessedResult).not.toHaveBeenCalled();
    expect(originalDeletion.deleteOriginalAsset).not.toHaveBeenCalled();
  });
});
