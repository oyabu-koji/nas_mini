import {
  SESSION_CHUNK_TIMEOUT_MS,
  SESSION_REQUEST_TIMEOUT_MS,
  createUploadSession,
  uploadUploadSessionChunk,
} from './mediaVaultApi';

jest.mock('expo-file-system', () => ({
  File: jest.fn(),
}));

const { File } = require('expo-file-system');

describe('mediaVaultApi upload sessions', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      text: jest.fn().mockResolvedValue(JSON.stringify({
        id: 'session-123',
        status: 'created',
        size_bytes: 16,
        chunk_size_bytes: 8,
        total_chunks: 2,
        expected_file_sha256: 'a'.repeat(64),
        expires_at: '2026-07-19T00:00:00+00:00',
        missing_chunk_indexes: [0, 1],
        retryable: false,
      })),
    });
  });

  it('creates a session with JSON and uses a session timeout', async () => {
    const setTimeoutSpy = jest.spyOn(global, 'setTimeout');

    const session = await createUploadSession({
      settings: { backendUrl: 'http://backend.test', apiToken: 'masked' },
      session: { client_upload_id: 'client-123' },
    });

    expect(session.id).toBe('session-123');
    expect(session.total_chunks).toBe(2);
    expect(session.expires_at).toBe('2026-07-19T00:00:00+00:00');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://backend.test/upload-sessions',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ client_upload_id: 'client-123' }) }),
    );
    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), SESSION_REQUEST_TIMEOUT_MS);
  });

  it('sends File.slice as the raw chunk body with exact fixed-range headers', async () => {
    const slicedFile = { kind: 'slice' };
    File.mockImplementation(() => ({ slice: jest.fn().mockReturnValue(slicedFile) }));
    const setTimeoutSpy = jest.spyOn(global, 'setTimeout');

    await uploadUploadSessionChunk({
      settings: { backendUrl: 'http://backend.test', apiToken: 'masked' },
      sessionId: 'session-123',
      uri: 'file:///clip.mov',
      chunkIndex: 1,
      offset: 8,
      length: 8,
      totalSize: 16,
      sha256: 'b'.repeat(64),
    });

    expect(File).toHaveBeenCalledWith('file:///clip.mov');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://backend.test/upload-sessions/session-123/chunks/1',
      expect.objectContaining({
        method: 'PUT',
        body: slicedFile,
        headers: expect.objectContaining({
          'Content-Range': 'bytes 8-15/16',
          'X-Chunk-SHA256': 'b'.repeat(64),
        }),
      }),
    );
    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), SESSION_CHUNK_TIMEOUT_MS);
  });

  it('preserves the backend stable error code and retryability', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      text: jest.fn().mockResolvedValue(JSON.stringify({ code: 'active_session_limit', retryable: true })),
    });

    await expect(
      createUploadSession({
        settings: { backendUrl: 'http://backend.test', apiToken: 'masked' },
        session: { client_upload_id: 'client-123' },
      }),
    ).rejects.toMatchObject({ code: 'active_session_limit', retryable: true, status: 429 });
  });
});
