# Feature Spec

## Metadata

- Date: 2026-07-21
- Baseline last measured: 2026-07-22
- Feature name: Mobile lint and coverage quality gates
- Status: implemented and formally validated on 2026-07-23
- Priority: project-wide quality improvement; non-blocking for the next product feature,
  but required before the Phase 2B release candidate is accepted
- Related files:
  - `docs/architecture.md`
  - `docs/development-guidelines.md`
  - `package.json`
  - `jest.setup.js`
  - `.gitignore`
  - `.steering/20260718_1-processed-video-delivery/tasklist.md`
  - `.steering/20260718_2-managed-preview-presets/tasklist.md`

## Background

The processed-video-delivery and managed-preview-presets implementations pass their
Backend, Mobile, Expo dependency, iOS export, and Compose checks. Validation of the
current combined codebase nevertheless identifies two project-wide quality gaps:

- `npm run lint` is documented as a Mobile quality command but no lint script or ESLint
  configuration exists.
- The latest imported-files-only Mobile coverage is 77.36% statements and 77.54% lines.
  This is not yet the canonical gate because Jest does not collect every maintained
  Mobile source module. The initial project quality target is 80%.

These gaps are not runtime defects in either implementation and do not block work on the
next product feature. They should be handled as one explicit quality-gate feature so that
later Phase 2B work does not increase an unmeasured lint or test deficit.

## Goal

Provide reproducible Mobile lint and coverage commands that pass on the existing Expo
SDK 54 JavaScript application, fail when the agreed thresholds regress, and are suitable
for both local development and later CI use.

## Scope

- Add an Expo SDK 54-compatible ESLint setup for the repository's JavaScript and JSX
  Mobile source, tests, and relevant root configuration files.
- Add `npm run lint` as the canonical non-mutating lint command.
- Correct existing lint errors without changing product behavior. Avoid unrelated bulk
  formatting or architecture refactors.
- Add `npm run test:coverage` as the canonical Jest coverage command.
- Raise global statements and lines coverage to at least 80% through meaningful tests of
  observable behavior and failure paths.
- Set executable Jest coverage thresholds so a later regression makes
  `npm run test:coverage` fail.
- Preserve the latest branch and function coverage as explicit minimum floors of 69.46%
  branches and 80.08% functions while statements and lines reach the initial 80% gate.
  Raising branches to 80% may be a later quality increment and must not be implied by
  this feature.
- Keep coverage output and other generated analysis artifacts out of Git.
- Record the final commands and thresholds in the durable engineering documentation.

## Out of Scope

- Backend lint tooling or Backend coverage thresholds.
- TypeScript adoption, Expo SDK upgrade, Node version change, or replacement of Jest.
- Product behavior changes, UI redesign, Phase 2B preset/LUT implementation, or Apple
  Log detection.
- Artificial coverage increases through broad source exclusions, ignored files,
  empty assertions, direct testing of private implementation details, or removal of
  valid failure branches.
- Selecting or configuring a hosted CI provider. The commands must be CI-ready, but the
  provider workflow belongs to a later repository-operations task.
- Requiring physical-device tests to contribute to Jest coverage.

## Current Reference Baseline

- Measured on 2026-07-22 after managed-preview-presets remediation.
- `npm test`: 21 suites and 96 tests pass.
- The current `npx jest --runInBand --coverage` result is:
  - statements: 77.36% (`1022 / 1321`)
  - branches: 69.46% (`851 / 1225`)
  - functions: 80.08% (`193 / 241`)
  - lines: 77.54% (`1012 / 1305`)
- This result uses Jest's current imported-files-only behavior. It is a reference for
  preventing regression, not the canonical all-maintained-source baseline.
- The repository currently contains 56 JavaScript/JSX files under `src/`, including 21
  test files, plus the maintained JavaScript entry point under
  `modules/streaming-sha256/src/`.
- No `lint` or `test:coverage` package script exists.
- No ESLint configuration or ESLint development dependency exists.

The implementation must first introduce the canonical collection settings below and
record the resulting all-maintained-source baseline before editing tests. The steering
plan records the command, timestamp, matched production-file count, suite/test count,
and all four metrics for both the reference and canonical baselines. A denominator
change is expected when unimported source is included; it does not permit lowering the
final floors defined by this specification.

## Implementation Result

- Implemented on 2026-07-22 with Node `v24.15.0`, Expo `~54.0.36`, Jest `~29.7.0`, and the locked npm dependency graph.
- Canonical initial measurement immediately after config introduction and before production/test remediation:
  - command: `npm run test:coverage`
  - production files: 36
  - suites/tests: 21 / 96, all behavioral tests passed
  - statements: 68.91% (`1022 / 1483`)
  - branches: 62.48% (`851 / 1362`)
  - functions: 69.67% (`193 / 277`)
  - lines: 69.22% (`1012 / 1462`)
  - exit: 1 only because all four configured global floors were below target
- Final canonical measurement at 2026-07-22 23:35:09 +0900:
  - command: `npm run test:coverage`
  - production files: 36, with no zero-coverage file
  - suites/tests: 32 / 157, all passed
  - statements: 86.07% (`1280 / 1487`)
  - branches: 77.30% (`1056 / 1366`)
  - functions: 89.56% (`249 / 278`)
  - lines: 86.08% (`1262 / 1466`)
  - exit: 0
- The production-file count stayed fixed at 36. Total instrumented statements, branches,
  functions, and lines changed only because the lint remediation stabilized the
  processed-result identity with `useMemo`; the source scope and exclusions did not change.

## Functional Requirements

### Lint Contract

- Use the Expo SDK 54-compatible flat configuration at root `eslint.config.js`.
- The canonical package script is
  `eslint App.jsx index.js jest.setup.js eslint.config.js src modules --max-warnings=0`.
  Therefore `npm run lint` checks the maintained root JavaScript files, `src/`, and
  `modules/` without modifying files. A newly maintained root JavaScript configuration
  file must be added to this explicit scope.
- The configuration supports React, React Native, Jest globals, and React Hooks rules as
  used by the current Expo managed application.
- Generated export output, coverage output, `node_modules`, Backend Python, media files,
  and temporary directories are excluded explicitly.
- The canonical command enforces `--max-warnings=0`; errors and warnings both make it
  exit non-zero.
- An optional `lint:fix` command may be provided, but validation and future CI use the
  non-mutating `lint` command.
- Rule suppressions are local and justified. Repository-wide disabling of Hooks,
  undefined-variable, unreachable-code, or equivalent correctness rules is prohibited.

### Coverage Contract

- The canonical `test:coverage` package script is
  `jest --runInBand --coverage`. It runs the normal Jest suite with coverage enabled and
  returns the same behavioral result as `npm test`.
- The command enforces Jest `coverageThreshold.global` floors of 80% statements, 80%
  lines, 69.46% branches, and 80.08% functions. The implementation may raise but must not
  lower these values.
- Canonical `collectCoverageFrom` includes `src/**/*.{js,jsx}` and
  `modules/*/src/**/*.{js,jsx}`, excluding only `**/*.test.{js,jsx}` and
  `**/__tests__/**`. Root bootstrap files `index.js` and `App.jsx`, Jest setup/config,
  generated code, and static configuration are outside this coverage denominator.
- The initial feature introduces no additional coverage exclusion. A later exclusion
  requires a documented technical reason and review of the quality-gate specification.
- Coverage output uses `<rootDir>/coverage` and the `text`, `lcov`, and `json-summary`
  reporters. The directory remains ignored by Git.
- Any future coverage-scope change records the old and new globs, matched production-file
  counts, suite/test counts, all four metrics, reason, and approval. No scope change may
  silently retain an old numerator or lower a floor.
- New tests prioritize observable behavior in the currently low-coverage, user-relevant
  areas, including asset loading/detail flows, media metadata normalization, API failure
  handling, and processed-result save transitions.
- Tests must cover success and failure outcomes without network access, device-specific
  writes, timing flakiness, or dependence on test execution order.
- Coverage reports are generated under a conventional ignored directory and are never
  committed.

### Validation Contract

- The following commands all succeed from a clean dependency installation compatible
  with the existing lockfile:

```bash
npm run lint
npm test
npm run test:coverage
npx expo install --check
npx expo export --platform ios
```

- Backend regression tests continue to pass with `cd backend && uv run pytest` even
  though this feature does not introduce Backend lint policy.
- `git diff --check` succeeds and generated coverage/export artifacts do not appear in
  `git status`.

## Implementation Constraints

- Keep React Native, Expo managed workflow, JavaScript, Node 24, and Expo SDK 54.
- Add packages using the repository's npm/Expo dependency rules and commit the resulting
  `package-lock.json` change. Install Expo-related lint packages, including
  `eslint-config-expo`, through `npx expo install`.
- Prefer the lint configuration recommended for the installed Expo SDK, then add only
  the minimal project-specific overrides required for Jest and the existing source
  layout.
- Do not mix mass formatting with lint correctness fixes. A formatting policy can be
  proposed separately if needed.
- Do not weaken the tests, production validation, or error paths implemented by earlier
  features to satisfy a lint or coverage number.

## Acceptance Criteria

- `npm run lint` exists, is non-mutating, and exits successfully under the enforced
  warning policy.
- `npm run test:coverage` exists and fails when global statements or lines fall below
  80%, branches below 69.46%, or functions below 80.08%.
- The final report shows at least 80% global statements and lines coverage with all Jest
  suites passing, while branches remain at least 69.46% and functions at least 80.08%.
- The canonical coverage report includes all production files matched by
  `src/**/*.{js,jsx}` and `modules/*/src/**/*.{js,jsx}` after the stated test exclusions;
  an intentionally unimported source file appears with zero coverage rather than being
  omitted.
- Added tests exercise real public behavior and materially cover previously untested
  success/error branches; broad exclusions or no-op assertions are not used.
- `npm test`, Expo dependency validation, iOS export, Backend pytest, and
  `git diff --check` remain successful.
- Coverage/export output is ignored and no generated report is committed.
- `docs/development-guidelines.md` and `docs/architecture.md` describe the implemented
  lint and coverage commands, canonical source scope, four enforced thresholds, and
  scope-change record without claiming that physical-device validation is automated.
- `docs/repository-structure.md` documents root `eslint.config.js` and its ownership.

## Sequencing and Dependencies

- This feature has no runtime or schema dependency on
  `docs/ideas/20260718_2-managed-preview-presets.md`.
- Managed-preview-presets has already been implemented first; both the reference and
  canonical baselines for this feature include its Mobile modules and tests.
- Before a Phase 2B release candidate is accepted, this quality-gate feature must be
  implemented and both commands must pass against the combined codebase.
- If the next feature lands first, its new Mobile modules and tests are included in the
  coverage denominator; the quality task must not freeze or measure only the earlier
  source set.

## Durable Docs Impact

- Update `docs/development-guidelines.md` with the exact lint, test, and coverage
  commands, canonical coverage scope, all four floors, scope-change procedure, and the
  rule for documenting suppressions.
- Update `docs/architecture.md` quality checks with the same canonical commands, source
  scope, all four floors, and scope-change procedure.
- No product requirements, functional behavior, or glossary changes are expected. The
  new maintained root configuration path is the repository-structure change below.
- Update `docs/repository-structure.md` to document the new root `eslint.config.js` and
  its responsibility for Mobile JavaScript lint policy.

## Open Questions

- Whether the hosted CI provider will later run one aggregate `validate` script or call
  the individual quality commands directly.
- Whether a later quality increment should raise branches and functions to 80% or adopt
  per-module thresholds for safety-critical upload, integrity, and save flows.
