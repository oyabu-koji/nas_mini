export const CLIENT_VERSION = '0.2.0';
export const CLIENT_VERSION_HEADER = 'X-MediaVault-Client-Version';

const SEMANTIC_VERSION_PATTERN = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;

export function parseSemanticVersion(value) {
  const match = SEMANTIC_VERSION_PATTERN.exec(String(value ?? ''));
  if (!match) {
    return null;
  }
  return match.slice(1).map(Number);
}

export function compareSemanticVersions(left, right) {
  const leftParts = parseSemanticVersion(left);
  const rightParts = parseSemanticVersion(right);
  if (!leftParts || !rightParts) {
    return null;
  }
  for (let index = 0; index < 3; index += 1) {
    if (leftParts[index] !== rightParts[index]) {
      return leftParts[index] < rightParts[index] ? -1 : 1;
    }
  }
  return 0;
}
