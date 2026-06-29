import { useCallback, useEffect, useMemo, useState } from 'react';

import { checkHealth } from '../../../shared/api/mediaVaultApi';
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
        setApiToken(storedApiToken);
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

  const hasSavedToken = apiToken.length > 0;
  const canUseApi = backendUrl.trim().length > 0 && hasSavedToken;

  const saveSettings = useCallback(async () => {
    setStatus('saving');
    setMessage(null);
    try {
      const normalizedBackendUrl = await saveBackendUrl(backendUrl);
      const nextToken = apiTokenInput.trim() || apiToken;
      if (!normalizedBackendUrl || !nextToken) {
        throw createAppError('missing_settings', messageForErrorCode('missing_settings'));
      }
      const savedToken = apiTokenInput.trim() ? await saveApiToken(apiTokenInput) : apiToken;
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
  }, [apiToken, apiTokenInput, backendUrl]);

  const runConnectionCheck = useCallback(async () => {
    setStatus('checking');
    setMessage(null);
    try {
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
