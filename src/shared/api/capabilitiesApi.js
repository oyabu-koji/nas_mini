import {
  CLIENT_VERSION,
  compareSemanticVersions,
  parseSemanticVersion,
} from '../constants/clientVersion';
import { createAppError, messageForErrorCode } from '../utils/errors';
import { requestJson } from './mediaVaultApi';

const PHASE2C_MINIMUM_CLIENT_VERSION = '0.3.0';

export async function getMediaVaultCapabilities(settings) {
  return sanitizeCapabilities(await requestJson({
    baseUrl: settings.backendUrl,
    apiToken: settings.apiToken,
    path: '/api/v1/capabilities',
  }));
}

export function sanitizeCapabilities(value) {
  const features = value?.features;
  if (
    !value || value.api_version !== 'v1' || !features
    || typeof features.processed_result_delivery !== 'boolean'
    || typeof features.managed_preview_presets !== 'boolean'
    || typeof features.custom_lut !== 'boolean'
    || typeof features.generated_apple_log_conversion !== 'boolean'
    || typeof features.numeric_rendition_progress !== 'boolean'
    || typeof features.detector_certified !== 'boolean'
    || typeof features.formal_apple_log_preview !== 'boolean'
    || typeof features.safe_delete_candidate !== 'boolean'
    || value.formal_preview_schema_version !== 1
    || (
      value.minimum_client_version != null
      && !parseSemanticVersion(value.minimum_client_version)
    )
  ) {
    throw domainError('managed_capabilities_invalid');
  }

  if (
    features.safe_delete_candidate
    && (
      !features.formal_apple_log_preview
      || value.minimum_client_version == null
      || compareSemanticVersions(
        value.minimum_client_version,
        PHASE2C_MINIMUM_CLIENT_VERSION,
      ) < 0
    )
  ) {
    throw domainError('managed_capabilities_invalid');
  }

  if (
    value.minimum_client_version != null
    && compareSemanticVersions(CLIENT_VERSION, value.minimum_client_version) < 0
  ) {
    throw domainError('incompatible_client');
  }

  return {
    apiVersion: 'v1',
    minimumClientVersion: value.minimum_client_version,
    features: {
      processedResultDelivery: features.processed_result_delivery,
      managedPreviewPresets: features.managed_preview_presets,
      customLut: features.custom_lut,
      generatedAppleLogConversion: features.generated_apple_log_conversion,
      numericRenditionProgress: features.numeric_rendition_progress,
      detectorCertified: features.detector_certified,
      formalAppleLogPreview: features.formal_apple_log_preview,
      safeDeleteCandidate: features.safe_delete_candidate,
    },
    formalPreviewSchemaVersion: 1,
  };
}

function domainError(code) {
  return createAppError(code, messageForErrorCode(code));
}
