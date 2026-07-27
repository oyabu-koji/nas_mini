import { useCallback, useEffect, useMemo, useState } from 'react';

import { checkHealth } from '../../../shared/api/mediaVaultApi';
import {
  isAcceptedBackendUrl,
  validateAndNormalizeBackendUrl,
} from '../../../shared/services/backendEndpointPolicy';
import { getBackendUrl, saveBackendUrl } from '../../../shared/services/settingsStorage';
import { getApiToken, saveApiToken } from '../../../shared/services/secureTokenStorage';
import { createAppError, messageForErrorCode, toDisplayError } from '../../../shared/utils/errors';

export function useSettings() {
  const [backendUrl, setBackendUrl] = useState('');
  const [apiToken, setApiToken] = useState('');
  const [apiTokenInput, setApiTokenInput] = useState('');
  const [status, setStatus] = useState('loading');
  const [message, setMessage] = useState(null);

  useEffect(() => {
    let isMounted = true;
    async function load() {
      try {
        const [storedBackendUrl, storedApiToken] = await Promise.all([getBackendUrl(), getApiToken()]);
        if (!isMounted) {
          return;
        }
        setBackendUrl(storedBackendUrl);
        setApiToken(String(storedApiToken ?? '').trim());
        setStatus('idle');
      } catch {
        if (isMounted) {
          setStatus('error');
          setMessage(messageForErrorCode('unknown'));
        }
      }
    }
    load();
    return () => {
      isMounted = false;
    };
  }, []);

  const settings = useMemo(
    () => ({
      backendUrl,
      apiToken,
    }),
    [backendUrl, apiToken],
  );

  const normalizedSavedToken = String(apiToken ?? '').trim();
  const hasSavedToken = normalizedSavedToken.length > 0;
  const canUseApi = isAcceptedBackendUrl(backendUrl) && hasSavedToken;

  const saveSettings = useCallback(async () => {
    setStatus('saving');
    setMessage(null);
    try {
      const normalizedBackendUrl = validateAndNormalizeBackendUrl(backendUrl);
      const replacementToken = apiTokenInput.trim();
      const nextToken = replacementToken || normalizedSavedToken;
      if (!nextToken) {
        throw createAppError('missing_settings', messageForErrorCode('missing_settings'));
      }
      await saveBackendUrl(normalizedBackendUrl);
      const savedToken = replacementToken ? await saveApiToken(replacementToken) : nextToken;
      setBackendUrl(normalizedBackendUrl);
      setApiToken(savedToken);
      setApiTokenInput('');
      setStatus('success');
      setMessage('Settings saved.');
    } catch (error) {
      const displayError = toDisplayError(error);
      setStatus('error');
      setMessage(displayError.message);
    }
  }, [apiTokenInput, backendUrl, normalizedSavedToken]);

  const runConnectionCheck = useCallback(async () => {
    setStatus('checking');
    setMessage(null);
    try {
      validateAndNormalizeBackendUrl(settings.backendUrl);
      await checkHealth(settings);
      setStatus('success');
      setMessage('Backend is reachable.');
    } catch (error) {
      const displayError = toDisplayError(error);
      setStatus('error');
      setMessage(displayError.message);
    }
  }, [settings]);

  return {
    backendUrl,
    setBackendUrl,
    apiTokenInput,
    setApiTokenInput,
    hasSavedToken,
    settings,
    canUseApi,
    status,
    message,
    saveSettings,
    runConnectionCheck,
  };
}
