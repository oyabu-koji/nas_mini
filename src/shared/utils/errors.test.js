import {
  AppError,
  classifyHttpStatus,
  createHttpError,
  createNetworkError,
  createTimeoutError,
  messageForErrorCode,
  toDisplayError,
} from './errors';

describe('errors', () => {
  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [409, 'preview_not_ready'],
    [413, 'too_large'],
    [416, 'range_not_satisfiable'],
    [422, 'validation_error'],
    [500, 'server_error'],
    [418, 'unknown'],
  ])('classifies HTTP %s as %s', (status, code) => {
    expect(classifyHttpStatus(status)).toBe(code);
  });

  it('maps unknown failures to safe retryable display data', () => {
    expect(messageForErrorCode('not_registered')).toBe('Something went wrong.');
    expect(toDisplayError(new Error('private adapter detail'))).toEqual({
      code: 'unknown',
      message: 'Something went wrong.',
      retryable: true,
    });
  });

  it('preserves AppError identity for processed and managed server codes', () => {
    const processed = createHttpError(409, 'processed_result_superseded', false);
    const managed = createHttpError(409, 'rendition_precondition_changed', true);

    expect(processed).toMatchObject({
      code: 'processed_result_superseded',
      status: 409,
      retryable: false,
    });
    expect(toDisplayError(managed)).toEqual({
      code: 'rendition_precondition_changed',
      message: 'The active processed video changed. Retry the same request.',
      retryable: true,
    });
  });

  it('derives retryability for preview, server, network, and timeout failures', () => {
    expect(createHttpError(409)).toMatchObject({ code: 'preview_not_ready', retryable: true });
    expect(createHttpError(503)).toMatchObject({ code: 'server_error', retryable: true });
    expect(createHttpError(401)).toMatchObject({ code: 'unauthorized', retryable: false });
    expect(createNetworkError()).toMatchObject({ code: 'network_unreachable', retryable: true });
    expect(createTimeoutError()).toMatchObject({ code: 'timeout', retryable: true });
    expect(createNetworkError()).toBeInstanceOf(AppError);
  });
});
