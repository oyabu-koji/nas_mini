const { globalIgnores } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');
const globals = require('globals');

module.exports = [
  globalIgnores([
    'coverage/',
    'dist/',
    'build/',
    'web-build/',
    '.expo/',
    'node_modules/',
    'backend/',
    '.agents/',
    '.steering/',
    'tmp/',
    'temp/',
    'generated/',
  ]),
  ...expoConfig,
  {
    files: ['**/*.test.{js,jsx}', 'jest.setup.js'],
    languageOptions: {
      globals: globals.jest,
    },
  },
  {
    files: ['eslint.config.js'],
    languageOptions: {
      globals: globals.node,
    },
  },
];
