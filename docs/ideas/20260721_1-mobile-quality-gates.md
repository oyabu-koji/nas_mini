# Feature Spec

## Metadata

- Date: 2026-07-21
- Feature name: Mobile lint and coverage quality gates
- Status: draft
- Priority: project-wide quality improvement; non-blocking for the next product feature,
  but required before the Phase 2B release candidate is accepted
- Related files:
  - `docs/architecture.md`
  - `docs/development-guidelines.md`
  - `package.json`
  - `jest.setup.js`
  - `.gitignore`
  - `.steering/20260718_1-processed-video-delivery/tasklist.md`

## Background

The processed-video-delivery implementation passes its Backend, Mobile, Expo dependency,
iOS export, and Compose checks. Its final validation nevertheless identified two
project-wide quality gaps:

- `npm run lint` is documented as a Mobile quality command but no lint script or ESLint
  configuration exists.
- The measured Mobile coverage is 75.24% statements and 75.58% lines. The initial
  project quality target is 80%.

These gaps are not runtime defects in processed-video delivery and do not block work on
the next product feature. They should be handled as one explicit quality-gate feature so
that later Phase 2B work does not increase an unmeasured lint or test deficit.

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
- Preserve the current branch and function coverage as explicit minimum floors while
  statements and lines reach the initial 80% gate. Raising every metric to 80% may be a
  later quality increment and must not be implied by this feature.
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

## Current Baseline

- `npm test`: 17 suites and 75 tests pass.
- Global coverage:
  - statements: 75.24%
  - branches: 65.91%
  - functions: 76.06%
  - lines: 75.58%
- No `lint` or `test:coverage` package script exists.
- No ESLint configuration or ESLint development dependency exists.

The implementation must measure and record a fresh baseline before editing tests. If a
tooling update changes the coverage calculation, the steering plan must explain the
difference rather than silently lowering the thresholds.

## Functional Requirements

### Lint Contract

- `npm run lint` checks the intended Mobile JavaScript/JSX source, tests, and maintained
  root JavaScript configuration without modifying files.
- The configuration supports React, React Native, Jest globals, and React Hooks rules as
  used by the current Expo managed application.
- Generated export output, coverage output, `node_modules`, Backend Python, media files,
  and temporary directories are excluded explicitly.
- The lint command exits non-zero for errors. The initial implementation must also
  either finish with zero warnings or make the accepted warning budget explicit and
  enforce no increase; silently accumulating unbounded warnings is not allowed.
- An optional `lint:fix` command may be provided, but validation and future CI use the
  non-mutating `lint` command.
- Rule suppressions are local and justified. Repository-wide disabling of Hooks,
  undefined-variable, unreachable-code, or equivalent correctness rules is prohibited.

### Coverage Contract

- `npm run test:coverage` runs the normal Jest suite with coverage enabled and returns the
  same behavioral result as `npm test`.
- The command enforces global thresholds of at least 80% statements and 80% lines.
- The initial branch floor is at least 65% and the initial function floor is at least
  76%. The implementation may choose higher floors based on its final measured values,
  but may not set either below the current baseline after accounting for normal rounding.
- Coverage is collected from maintained Mobile source modules, not only files imported
  incidentally by the current tests. Any exclusion beyond entry/bootstrap code,
  generated code, or static configuration requires a documented technical reason.
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
  `package-lock.json` change.
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
  80%, branches below the accepted baseline floor, or functions below the accepted
  baseline floor.
- The final report shows at least 80% global statements and lines coverage with all Jest
  suites passing.
- Added tests exercise real public behavior and materially cover previously untested
  success/error branches; broad exclusions or no-op assertions are not used.
- `npm test`, Expo dependency validation, iOS export, Backend pytest, and
  `git diff --check` remain successful.
- Coverage/export output is ignored and no generated report is committed.
- `docs/development-guidelines.md` and `docs/architecture.md` describe the implemented
  lint and coverage commands and their enforced thresholds without claiming that
  physical-device validation is automated.

## Sequencing and Dependencies

- This feature has no runtime or schema dependency on
  `docs/ideas/20260718_2-managed-preview-presets.md`.
- Planning and implementation of the managed-preview-presets feature may proceed first.
- Before a Phase 2B release candidate is accepted, this quality-gate feature must be
  implemented and both commands must pass against the combined codebase.
- If the next feature lands first, its new Mobile modules and tests are included in the
  coverage denominator; the quality task must not freeze or measure only the earlier
  source set.

## Durable Docs Impact

- Update `docs/development-guidelines.md` with the exact lint, test, and coverage commands
  and the rule for documenting suppressions.
- Update `docs/architecture.md` quality checks with the enforced coverage thresholds.
- No product requirements, functional behavior, glossary, or repository ownership
  changes are expected unless implementation introduces a new maintained config path.
- If a new lint config file is added at the repository root, update
  `docs/repository-structure.md` to document it.

## Open Questions

- Whether the hosted CI provider will later run one aggregate `validate` script or call
  the individual quality commands directly.
- Whether a later quality increment should raise branches and functions to 80% or adopt
  per-module thresholds for safety-critical upload, integrity, and save flows.
