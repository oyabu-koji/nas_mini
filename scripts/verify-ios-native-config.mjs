import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const appConfig = JSON.parse(readFileSync(resolve(repositoryRoot, 'app.json'), 'utf8')).expo;
const packageConfig = JSON.parse(
  readFileSync(resolve(repositoryRoot, 'package.json'), 'utf8'),
);
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
const xcodeProject = readFileSync(
  resolve(repositoryRoot, 'ios/LatestTemplate.xcodeproj/project.pbxproj'),
  'utf8',
);

const expected = {
  displayName: 'MediaVault',
  version: '0.4.0',
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

const marketingVersions = [
  ...xcodeProject.matchAll(/MARKETING_VERSION = ([^;]+);/g),
].map((match) => match[1]);
if (
  marketingVersions.length !== 2
  || marketingVersions.some((version) => version !== expected.version)
) {
  throw new Error('iOS native config mismatch for marketingVersion');
}
if (packageConfig.version !== expected.version) {
  throw new Error('iOS native config mismatch for packageVersion');
}

process.stdout.write(`${JSON.stringify({
  app: actual,
  package: { version: packageConfig.version },
  plist: native,
  xcode: { marketingVersions },
}, null, 2)}\n`);
