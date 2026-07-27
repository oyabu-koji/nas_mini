import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const appConfig = JSON.parse(readFileSync(resolve(repositoryRoot, 'app.json'), 'utf8')).expo;
const plist = JSON.parse(execFileSync(
  'plutil',
  [
    '-convert',
    'json',
    '-o',
    '-',
    resolve(repositoryRoot, 'ios/LatestTemplate/Info.plist'),
  ],
  { encoding: 'utf8' },
));

const expected = {
  displayName: 'MediaVault',
  version: '0.2.0',
  allowsArbitraryLoads: false,
  allowsLocalNetworking: true,
};
const actual = {
  displayName: appConfig.name,
  version: appConfig.version,
  allowsArbitraryLoads:
    appConfig.ios?.infoPlist?.NSAppTransportSecurity?.NSAllowsArbitraryLoads,
  allowsLocalNetworking:
    appConfig.ios?.infoPlist?.NSAppTransportSecurity?.NSAllowsLocalNetworking,
};
const native = {
  displayName: plist.CFBundleDisplayName,
  version: plist.CFBundleShortVersionString,
  allowsArbitraryLoads: plist.NSAppTransportSecurity?.NSAllowsArbitraryLoads,
  allowsLocalNetworking: plist.NSAppTransportSecurity?.NSAllowsLocalNetworking,
};

for (const [key, expectedValue] of Object.entries(expected)) {
  if (actual[key] !== expectedValue || native[key] !== expectedValue) {
    throw new Error(`iOS native config mismatch for ${key}`);
  }
}

process.stdout.write(`${JSON.stringify({ app: actual, plist: native }, null, 2)}\n`);
