import {
  createAppError,
  messageForErrorCode,
} from '../utils/errors';

const IPV4_LITERAL_PATTERN = /^\d+(?:\.\d+)*$/;
const HOST_LABEL_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
const HTTP_IPV4_RANGES = [
  ['10.0.0.0', 8],
  ['172.16.0.0', 12],
  ['192.168.0.0', 16],
  ['100.64.0.0', 10],
];

function invalidUrlError() {
  return createAppError('invalid_url', messageForErrorCode('invalid_url'));
}

function parseExactIpv4(hostname) {
  const octets = hostname.split('.');
  if (octets.length !== 4) {
    return null;
  }

  const values = octets.map((octet) => {
    if (!/^\d+$/.test(octet) || (octet.length > 1 && octet.startsWith('0'))) {
      return null;
    }
    const value = Number(octet);
    return Number.isInteger(value) && value >= 0 && value <= 255 ? value : null;
  });
  if (values.some((value) => value == null)) {
    return null;
  }
  return values;
}

function ipv4ToUint32(octets) {
  return (
    ((octets[0] << 24) >>> 0) +
    (octets[1] << 16) +
    (octets[2] << 8) +
    octets[3]
  ) >>> 0;
}

function isInCidr(octets, network, prefixLength) {
  const addressValue = ipv4ToUint32(octets);
  const networkValue = ipv4ToUint32(parseExactIpv4(network));
  const mask = prefixLength === 0 ? 0 : (0xffffffff << (32 - prefixLength)) >>> 0;
  return (addressValue & mask) === (networkValue & mask);
}

function isAcceptedHttpIpv4(octets) {
  return HTTP_IPV4_RANGES.some(([network, prefixLength]) =>
    isInCidr(octets, network, prefixLength),
  );
}

function isValidHostname(hostname) {
  if (
    !hostname ||
    hostname.length > 253 ||
    hostname.startsWith('.') ||
    hostname.endsWith('.')
  ) {
    return false;
  }
  return hostname.split('.').every((label) => HOST_LABEL_PATTERN.test(label));
}

function isAcceptedHttpHostname(hostname) {
  if (!isValidHostname(hostname)) {
    return false;
  }
  const labels = hostname.toLowerCase().split('.');
  if (labels.length === 1) {
    return labels[0] !== 'localhost';
  }
  return labels.at(-1) === 'local';
}

function extractRawHostname(input) {
  const authorityMatch = input.match(/^[A-Za-z][A-Za-z0-9+.-]*:\/\/([^/?#]*)/);
  if (!authorityMatch) {
    throw invalidUrlError();
  }
  const authority = authorityMatch[1];
  if (authority.startsWith('[')) {
    const closingBracket = authority.indexOf(']');
    if (closingBracket < 0) {
      throw invalidUrlError();
    }
    const suffix = authority.slice(closingBracket + 1);
    if (suffix && !/^:\d+$/.test(suffix)) {
      throw invalidUrlError();
    }
    return authority.slice(0, closingBracket + 1);
  }
  const colonIndex = authority.lastIndexOf(':');
  if (colonIndex >= 0) {
    const port = authority.slice(colonIndex + 1);
    if (!/^\d+$/.test(port)) {
      throw invalidUrlError();
    }
    return authority.slice(0, colonIndex);
  }
  return authority;
}

export function validateAndNormalizeBackendUrl(input) {
  const trimmed = String(input ?? '').trim();
  if (/[\u0000-\u0020\u007f]/.test(trimmed)) {
    throw invalidUrlError();
  }
  let parsedUrl;
  try {
    parsedUrl = new URL(trimmed);
  } catch {
    throw invalidUrlError();
  }

  if (
    !['http:', 'https:'].includes(parsedUrl.protocol) ||
    !parsedUrl.hostname ||
    parsedUrl.username ||
    parsedUrl.password ||
    parsedUrl.pathname !== '/' ||
    parsedUrl.search ||
    parsedUrl.hash
  ) {
    throw invalidUrlError();
  }

  const rawHostname = extractRawHostname(trimmed);
  const rawIpv4Candidate = IPV4_LITERAL_PATTERN.test(rawHostname);
  const parsedIpv4 = parseExactIpv4(parsedUrl.hostname);
  if (parsedIpv4 || rawIpv4Candidate) {
    const rawIpv4 = parseExactIpv4(rawHostname);
    if (!rawIpv4 || !parsedIpv4 || rawHostname !== parsedUrl.hostname) {
      throw invalidUrlError();
    }
  }

  if (parsedUrl.protocol === 'http:') {
    if (parsedIpv4) {
      if (!rawIpv4Candidate || !isAcceptedHttpIpv4(parsedIpv4)) {
        throw invalidUrlError();
      }
    } else if (!isAcceptedHttpHostname(rawHostname)) {
      throw invalidUrlError();
    }
  }

  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed;
}

export function isAcceptedBackendUrl(input) {
  try {
    validateAndNormalizeBackendUrl(input);
    return true;
  } catch {
    return false;
  }
}
