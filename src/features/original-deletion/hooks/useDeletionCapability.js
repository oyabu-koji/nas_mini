import { useCallback, useEffect, useRef, useState } from 'react';

import { getMediaVaultCapabilities } from '../../../shared/api/capabilitiesApi';
import { toDisplayError } from '../../../shared/utils/errors';

export function useDeletionCapability({ settings, canUseApi }) {
  const [capabilities, setCapabilities] = useState(null);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const operationRef = useRef(0);

  const refreshCapabilities = useCallback(async () => {
    const operation = operationRef.current + 1;
    operationRef.current = operation;
    if (!canUseApi) {
      setCapabilities(null);
      setStatus('idle');
      setError(null);
      return null;
    }

    setStatus('loading');
    setError(null);
    try {
      const nextCapabilities = await getMediaVaultCapabilities(settings);
      if (operation !== operationRef.current) {
        return null;
      }
      setCapabilities(nextCapabilities);
      setStatus('ready');
      return nextCapabilities;
    } catch (capabilityError) {
      if (operation !== operationRef.current) {
        return null;
      }
      setCapabilities(null);
      setStatus('error');
      setError(toDisplayError(capabilityError));
      return null;
    }
  }, [canUseApi, settings]);

  useEffect(() => {
    refreshCapabilities();
    return () => {
      operationRef.current += 1;
    };
  }, [refreshCapabilities]);

  return {
    capabilities,
    status,
    error,
    refreshCapabilities,
  };
}
