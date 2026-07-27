import {
  isAcceptedBackendUrl,
  validateAndNormalizeBackendUrl,
} from './backendEndpointPolicy';

describe('backendEndpointPolicy', () => {
  it.each([
    ['http://10.0.0.1', 'http://10.0.0.1'],
    ['http://10.255.255.255/', 'http://10.255.255.255'],
    ['http://172.16.0.0:8000/', 'http://172.16.0.0:8000'],
    ['http://172.31.255.255', 'http://172.31.255.255'],
    ['http://192.168.0.1', 'http://192.168.0.1'],
    ['http://100.64.0.0', 'http://100.64.0.0'],
    ['http://100.127.255.255', 'http://100.127.255.255'],
    ['http://mediavault', 'http://mediavault'],
    ['http://mediavault:80', 'http://mediavault:80'],
    ['http://Mac-Mini', 'http://Mac-Mini'],
    ['http://media-vault.local:8000/', 'http://media-vault.local:8000'],
    ['https://media.example.com', 'https://media.example.com'],
    ['https://media.example.com:443', 'https://media.example.com:443'],
    ['https://host.tail-scale.ts.net:8443/', 'https://host.tail-scale.ts.net:8443'],
    ['  https://MEDIA.example.com/  ', 'https://MEDIA.example.com'],
  ])('accepts %s and only removes a root slash', (input, expected) => {
    expect(validateAndNormalizeBackendUrl(input)).toBe(expected);
    expect(isAcceptedBackendUrl(input)).toBe(true);
  });

  it.each([
    'http://9.255.255.255',
    'http://11.0.0.0',
    'http://172.15.255.255',
    'http://172.32.0.0',
    'http://192.167.255.255',
    'http://192.169.0.0',
    'http://100.63.255.255',
    'http://100.128.0.0',
    'http://127.0.0.1',
    'http://169.254.0.1',
    'http://0.0.0.0',
    'http://8.8.8.8',
    'http://localhost',
    'http://host.example.com',
    'http://host.ts.net',
    'http://host.local.example',
    'http://-host',
    'http://host-',
    'http://host_name',
    'http://media\nvault',
    'http://host..local',
    'http://.local',
    'http://host.local.',
    'http://010.0.0.1',
    'http://127.1',
    'http://10.0.0',
    'http://10.0.0.256',
    'http://10.example.com',
    'http://[::1]',
    'http://user:secret@10.0.0.1',
    'http://10.0.0.1/path',
    'http://10.0.0.1?query=1',
    'http://10.0.0.1#fragment',
    'http://10.0.0.1//',
    'ftp://10.0.0.1',
    '10.0.0.1',
    '',
  ])('rejects %s with a non-sensitive stable error', (input) => {
    expect(() => validateAndNormalizeBackendUrl(input)).toThrow(
      expect.objectContaining({
        code: 'invalid_url',
        message: 'Use a private HTTP backend or a valid HTTPS backend.',
      }),
    );
    expect(isAcceptedBackendUrl(input)).toBe(false);
  });

  it.each([
    'https://user:secret@example.com',
    'https://example.com/path',
    'https://example.com?query=1',
    'https://example.com#fragment',
    'https://example.com//',
    'https://010.0.0.1',
    'https://127.1',
    'https://0x7f.0.0.1',
    'https://0x7f000001',
    'https://2130706433',
  ])('applies origin and exact IPv4 constraints to HTTPS: %s', (input) => {
    expect(() => validateAndNormalizeBackendUrl(input)).toThrow(
      expect.objectContaining({ code: 'invalid_url' }),
    );
  });
});
