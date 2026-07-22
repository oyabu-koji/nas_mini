# Feature Spec

## Metadata

- Date: 2026-07-18
- Feature name: Managed video preview presets and test LUT pipeline
- Status: implemented and formally validated on 2026-07-21
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/repository-structure.md`
  - `docs/ideas/20260711_3-resumable-original-finalization.md` (verified-original prerequisite)
  - `docs/ideas/20260718_1-processed-video-delivery.md` (result delivery prerequisite)
  - `docs/ideas/20260711_2-apple-log-preview.md` (subsequent Apple Log detection feature)
  - `docs/ideas/iphone_applelog_app_distribution_and_lut_policy.md`

## Background

The application needs a server-owned way to expose available processing presets before
Apple Log to Rec.709 is enabled. The first presets are `compress-only`, a generated
identity LUT, and a generated visibly changing test LUT. They prove the selection,
manifest, validation, ffmpeg, result, and delivery paths without presenting any test
LUT as an Apple-owned or Apple Log to Rec.709 transform.

Preset files and their authority remain on the Mac mini. Mobile may select only safe
metadata returned by the server and must never upload, enumerate, or name arbitrary LUT
files. A missing preset is an availability condition with a successful `compress-only`
fallback; a registered but invalid or unreadable preset is an operational failure that
must remain visible.

## Target Users / Use Cases

- A user who wants to choose between lightweight-only, identity, and test-LUT processing
  for a verified video result.
- A Mac mini administrator who adds a manifest-backed custom LUT without placing it in
  Git or the Docker image.
- A developer who needs reproducible evidence that a selected LUT was validated and
  applied to a specific derived result.

## Scope

- Run only for a session-derived Phase 2A video that satisfies the existing shared
  delivery predicate: `verification_status = file_verified`,
  `preview_status = preview_ready`, an active ready video result with matching stored
  size/SHA-256 and valid storage, and no legacy LOG safety gate. A preset never changes
  the original or its SHA-256 verification state.
- Define a server-managed preset catalog. `compress-only` is a built-in virtual preset
  and is always returned as enabled. It requires no LUT file and performs the standard
  lightweight H.264/AAC/1080p rendering path.
- Add generated identity and visibly changing test LUT presets. The generators, their
  exact versions and parameters, output SHA-256 values, test fixtures, and manifests are
  maintained in the repository. Neither preset claims Apple Log conversion or Rec.709
  output.
- Permit custom LUT presets only from a configured, repo-external `USER_LUT_ROOT` that
  is read-only to the worker. Each custom preset requires a sidecar manifest and a file
  whose canonical resolved path remains inside that root.
- Define a manifest with at least preset ID, display name, enabled state, preset kind,
  version, source/origin reference, license or terms reference, optional declared target
  color space, LUT relative path, file SHA-256, file format, grid size, and manifest
  SHA-256. Manifest schema version `1` uses the canonical digest contract below.
  `compress-only` has an explicit manifest-equivalent record with no LUT path.
- Validate every enabled LUT before it becomes selectable and again immediately before
  ffmpeg use: confined path, regular file, configured size limit, supported `.cube`
  format, supported grid size, finite numeric values, declared hash, and manifest hash.
- Add authenticated, versioned read APIs for server capabilities and the selectable
  preset catalog. Responses expose only safe metadata: IDs, display names, kinds,
  enabled/available state, version, declared target color space, and API capability
  flags. They never expose local paths, raw LUT contents, tokens, or full manifests.
- Mobile obtains presets from the configured server, shows only returned enabled
  selections, and snapshots the chosen preset ID into a new rendering request for the
  verified asset. A new requested preset creates a new derived rendition and immutable
  `processed_result` from the same original; it never overwrites historical derived bytes.
- Persist a rendition's requested preset, applied preset, preset version, LUT SHA-256,
  manifest SHA-256, and transform state with the resulting derived file and immutable
  `processed_result`. It has its own result ID, size, SHA-256, MIME type, and the
  applicable preview generation (null during Phase 2A delivery and required after the
  Phase 2B migration). The active-result pointer changes atomically only after those
  values are committed and must satisfy the delivery feature's `processed_results`
  foreign-key, same-asset, ready-state, and unique-derived-file invariants. Historical
  derived files and results remain audit records until retention removes them.
- Persist a durable `rendition` request record and one-to-one rendition provenance. The
  rendition owns request idempotency, selection ordering, job state, and its nullable
  result reference. Provenance is keyed to the derived file/result and owns the requested
  and applied preset evidence; these fields are not added as mutable identity columns on
  `processed_results`.
- When a requested LUT preset is classified `absent` or `disabled`, render
  `compress-only`, complete the job, and persist `color_transform_status = unavailable`
  and `color_transform_error_code = lut_preset_unavailable`. A direct request for
  `compress-only` instead records `color_transform_status = not_requested` with no error.
  A `registered_invalid` preset or post-snapshot validation/application failure fails the
  rendering job and exposes no rendition for playback or download.
- Make result delivery from the preceding feature available only for an eligible ready
  rendition. Saving a rendition does not confirm preview review or permit original
  deletion.

## Out of Scope

- Apple Log auto-detection, Apple Log 2, Apple Log to Rec.709 generation, or a claim
  that an identity/test/custom LUT is a Rec.709 conversion.
- Uploading a LUT from Mobile, allowing a Mobile path or arbitrary LUT URL, creative-LUT
  authoring, or an administrator UI/API for editing manifests.
- Extraction, copying, or redistribution of LUTs from Final Cut Pro, another Apple app,
  or any source without confirmed terms.
- Background batch rendering, automatic preset selection, cloud deployment, QR setup,
  and photo-library save behavior beyond the preceding delivery feature.
- Rewriting existing originals, automatically deleting iPhone originals, or making
  `safe_to_delete_candidate` depend on a test-LUT result.

## User Flow

1. Mobile reads the server capabilities and preset catalog using the manually configured
   Backend URL and SecureStore token.
2. The user selects `compress-only`, identity, a test preset, or an enabled custom
   preset returned by that server. Mobile sends only the preset ID, never a LUT file.
3. After Phase 2A has produced an immutable original and an active result that satisfies
   the shared delivery predicate, Mobile requests a rendition for that asset and selected
   preset. A not-ready or legacy LOG safety-gated asset remains unavailable here.
4. The Backend snapshots the requested preset, validates the manifest and any LUT file,
   and queues a rendering job.
5. For `compress-only` or an unavailable requested preset, the job produces a
   lightweight result and records the fallback state. For a valid LUT, it applies the
   LUT and records the exact applied evidence. For an invalid registered LUT, it fails
   without exposing a result.
6. Mobile shows the requested and applied preset plus transform state. It may play or
   save only the ready result supplied by the delivery feature.
7. Selecting a different preset later creates a new rendition from the same original;
   it does not mutate an earlier result or the original.

## Functional Requirements

### Catalog and Manifest Contract

- `GET /api/v1/capabilities` identifies the API version, minimum supported client
  version when required, and feature flags for result delivery, managed presets, custom
  LUT support, and generated Apple Log conversion. A disabled future feature is reported
  as unavailable rather than implied by a preset name.
- `GET /api/v1/presets` always returns `compress-only` and returns only enabled,
  successfully validated LUT presets. The response is stable enough that an older client
  can ignore newly added fields and unknown preset kinds.
- Preset IDs are server-owned lowercase kebab-case identifiers. Mobile treats IDs and
  labels as data and does not hard-code an Apple Log or custom LUT list.
- A custom manifest may describe provenance and terms, but the response exposes only a
  bounded human-readable attribution/reference suitable for the UI. It never exposes a
  filesystem path or full license text.
- Validation rejects symlinks escaping `USER_LUT_ROOT`, unsupported or oversized files,
  duplicate preset IDs, malformed cube headers/data, non-finite entries, unsupported
  grids, and hash or manifest mismatches.
- Schema version `1` accepts only a three-dimensional `.cube` LUT with exactly one
  `LUT_3D_SIZE` in the supported set `17`, `33`, or `65`, exactly the declared number of
  RGB rows, and a maximum file size of 16,777,216 bytes. The manifest is limited to
  65,536 bytes. Limits are checked before parsing and enforced again during snapshot
  copy; raising them requires a later schema/configuration decision and matching tests.
- A version `1` manifest is a UTF-8 JSON object without a byte-order mark. Duplicate JSON
  keys, an unsupported `schema_version`, unknown version-1 fields, non-integer numeric
  manifest values, and a root value other than an object are rejected. Preset and digest
  text is compared using its exact JSON string value; filenames and labels are not used
  as security identifiers.
- `manifest_sha256` is a lowercase 64-character hexadecimal string. Its digest input is
  the RFC 8785 JSON Canonicalization Scheme byte sequence of the complete manifest after
  removing only the top-level `manifest_sha256` member. The same rule applies to built-in
  and custom manifests. The LUT file SHA-256 is the lowercase hexadecimal digest of the
  exact file bytes. Unknown fields cannot be discarded before hashing.
- A custom preset candidate is identified by a server-owned lowercase kebab-case
  directory/registry ID, not by a display label or a manifest supplied path. The registry
  classifies the requested ID at rendition creation as exactly one of:
  `absent`, `disabled`, `registered_invalid`, or `valid`.
  - `absent`: there is no registry candidate for the requested ID.
  - `disabled`: a structurally valid manifest for that ID explicitly has `enabled=false`.
  - `registered_invalid`: a candidate exists but its manifest, path, file, format,
    hashes, schema, or contents fail validation.
  - `valid`: the manifest and LUT pass every catalog-time validation.
- Only `absent` and `disabled` select the successful `compress-only` fallback.
  `registered_invalid` is a terminal rendition failure. A job created from a `valid`
  registry snapshot is terminally failed when it cannot acquire and reproduce the
  expected LUT bytes because the source is missing, unreadable, or mismatched; it is never
  reclassified as `absent` or silently downgraded. Directory-entry replacement after a
  validated descriptor is open follows the immutable worker-input contract below.

### Rendering and Provenance Contract

- Add authenticated `POST /api/v1/assets/{asset_id}/renditions`. Its JSON body contains
  only `client_rendition_request_id` and `preset_id`; it never accepts a local URI,
  filesystem path, LUT bytes, manifest, or ffmpeg arguments. Both client request IDs and
  server `rendition_id` values are opaque lowercase 32-character UUID hex strings.
- The endpoint first resolves an existing client request ID. An exact idempotent replay
  returns its original rendition without re-evaluating current eligibility. Before
  creating a new rendition, the endpoint resolves the asset and calls the existing
  Phase 2A shared delivery eligibility service. Unknown assets return `404`.
  An image, non-session video, non-`file_verified` or non-`preview_ready` asset, missing or
  invalid active result, storage-integrity failure, and every legacy LOG safety-gated
  state return `409 rendition_asset_not_eligible`. Rejection creates no rendition/job,
  does not increment selection generation, and does not change preview/review state.
- Mobile generates and durably stores `client_rendition_request_id` before the POST.
  A global unique constraint maps one client request ID to exactly one asset and preset.
  Replaying the same ID, asset, and preset returns the original rendition without a new
  job or generation increment. Reusing the ID with another asset or preset returns
  `409 rendition_request_conflict`. A deliberate new render, including the same preset,
  uses a new client request ID.
- A new request increments `assets.rendition_selection_generation` and stores that value,
  the requested preset ID, registry classification, canonical manifest digest, expected
  LUT digest/version when applicable, the bounded canonical manifest bytes and safe
  parsed fields when a manifest exists, and a unique job dedup key derived from the
  rendition ID in one transaction. The job never re-reads a mutable manifest as its
  authority. Idempotent replay does not increment the generation.
- New asynchronous work returns `202`; an idempotent replay returns `200` with the same
  representation. The response contains only rendition ID, asset ID, client request ID,
  selection generation, requested/applied preset IDs, rendition state, transform state,
  safe error code, timestamps, and nullable exact `result_id`. It exposes no token,
  filesystem path, LUT bytes, or full manifest.
- Authenticated `GET /api/v1/assets/{asset_id}/renditions/{rendition_id}` uses the same
  bearer-token and asset-access policy as the POST and returns that same safe
  representation for polling. Missing or invalid authentication is rejected before
  resource lookup. Rendition states are `queued`, `validating`, `rendering`, `finalizing`,
  `ready`, `failed`, and `superseded`; the initial API does not claim a synthetic
  percentage. Unknown or cross-asset IDs return `404 rendition_not_found` without
  revealing whether the rendition exists under another asset.
- A rendition job records the requested preset and registry classification before worker
  validation. `absent` and `disabled` follow the explicit `compress-only` fallback.
  `registered_invalid` and validation failure of a previously `valid` snapshot terminate
  with no deliverable result. Stable codes include `lut_preset_registered_invalid` for an
  invalid registry candidate, `lut_preset_source_changed` for missing or mismatched
  source bytes after a valid request snapshot, and `lut_application_failed` for ffmpeg
  rejection. They never contain a path or parser output.
- The one-to-one rendition provenance record holds requested and applied preset evidence.
  `transform_kind = none` is used for `compress-only`; `transform_kind = lut` is used
  only when ffmpeg applied a validated LUT. Identity and test transforms are labelled by
  their actual preset names and never as Apple Log to Rec.709.
- A retry or a new preset selection cannot replace historical bytes in place. It creates
  a new rendition and `processed_result` linked to the same asset, then atomically
  changes the active-result pointer only after the new result is ready. Delivery always
  receives an explicit result ID; an inactive historical result is audit-only and its
  download request returns `409 processed_result_superseded` rather than another result.
- Worker finalization re-reads `assets.rendition_selection_generation` in the same
  transaction that creates rendition provenance and the immutable `processed_result`.
  Only a rendition whose stored generation still matches may supersede the old active
  result and change `active_processed_result_id`. An older request that finishes later is
  recorded as `superseded` and must not change the asset pointer, preview/review state, or
  a newer rendition. Result/provenance/job/rendition writes and the allowed active pointer
  change commit atomically; promoted files are cleaned up or retained as explicit
  inactive audit results according to the finalizer outcome, never substituted.
- Finalization also re-evaluates the Phase 2A asset-side delivery invariants and evaluates
  the proposed new result as the candidate active result: the asset remains
  `file_verified` and `preview_ready`, is not legacy LOG safety-gated, and the candidate
  result/provenance/storage relation is ready and valid. If any check fails, the rendition
  terminates with `rendition_asset_not_eligible`, leaves the existing active pointer and
  preview/review state unchanged, and exposes no candidate result for delivery. This
  feature never moves a legacy LOG asset from `failed` to `preview_ready`; only Phase 2B
  may replace that safety boundary with formal detection provenance.
- Before Phase 2B, an active managed rendition is deliverable through the existing exact
  result endpoint only when all existing Phase 2A checks remain true and its one-to-one
  rendition provenance is internally valid. This extends the candidate-result branch of
  the shared service without weakening its asset-state, legacy LOG, active-pointer, or
  storage checks. It does not set `formal_preview_id`, claim Apple Log detection, confirm
  review, or provide safe-delete evidence. Phase 2B later requires matching formal
  preview ID, preview generation, and detection provenance.
- Preview confirmation and safe-delete logic remain bound to their existing formal
  preview contract. This feature does not silently use a test-LUT rendition as evidence
  that an iPhone original may be deleted.

### Immutable Worker Input Contract

- Catalog validation alone does not authorize a path for ffmpeg. For every `valid` LUT
  job, the worker performs root-anchored, no-follow descriptor traversal under
  `USER_LUT_ROOT`, verifies each opened object is the expected regular file, and copies
  bounded bytes into a backend-generated job-private snapshot under `MEDIA_ROOT` using
  exclusive creation. It hashes during copy and rejects a digest mismatch.
- The worker reads the canonical manifest bytes and parsed fields stored durably at
  rendition creation and verifies their digest; it does not reopen the custom manifest.
  The manifest record and LUT snapshot are immutable before use. Immediately before
  ffmpeg, the worker reopens only the backend-generated LUT snapshot, verifies its size
  and SHA-256 again, and passes only that snapshot path to ffmpeg. It never passes the
  mutable custom-root path.
- Failure to acquire the expected no-follow descriptor, a non-regular descriptor,
  truncation/read failure, byte-limit overflow, copy digest mismatch, or snapshot rehash
  mismatch is terminal. Source disappearance after a `valid` request is not an
  unavailable fallback. After the worker has opened and validated a descriptor, replacing
  its directory entry does not itself fail the job: the worker never resolves that path
  again and may proceed if bytes read from the already-open descriptor produce the
  expected, reverified snapshot. It must never consume replacement bytes through a new
  path lookup. Job-private snapshots are removed after a terminal outcome; provenance
  retains hashes and safe source/terms references, not LUT bytes or local paths.

### Mobile Contract

- Mobile fetches the catalog after server configuration is available and handles
  authentication, reachability, incompatible-client, and empty-catalog errors without
  inventing local presets.
- A valid catalog response always contains the server-returned `compress-only` entry,
  even when it contains no enabled LUT. If that required entry is absent or malformed,
  Mobile reports a catalog error and does not synthesize a local preset. It presents
  unavailable fallback and terminal registered-LUT failure as different states.
- The UI must not label identity, test, or custom output as Apple Log or Rec.709 unless
  a later approved feature provides that exact claim and evidence.
- Mobile persists an outstanding client request ID until the server returns its durable
  rendition representation. Network timeout retries reuse that ID. Selecting another
  preset is an explicit new request and the UI follows only that newer rendition as the
  current selection; completion of an older poll cannot replace it on screen.

## Non-Functional / Technical Notes

- Keep React Native + Expo managed workflow, JavaScript, FastAPI, SQLite, ffmpeg, and
  immutable-original storage. Native photo-library saving remains owned by the preceding
  delivery feature.
- Managed generator source and test fixtures may live in the Backend image/repository.
  User LUT files must not. The Docker deployment mounts `USER_LUT_ROOT` read-only.
- Test validation must cover identity behavior with deterministic synthetic input, a
  visibly changing test LUT, manifest mutation, LUT mutation after registration,
  malformed headers, invalid grids, NaN/Inf values, symlink escape, unavailable preset
  fallback, and ffmpeg rejection. Descriptor/snapshot integration tests replace or relink
  the custom source before descriptor acquisition and after a validated descriptor is
  open. They prove that ffmpeg receives either the original verified snapshot bytes or no
  LUT at all, never bytes obtained by resolving the replacement path.
- API/worker tests cover idempotent POST replay, request-ID payload conflict, unique job
  creation, status polling, A-then-B completion in both orders, stale-generation pointer
  rejection, and rollback at each finalization write.
- Eligibility tests cover an accepted normal Phase 2A video, non-ready and integrity-
  failed states, a legacy LOG safety-gated asset, and an asset that becomes ineligible
  while its job is running. No rejected case changes asset/review state or active result.
- No LUT file content, raw complete media metadata, local URI, token, or server path may
  appear in API responses or routine logs.

## Acceptance Criteria

- `compress-only` is returned for every authenticated compatible server and can produce
  a downloadable ready result only from an asset satisfying the complete Phase 2A shared
  delivery predicate.
- A direct `compress-only` request records `not_requested` without an unavailable error;
  only fallback from an absent or disabled requested LUT records `unavailable` and
  `lut_preset_unavailable`.
- Generated identity and test presets are reproducible from versioned generator inputs;
  their manifests and SHA-256 values validate before they are selectable.
- Mobile cannot select a preset that the server did not return, and it never uploads LUT
  bytes or a local path.
- A missing or disabled requested preset completes with `compress-only`,
  `color_transform_status = unavailable`, and
  `color_transform_error_code = lut_preset_unavailable`.
- An altered, malformed, hash-mismatched, escaping, or ffmpeg-rejected registered LUT
  fails the rendition and exposes neither playback nor download for that failed result.
- Applying a new preset leaves the original and earlier derived rendition bytes intact,
  and records the exact requested/applied preset evidence for the new result.
- Identity, test, and custom LUT output is never displayed as Apple Log to Rec.709.
- Manifest digest verification is reproducible from the schema-version-1 canonical bytes,
  rejects duplicate keys and self-hash ambiguity, and produces the same digest in
  generator, registry, worker, and provenance tests.
- `absent` and `disabled` requests alone use fallback. Every registered-invalid or
  post-snapshot source/hash/format failure terminates without a deliverable result.
- Retrying a POST with the same client request ID creates one rendition/job. Reusing that
  ID for different input returns `409`, and out-of-order completion cannot make an older
  selection active after a newer selection.
- A custom LUT replaced during validation or before ffmpeg is never consumed from its
  mutable source path; only a size/hash-verified job-private snapshot may be applied.
- A normal eligible Phase 2A video can create and activate a managed rendition. A
  non-ready, integrity-failed, non-session, or legacy LOG safety-gated asset receives
  `409 rendition_asset_not_eligible`, creates no work, and cannot be made ready by this
  feature. If eligibility is lost during processing, finalization cannot switch the
  active result.

## Open Questions

- The maximum number and retention period for historical renditions per asset.
- Whether the initial rendition request should be made immediately after finalization or
  only after an explicit selection in Asset Detail; both must preserve the same immutable
  original and preset snapshot contract.
- Whether a later API revision should add ffmpeg-derived numeric progress. The initial
  contract intentionally exposes only durable phase states.

## Durable Docs Impact

- Update candidates: `product-requirements.md`, `functional-design.md`,
  `architecture.md`, `development-guidelines.md`, `glossary.md`, and
  `repository-structure.md`.
- Update timing: after this feature and the delivery feature are reviewed together and
  their result/rendition model is confirmed.
- Reason: managed preset catalog, rendition provenance, and API capabilities are stable
  architecture and user-flow changes, but the exact resource model must first be agreed.
