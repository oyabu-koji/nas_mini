import appConfig from '../../../app.json';
import packageConfig from '../../../package.json';

import { CLIENT_VERSION } from './clientVersion';

describe('client version', () => {
  it('keeps package, Expo, and API versions synchronized at 0.4.0', () => {
    expect(packageConfig.version).toBe('0.4.0');
    expect(appConfig.expo.version).toBe('0.4.0');
    expect(CLIENT_VERSION).toBe('0.4.0');
  });
});
