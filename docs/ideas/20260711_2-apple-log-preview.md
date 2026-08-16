# Feature Spec

## Metadata

- Date: 2026-07-11
- Feature name: Apple Log detection and formal preview provenance
- Status: implemented as the Phase 2B baseline; detector/classifier, schema gate, and
  Mobile contract superseded by `20260802_1-apple-log-container-signaling-detection`
- Successor validation status: detector-v2 fixture certification and full release validation
  are tracked in the successor `.steering/` tasklist
- Initial release decision: formal preview uses automatic preset resolution only;
  identity, test, and custom selections remain separate managed renditions
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/repository-structure.md`
  - `docs/ideas/20260711_3-resumable-original-finalization.md` (Phase 2A prerequisite)
  - `docs/ideas/20260718_1-processed-video-delivery.md` (result-delivery prerequisite)
  - `docs/ideas/20260718_2-managed-preview-presets.md` (managed-preset prerequisite)
  - `docs/ideas/20260802_1-apple-log-container-signaling-detection.md` (current successor contract)
  - `docs/ideas/iphone_applelog_app_distribution_and_lut_policy.md`

## Background

The current LOG safety gate correctly prevents an identity LUT from being presented as
an Apple Log to Rec.709 conversion, but it also keeps a verified Apple Log original from
being reviewed when no transform is available. Phase 2B must preserve the exact detection
and transform evidence for every ready Apple Log preview while allowing a clearly labelled
unconverted `compress-only` result when a requested preset is unavailable.

This specification is now the third implementation stage. Processed-video delivery is
defined first, and managed preset discovery, validation, and generic LUT rendering are
defined second. This stage adds high-confidence Apple Log detection and the provenance
gate that makes an Apple Log fallback safe to play, confirm, and deliver without claiming
Rec.709 conversion.

## Successor Relationship (2026-08-15)

This document remains the decision history and baseline for Phase 2B formal preview,
generation fencing, provenance, delivery, confirmation, and unconverted fallback. The
following contracts are replaced by
`docs/ideas/20260802_1-apple-log-container-signaling-detection.md`:

- schema-v1 tag/color-metadata detection is replaced by detector-v2 same-fd bounded ISO
  BMFF `logs` parsing plus FFprobe track/color correlation;
- the single Apple Log profile is replaced by closed `apple-log-1` and `apple-log-2`
  profiles with separate requested preset IDs;
- detector rule/manifest/certificate schema v1 is replaced by strict schema v2 artifacts;
- the Phase 2B/2C schema authority is extended by offline successor
  `010_apple_log_container_signaling` without modifying migrations 008 or 009;
- the relevant Phase 2 asset-specific client floor is raised to Mobile 0.4.0.

Where this document conflicts with the successor, the successor is authoritative. In
0.4.0 both Apple Log profiles remain explicitly unconverted `compress-only` previews.
Neither document implements, registers, enables, or applies an Apple Log-to-Rec.709 LUT;
that remains a future separately reviewed feature.

## Implementation Gate

The implementation may add the detector parser, schema, migration, API, and Mobile UI
before local media fixtures are available, but it must not enable Phase 2B processing until
all gates below pass.

1. The implemented and validated processed-result delivery, managed-preset, and Mobile
   quality-gate features are prerequisites. The managed registry must expose a valid virtual
   `compress-only` record and its immutable requested/applied-preset snapshot contract.
2. Before probing media, the repository owner authors and explicitly approves
   `backend/assets/detectors/apple-log-v1/detector-rule-input-v1.json`. This canonical schema-v1
   input contains only `detector_id`, rule version, an ordered `apple_log` all-of rule, an
   ordered `not_log` all-of rule, and per-predicate allowlisted path, `equals` or `present`
   operator, expected value when applicable, rationale, and non-fixture source reference. It
   also records the approving role, ISO 8601 approval time, and stable approval reference.
   Approval is a reviewed repository change owned by the repository owner; neither a script
   nor a fixture comparison may author, add, remove, or change a predicate. The same reviewed
   change stores its JCS SHA-256 as one lowercase 64-hex line in the adjacent
   `detector-rule-input-v1.sha256`; certification rejects a missing, malformed, or mismatched
   sidecar.
3. A user-owned Apple Log recording and an ordinary non-Log recording are supplied from a
   repository-external directory to
   `uv run --directory backend python scripts/certify_apple_log_detector.py --rule-input assets/detectors/apple-log-v1/detector-rule-input-v1.json --fixture-root <absolute-path>`
   from the repository root. Other working directories and path forms are not part of the
   operator contract.
   Media files, local paths, and raw ffprobe output are not committed or logged. Repository
   fixtures may contain only sanitized bounded probe JSON, expected classifications, source
   labels, and media SHA-256 values.
4. Certification validates the immutable rule input and writes and verifies
   `backend/assets/detectors/apple-log-v1/manifest.json`. Schema version `1` rejects duplicate
   keys, unknown fields, a BOM, non-canonical JSON, unsupported path/operator syntax, or a
   digest mismatch. The manifest pins the canonical rule-input SHA-256 and copies its rules
   byte-for-byte; it cannot infer rules from probe output or fixture differences. Canonical
   digests use the same JCS and SHA-256 rules as managed preset manifests.
5. The detector manifest pins `detector_id`, rule version, exact `ffprobe -version` first
   line, runtime `-show_entries`, rule-input SHA-256, exact-match predicates, fixture
   SHA-256/role/expected classification, source reference, `timeout_ms = 15000`,
   `max_stdout_bytes = 1048576`, `max_stderr_bytes = 1048576`, and
   `max_evidence_bytes = 4096`. Predicate paths are limited to allowlisted format tags,
   first-video-stream tags, disposition, and declared color metadata; operators are limited
   to exact equality and presence. Arbitrary expressions, regex, filenames, `is_log`, and
   pixel heuristics are forbidden.
   The Backend Dockerfile also pins the base image digest and the ffmpeg package version
   that supplies this exact ffprobe build; an unpinned or changed build cannot certify.
6. Certification runs the pinned Docker ffprobe against both external recordings and proves
   only that the already-approved predicates yield their expected classifications. Rule-input
   absence or digest mismatch and any attempt to infer a predicate from fixture differences
   are rejected. A sanitized unsupported/ambiguous metadata fixture proves `unknown`; an Apple
   Log 2 fixture is added only when a controlled source exists and otherwise remains a
   sanitized negative contract fixture. A deterministic `certificate-summary.json` records
   only the manifest digest, rule-input digest, ffprobe version, and fixture role/SHA-256 pairs.
7. Startup validates the manifest and confirms the runtime ffprobe version before reporting
   `formal_apple_log_preview = true`. Until then, existing Phase 2A behavior remains active,
   the Phase 2B migration is not run, and no profile-aware preview job is created. This keeps
   existing ready previews intact rather than partially enabling detection.
8. `generated-apple-log-rec709` remains absent or disabled until a later dedicated feature
   records its legal/source evidence, generator or library version, parameters, LUT SHA-256,
   Rec.709 output tags, and fixture comparison result. This feature does not create, bundle,
   extract, enable, or imply that transform.

After a certified detector reports `apple_log`, the automatic requested preset is always
`generated-apple-log-rec709`, even when it is absent or disabled. In that availability case,
the applied preset is `compress-only`, the job succeeds, and provenance preserves the
unavailable requested preset. A registered preset that is malformed, altered, hash-mismatched,
or rejected by ffmpeg is a terminal failure. Availability and integrity are distinct outcomes.

## Target Users / Use Cases

- An iPhone user who records Apple Log and needs to review a verified backup before an
  approved Rec.709 transform becomes available.
- A user who must distinguish a transformed result, an explicitly unconverted Apple Log
  result, and an ordinary video result.
- A Mac mini administrator who enables managed presets under the preceding feature but
  must not allow arbitrary Mobile LUT input or mislabel a custom LUT as Apple Log to
  Rec.709.

## Scope

- Run only after Phase 2A finalizes a video original with
  `verification_status = file_verified`. Chunk upload, assembly, unverified originals,
  and Phase 1 direct assets are outside the processing boundary.
- Record `log_detection_status` as `apple_log`, `not_log`, or `unknown`, plus nullable
  profile name, detector rule version, detector-manifest SHA-256, and bounded evidence
  summary. The legacy Mobile `is_log` field remains audit-only and never authorizes a
  transform or detection result.
- Initially recognize only the approved `apple_log` profile. Apple Log 2, unsupported
  future profiles, and ambiguous metadata are `unknown`.
- Resolve formal preview presets automatically. `apple_log` always requests
  `generated-apple-log-rec709`; `not_log` and `unknown` request `compress-only`. Existing
  identity, test, and custom selections remain managed renditions with their own selection
  generation and never become formal preview intent or evidence in this feature.
- Create formal preview provenance for each ready Phase 2B session-derived video. It
  records detection evidence; requested and applied preset; transform kind; color
  transform state; and, when a LUT was actually applied, the preset version, LUT SHA-256,
  and preset-manifest SHA-256. `formal_preview_id -> preview_provenance.result_id` is the
  current formal authority, and the result's generation must equal the asset generation.
  `active_processed_result_id` continues to identify the current managed rendition when one
  exists; otherwise it points to the current formal result.
- For a detected Apple Log fallback, require `transform_kind = none`,
  `applied_preset_id = compress-only`,
  `color_transform_status = unavailable`, and
  `color_transform_error_code = lut_preset_unavailable`. Mobile displays this result as
  unconverted Apple Log, never as Rec.709.
- Replace the temporary LOG safety trigger with Phase 2B SQLite constraints that reject
  `preview_ready` for a verified session-derived video unless `formal_preview_id` resolves
  to matching current formal provenance. The constraint does not require
  `active_processed_result_id` to equal the formal result. A direct database update or an
  old worker cannot bypass this gate.
- Migrate only Phase 2A session-derived `file_verified` videos. The migration creates one
  profile-aware preview job per eligible asset and fences old-generation preview jobs so
  they cannot overwrite the current formal relation, managed relation, review state, or
  provenance.
- Permit playback, confirmation, and the preceding result-delivery feature only for the
  current provenance-backed formal preview. Exact-result delivery separately permits the
  latest successfully finalized managed rendition identified by its active pointer, ready
  result/rendition, and rendition provenance. A newer failed or superseded selection does not
  invalidate that successful authority. An Apple Log fallback is eligible because the user
  is confirming an immutable verified original and a visibly unconverted derived result, not
  a color-quality claim.
- Require Mobile version `0.2.0` or later when the Backend advertises
  `formal_apple_log_preview = true`; older clients remain safe but cannot confirm or deliver
  a Phase 2B formal preview.

## Out of Scope

- Processed-video download/save, generic preset catalog management, identity/test LUT
  generation, custom LUT manifest authoring, and Mobile preset upload; these belong to
  the preceding feature ideas.
- The implementation or enabling of Apple Log to Rec.709 conversion. It needs a future
  dedicated feature after the implementation gate is met.
- Pixel-based LOG inference, filename inference, manual override for an unknown profile,
  Apple Log 2, HDR targets, multiple output color spaces, or creative-LUT authoring.
- Backend original deletion, automatic iPhone deletion, preview retry UI, cloud review
  infrastructure, or App Review configuration.
- Reclassifying Phase 1 direct assets or serving historical identity-LUT files as formal
  Apple Log previews.
- Promoting an existing managed rendition, or an identity, test, or custom preset selection,
  into the formal preview/review/delete flow. A later feature may add an explicit formal
  rerender command with its own idempotency and review-reset contract.

## User Flow

1. Phase 2A retains and verifies a video original.
2. A profile-aware worker validates the certified detector manifest and exact ffprobe
   version, probes bounded allowlisted metadata, and persists `apple_log`, `not_log`, or
   `unknown` with the detector evidence snapshot.
3. The worker resolves automatic preset intent after detection and stores an immutable
   formal-preview attempt snapshot. It never reads a managed rendition selection.
4. For `apple_log`, the attempt requests `generated-apple-log-rec709`. While that preset is
   absent or disabled, the worker renders `compress-only`, preserves the requested preset,
   writes unavailable fallback provenance, and marks the same generation ready.
5. For `not_log` or `unknown`, the attempt requests and applies `compress-only` with
   `transform_kind = none` and `color_transform_status = not_requested`.
6. A registered preset with manifest, hash, format, source-change, or ffmpeg failure
   terminally fails its job and exposes neither playback, confirmation, nor result delivery.
7. Mobile reads only sanitized formal-preview fields returned by the Backend, visibly
   labels Apple Log fallback as unconverted and may then play, confirm, or explicitly
   save the derived result.
8. When a later approved Rec.709 preset is enabled, it creates a new generation from the
   same immutable original. It never rewrites the historical fallback result or its
   provenance.

## Functional Requirements

### Detector Certification and Classification Contract

- The certification script is a verifier, not a rule generator. It first parses the committed,
  human-approved `detector-rule-input-v1.json`, verifies its canonical SHA-256 against the
  reviewed sidecar and validates the approval fields, then probes only the
  repository-external files assigned the `apple_log` and
  `ordinary` roles. The filename and directory name have no semantic value. The script
  verifies the whole-file SHA-256, exact Docker ffprobe version, command limits, and expected
  result before writing a manifest candidate and path-free certificate summary.
- Runtime invokes the following argument vector without a shell:

  ```text
  ffprobe -v error -print_format json -select_streams v:0 -show_entries stream=index,codec_type,color_space,color_transfer,color_primaries:stream_tags:stream_disposition:format_tags <source>
  ```

  The manifest must contain this exact `show_entries` value. Its allowlisted field paths and
  exact expected values must be identical to the approved rule input. Fixtures can accept or
  reject that fixed rule but cannot make a field or value authoritative.
- Certification invokes its pinned Compose service with
  `subprocess.Popen(..., shell=False, start_new_session=True)`. A bounded reader drains stdout
  and stderr concurrently and terminates the process group when one additional byte would
  exceed either 1048576-byte stream limit or when the 15000 ms timeout expires. Each run has
  a validated secure-UUID container name.
  Failure handling terminates the process group and removes that exact container before it can
  publish an artifact; cleanup failure is itself a stable terminal error. Runtime ffprobe uses
  the same bounded subprocess helper without the container-cleanup callback.
- The detector evaluates the `apple_log` all-of predicate first, then a certified
  `not_log` all-of predicate. No exact match, absent metadata, conflicting metadata,
  unsupported profiles, or sanitized Apple Log 2-like metadata returns `unknown`.
- `is_log` remains audit-only. It cannot change classification, preset intent, capability,
  delivery, or UI labels. Filename, extension, local URI, and pixel sampling are likewise
  excluded from evidence.
- The bounded evidence summary contains only allowlisted path/value pairs actually used by
  the selected rule, sorted by path and canonicalized before SHA-256. The database stores
  the evidence digest and at most 4096 sanitized bytes; APIs expose the digest and rule
  identity, not raw values.
- Manifest missing/invalid, ffprobe version mismatch, timeout, non-zero exit, stdout or stderr
  over 1048576 bytes, process/container cleanup failure, malformed JSON, or a value outside
  the manifest schema is detector
  infrastructure failure, not `unknown`. Before enablement it keeps the capability false;
  an unexpected occurrence after enablement terminally fails the current generation.

### Automatic Formal-Preset Resolution

Formal preview does not consume Mobile managed-rendition selection. It uses this complete
resolution matrix after detection:

| Detection / intent | Registry classification | Requested preset | Applied preset | Transform state | Job / delivery |
|---|---|---|---|---|---|
| `apple_log` automatic default | `absent` or `disabled` | `generated-apple-log-rec709` | `compress-only` | `none` / `unavailable` / `lut_preset_unavailable` | `done`; formal preview may be played, confirmed, and delivered as unconverted |
| `apple_log` automatic default | `registered_invalid` | `generated-apple-log-rec709` | none | `failed` | terminal failure; no preview, confirmation, or delivery |
| `apple_log` automatic default | `valid` but snapshot/source/hash/format/ffmpeg fails | `generated-apple-log-rec709` | none | `failed` | terminal failure; no fallback |
| `apple_log` automatic default | `valid` and later separately enabled | `generated-apple-log-rec709` | `generated-apple-log-rec709` | `lut` / `applied` | reserved for the later conversion feature; cannot occur in this release |
| `not_log` or `unknown` automatic default | virtual `compress-only` is `valid` | `compress-only` | `compress-only` | `none` / `not_requested` / no error | `done`; formal preview may be played, confirmed, and delivered |
| explicit identity, test, or custom selection | any | managed-rendition request only | managed-rendition result only | managed rendition provenance | never creates or replaces a formal preview in this feature |

- After classification, the worker creates or resumes one durable
  `formal_preview_attempt` keyed by `(asset_id, preview_generation)`. It snapshots detector
  manifest/rule/evidence and the existing registry's requested preset classification,
  canonical manifest bytes, manifest SHA-256, expected LUT SHA-256, source root kind,
  relative path, version, and target metadata before rendering.
- For `absent` or `disabled`, that snapshot preserves
  `requested_preset_id = generated-apple-log-rec709` while setting
  `applied_preset_id = compress-only`. A direct `compress-only` request for `not_log` or
  `unknown` records `not_requested`, not `unavailable`.
- Lease recovery reuses the persisted attempt snapshot and generation. It does not re-read
  current Mobile selection or silently resolve a newly added preset. A valid preset source
  that changes after snapshot fails with `lut_preset_source_changed`.

### Persistence and Atomic Finalization

- The Phase 2B migration adds to `assets`: non-null `preview_generation` defaulting to `0`,
  nullable `formal_preview_id`, `log_detection_status` in
  `not_evaluated | apple_log | not_log | unknown`, and nullable source profile/detector
  identity fields. It adds nullable `jobs.preview_generation`, required for every
  session-derived video `preview`/historical `lut_preview` job and null for other job types.
- New `formal_preview_attempts` stores one immutable detector/preset snapshot per job and
  `(asset_id, preview_generation)`. New `preview_provenance` has one row per formal derived
  preview and one-to-one references its attempt, asset, derived file, and processed result.
  Existing `renditions`, `rendition_selection_generation`, and `rendition_provenance` remain
  separate audit records and are never backfilled into these tables. A formal result has a
  non-null `processed_results.preview_generation` equal to its asset and attempt generation.
  Managed rendition results keep `preview_generation = null` before and after migration and
  use `rendition_selection_generation` as their independent ordering authority.
- The finalizer opens one `BEGIN IMMEDIATE` transaction and rechecks the asset's session
  origin, `file_verified`, current generation, attempt snapshot, candidate size/SHA-256,
  detector evidence, preset evidence, and storage relation. It then inserts the derived file,
  ready `processed_result`, and `preview_provenance`; sets `formal_preview_id`; stores the
  detection fields; sets `preview_ready`; and marks the attempt and job done in the same
  transaction. It sets `active_processed_result_id` to the formal result only when no current
  managed rendition exists. A current managed rendition remains ready and active. The formal
  result generation must equal the asset and attempt generation.
- Terminal detector, preset, or rendering failure updates the attempt, job, and existing
  asset to `failed` in one transaction, records a stable error code, creates no ready result
  or provenance, and removes the uncommitted candidate. A generation mismatch instead sets
  an existing attempt to `superseded`, ends the job as `status = failed` with
  `error_message = preview_generation_superseded`, clears its claim/lease, and does not
  change the asset or current formal/managed relations.
- SQLite triggers reject `preview_ready` for every session-derived `file_verified` video
  unless formal preview, generation, and provenance all match. They also prevent a current
  formal result from being superseded and prevent one result from having both formal and
  rendition provenance. Preview stream and confirmation repeat the formal relation and
  storage checks; exact-result delivery resolves formal and managed authority separately.
- Steady-state classification used by migration, formal finalization, and delivery requires
  the active managed pointer to identify the highest successfully finalized ready managed
  relation; only newer failed or superseded selections may coexist. Managed pointer
  transition validation is separate: immediately before an `N -> N+1` switch it permits the
  old complete active relation and exactly one new ready target whose selection generation
  equals the asset's current selection generation, whose result has
  `preview_generation = null`, and whose rendition provenance is complete. After the switch
  only the old managed result is superseded; a current formal result remains ready.

### Migration and New-Upload Generation Fence

- Deployment stops the old `api` service first so no upload, rendition, or job write can begin.
  The old `worker` remains up only until queued or running `preview`, `lut_preview`, and
  `rendition` jobs and nonterminal renditions drain to zero, then it is stopped as well. The
  official `backend/scripts/run_phase2b_formal_preview_migration.py` host wrapper verifies that
  both Compose services are not running and starts only an offline one-shot
  `phase2b-migrator` with the database volume. API and worker stay stopped until migration
  succeeds or rollback completes. Preliminary preflight requires those zero counts, no asset
  in `preview_generating`, a certified detector manifest, exact runtime ffprobe, healthy
  `compress-only`, and `formal_apple_log_preview = false`. After acquiring `BEGIN IMMEDIATE`,
  the container's `backend/scripts/migrate_phase2b_formal_preview.py` repeats the schema/marker
  and drain checks in that same transaction before applying any change. A write between
  preliminary preflight and lock acquisition therefore causes an unchanged rollback. An
  orphan `preview_generating` asset is not repaired by this migration: preflight fails with
  `phase2b_migration_preview_not_drained`, and existing operational recovery must first move
  it to a terminal state. Only then may schema/backfill begin.
- The migration runs in a transaction and rebuilds affected SQLite tables/constraints. It
  initializes existing preview jobs and assets at generation `0`. Its eligible predicate is:
  `assets.type = video`, `verification_status = file_verified`, `preview_status IN
  (preview_ready, failed)`, and an `upload_sessions` row with the same asset ID and
  `status = completed`. Phase 1 direct assets and images are excluded.
- For each eligible asset it attempts exactly one generation-`1` `preview` job with
  `profile_detection_required = true` and dedup key
  `phase2b-profile-preview:{asset_id}`. Only a successful new insert sets generation `1`,
  clears the formal relation, sets `preview_generating`, and resets review to `not_reviewed`
  in that same transaction. Before mutation it classifies the active result by persisted
  provenance: the active pointer's highest successfully finalized ready managed rendition
  remains ready and active even when newer selection generations are `failed` or
  `superseded`; a legacy Phase 2A preview is cleared and superseded without deleting bytes;
  null remains null; and a newer deliverable ready managed result, nonterminal work, or an
  ambiguous/conflicting relation aborts the whole transaction with
  `phase2b_migration_active_result_ambiguous`. An existing queued, running, done, or failed
  dedup row leaves every asset field unchanged.
- Once Phase 2B is enabled, a newly completed upload session creates its first
  profile-aware `preview` job and generation in the upload-finalization transaction. It no
  longer creates a new `lut_preview` job. Job-insert failure rolls back asset/session
  completion linkage exactly as required by the existing Phase 2A finalization contract.
- Only generation-aware API/worker images start after migration. A migration marker and
  capability preflight reject mixed-version startup. The capability becomes true only when
  both detector certification and this migration marker validate. Claim and lease recovery
  both reject generation `0` late work with `preview_generation_superseded` before writing a
  candidate.

### Versioned API and Mobile Contract

- `GET /api/v1/capabilities` remains bearer-authenticated and adds
  `features.detector_certified`, `features.formal_apple_log_preview`, and
  `formal_preview_schema_version = 1`. When formal preview is enabled it sets
  `minimum_client_version = 0.2.0`; `generated_apple_log_conversion` remains false.
- The implementation updates both `app.json` and `package.json` application versions from
  `0.1.0` to `0.2.0`; version comparison uses strict three-component semantic versions and
  rejects malformed values rather than comparing strings lexicographically.
- Mobile version `0.2.0` sends `X-MediaVault-Client-Version: 0.2.0` to preview stream,
  confirmation, and exact processed-result requests. A missing/older header for a Phase 2B
  formal preview returns `409 incompatible_client`; list/detail and capability discovery
  remain readable so the app can show the upgrade requirement.
- Asset list/detail replace legacy `is_log` gating with authoritative safe fields. Detail
  returns the nullable `formal_preview` object below. It never returns paths, fixture labels,
  raw probe values, manifest bytes, rule-input contents, or LUT contents.

  | Object state | Always required | Detection and detector fields | Preset and transform fields | Deliverable fields | Failure field |
  |---|---|---|---|---|---|
  | `generating` | `schema_version = 1`, `state = generating`, integer `generation >= 1` | `detection_status`, `source_profile`, `detector_rule_version`, `detector_manifest_sha256`, and `detector_evidence_sha256` are nullable; detector identity fields are either all present after a successful probe or all null | `requested_preset_id`, `applied_preset_id`, `transform_kind`, `color_transform_status`, and `color_transform_error_code` are nullable | `preview_id = null`, `result = null` | `failure_code = null` |
  | `ready` | `schema_version = 1`, `state = ready`, integer `generation >= 1` | `detection_status` is required as `apple_log`, `not_log`, or `unknown`; detector rule version is a required bounded identifier and both detector digests are required 64-lowercase-hex values; `source_profile` remains nullable | requested/applied IDs, `transform_kind`, and `color_transform_status` are required; `color_transform_error_code` is `lut_preset_unavailable` only for Apple Log fallback and otherwise null in this release | 32-lowercase-hex `preview_id` and `result` with `result_id`, `mime_type`, `size_bytes`, `sha256`, `created_at`, and canonical relative `url` are required | `failure_code = null` |
  | `failed` | `schema_version = 1`, `state = failed`, integer `generation >= 1` | retained successful detector fields are returned as one complete group; otherwise the group and detection status are null; `source_profile` is nullable | `requested_preset_id` may be retained after resolution; `applied_preset_id = null`; transform fields are retained only when reached, with `color_transform_status = failed`, otherwise null | `preview_id = null`, `result = null` | stable non-null `failure_code` |

  `formal_preview = null` only for an image, a Phase 1 direct asset, a session video excluded
  before generation-1 queueing, or an unmigrated asset while the capability is false. It is
  never null for a Phase 2B session video after its generation-1 job is inserted. The list
  response does not include this object.
- `GET /assets/{asset_id}/preview`,
  `POST /assets/{asset_id}/preview-confirmation` require the current formal relation for a
  Phase 2B session video. `GET /assets/{asset_id}/results/{result_id}` first resolves result
  kind and permits either the current formal result or the current managed rendition.
  Invalid or stale formal relations return stable `409 formal_preview_not_ready` or
  `409 formal_preview_provenance_invalid`; a result that is current under neither authority
  returns `409 processed_result_superseded`.
- Mobile API adapters validate all enums, IDs, SHA-256 strings, nullable fields, and schema
  version before hooks consume them. Screens receive state through hooks/services and do not
  call HTTP, FileSystem, MediaLibrary, or SecureStore directly.
- Mobile presentation is fixed as follows:
  - `apple_log` + `unavailable`: show "Apple Log (unconverted)" and "Color transform
    unavailable"; allow playback, confirmation, and explicit verified-result save.
  - `apple_log` + `applied` (future): show the server-supplied approved transform name and
    transformed state only when full LUT provenance validates.
  - `not_log`: show the ordinary-video state without an Apple Log or Rec.709 claim.
  - `unknown`: show "Video profile unknown (unconverted)"; allow playback, confirmation,
    and save, but never infer Apple Log or Rec.709.
  - `failed` or invalid/incompatible provenance: show the stable error and no playback,
    confirmation, result-save, or iPhone-original deletion action.

### Failure Contract

For a `formal_preview.state = failed` response, `failure_code` is exactly one of
`log_detector_manifest_invalid`, `log_detector_version_mismatch`, `log_probe_timeout`,
`log_probe_failed`, `log_probe_output_invalid`, `lut_preset_registered_invalid`,
`lut_preset_source_changed`, `lut_application_failed`, `formal_preview_source_invalid`,
`formal_preview_render_failed`, `formal_preview_storage_failed`,
`formal_preview_database_failed`, or `formal_preview_relation_invalid`. The Backend never
returns exception text as a code. Adding a value requires a formal-preview schema-version
change and corresponding Mobile adapter/message handling.

| Condition | Stable result |
|---|---|
| certificate missing/not yet approved before rollout | capability false; migration and profile-aware job creation blocked; existing Phase 2A state unchanged |
| detector manifest invalid or runtime ffprobe version mismatch | `log_detector_manifest_invalid` or `log_detector_version_mismatch`; startup/migration blocked |
| ffprobe timeout, non-zero/reader/container-cleanup failure, stdout/stderr oversized output, malformed JSON after enablement | terminal `log_probe_timeout`, `log_probe_failed`, or `log_probe_output_invalid` |
| valid probe with absent, conflicting, or unsupported metadata | classification `unknown`; ready `compress-only` formal preview |
| requested preset `absent` or `disabled` | ready `compress-only`; `lut_preset_unavailable` provenance |
| registered invalid/source changed/LUT application failed | terminal `lut_preset_registered_invalid`, `lut_preset_source_changed`, or `lut_application_failed`; no fallback |
| original relation, compress-only render, storage, database, or persisted formal relation fails | matching terminal `formal_preview_*` enum value; no deliverable result |
| migration active result has ambiguous, conflicting, or dual provenance | `phase2b_migration_active_result_ambiguous`; full migration transaction rollback |
| current generation changed before claim/finalize | attempt `superseded` if present; job `failed` with `preview_generation_superseded`; lease clear; no asset mutation |
| current formal relation or client version invalid | authenticated `409`; no preview stream or confirmation; exact managed-result delivery remains kind-gated |

## Non-Functional / Technical Notes

- Keep React Native + Expo, FastAPI, SQLite, ffmpeg, managed preset manifests, and the
  immutable-original rule.
- The detector timeout is 15000 ms, captured stdout is at most 1048576 bytes, and stored
  evidence is at most 4096 bytes. stderr is reduced to a stable code and never includes the
  command, source path, raw metadata, token, local URI, fixture name, or LUT contents.
- Controlled external media covers Apple Log and ordinary video. Repository-owned sanitized
  metadata fixtures cover unknown/conflicting/missing metadata and the unsupported Apple Log
  2 contract without claiming that synthetic JSON proves a real Apple Log 2 recording.
- Detector and finalizer processing is bounded to one first video stream, one job-private
  candidate, and the existing ffmpeg timeout/memory model. No full media file is loaded into
  Python memory.
- Migration and finalization use SQLite `BEGIN IMMEDIATE`, existing WAL mode, and the
  configured 5000 ms busy timeout. Retried migration and job execution are idempotent.
- Historical identity-LUT derived files and old `lut_preview` jobs remain audit-only.
  They are never served as formal Rec.709 previews.
- Mobile remains React Native + Expo managed workflow + JavaScript. No TypeScript, Expo SDK
  upgrade, Node version change, cloud endpoint, or LUT upload is introduced.
- The current project-wide quality commands remain mandatory: `npm run lint`, `npm test`,
  `npm run test:coverage`, `npx expo install --check`, and iOS export. Canonical coverage
  continues to include all matched production modules and must keep statements/lines >= 80%,
  branches >= 69.46%, and functions >= 80.08% without new exclusions.

## Acceptance Criteria

- Without a certified manifest, capabilities report formal preview disabled, migration and
  profile-aware job creation refuse to run, and all existing asset/job/result state remains
  byte-for-byte unchanged.
- The certification command requires an independently authored and repository-owner-approved
  canonical rule input, accepts repository-external user-owned Apple Log and ordinary
  recordings, copies each no-follow descriptor into an owner-only bounded temporary snapshot,
  verifies its SHA-256, and gives only that same snapshot to the pinned Docker ffprobe. It
  writes no media or local path to Git/logs and produces a canonical manifest and path-free
  certificate summary whose committed digests are reproduced on a second run.
- Tests reject a missing or digest-mismatched rule input, unapproved input, any manifest rule
  not byte-identical to that input, and any attempt to derive predicates from fixture
  differences. Fixture metadata never edits the approved rule.
- Manifest duplicate/unknown keys, BOM, invalid canonical digest, unsupported predicate,
  changed fixture digest, ffprobe version mismatch, timeout, stdout/stderr oversized output,
  process/container cleanup failure, malformed JSON, and path/value mismatch are each tested
  with their specified blocked/failed outcome. No failure leaves a certifier container or
  publishes a partial artifact.
- A certified Apple Log fixture with no enabled Rec.709 preset records
  `requested_preset_id = generated-apple-log-rec709`,
  `applied_preset_id = compress-only`, `transform_kind = none`,
  `color_transform_status = unavailable`, and
  `color_transform_error_code = lut_preset_unavailable`; the job is done and Mobile calls it
  unconverted.
- Certified ordinary and sanitized unknown/unsupported metadata produce ready
  provenance-backed `compress-only` results with `not_requested`, without an Apple Log or
  Rec.709 claim. No filename, `is_log`, or pixel change can alter those results.
- A missing or disabled automatic preset falls back before LUT processing. A
  registered-invalid, altered, source-changed, malformed, hash-mismatched, or
  ffmpeg-rejected LUT terminally fails without playback, confirmation, or delivery.
- Explicit identity/test/custom selection continues to create only a managed rendition.
  It does not change `formal_preview_id`, `preview_generation`, preview/review state, or the
  formal result pointer.
- Migration rejects a running old API/worker, uncertified or mixed-version deployment, any
  queued/running legacy `preview`, `lut_preview`, or `rendition` job, any nonterminal rendition,
  and any remaining `preview_generating` asset without making a data change. A competing write
  after preliminary preflight is detected by the repeated checks under `BEGIN IMMEDIATE` and
  rolls back schema, marker, and data. After a complete drain the offline one-shot migration
  includes only completed session-derived verified videos, requeues each eligible asset
  exactly once, and does not mutate Phase 1 direct assets. Repeating it with an existing
  queued/running/done/failed dedup job leaves asset state unchanged.
- A newly completed Phase 2B upload session creates exactly one generation-aware
  profile-preview job in its finalization transaction. It creates no new `lut_preview` job.
- The Phase 2B kind-aware pointer trigger requires a managed pointer target to have a ready
  result, complete rendition provenance, and a ready rendition. The managed finalizer
  therefore commits its statements in this transaction order:
  derived/result, provenance, rendition `ready`, active pointer, job `done`. Direct SQL cannot
  point at a queued, validating, rendering, finalizing, failed, or superseded rendition.
  Its transition predicate permits exactly one current-selection ready target without
  applying the steady-state classifier to the old pointer during that intermediate state.
- Managed results retain `preview_generation = null` after Phase 2B; formal results alone
  carry the non-null generation matching the asset, attempt, and formal provenance.
- DB integration tests cover `active managed N -> ready managed N+1 -> pointer switch ->
  managed N superseded`, preservation of a current formal result during that switch, direct
  SQL rejection of any other intermediate relation, and rollback at every write boundary.
- A generation-`0` job and a stale/recovered newer job cannot create a candidate or change
  asset, formal preview, result, review, or provenance state. An existing attempt becomes
  `superseded`; the job becomes `failed` with `preview_generation_superseded` and a cleared
  lease. Direct SQL attempts to set `preview_ready` without the complete formal relation fail.
- Success finalization atomically commits attempt/job, derived file, result, provenance,
  detection fields, formal pointer, generation, and ready state. It sets the processed-result
  active pointer only when no current managed rendition exists. Migration and finalizer tests
  preserve an existing current managed result and its delivery, including
  `ready generation N -> failed/superseded generation N+1 -> migration -> formal finalize`. Failure
  injection at every write boundary rolls back DB state and removes the candidate.
- Asset Detail schema tests cover `formal_preview = null` and every required/nullable field in
  `generating`, `ready`, and `failed`. Sanitization, capability/minimum-client enforcement,
  preview stream,
  confirmation, and exact result delivery tests cover Apple Log unavailable, ordinary,
  unknown, failed, stale provenance, and incompatible-client states.
- Mobile component/hook tests verify the fixed labels and action matrix. A Development Build
  release check uses the certified Apple Log/ordinary inputs to verify playback and visible
  unconverted state; it does not need a Rec.709 LUT.
- `npm run lint` succeeds with 0 warnings; all Mobile and Backend tests pass; canonical
  coverage passes unchanged floors; Expo dependency check, iOS export, Metro root/bundle,
  Docker Compose config, deterministic detector certification, and `git diff --check` pass
  without generated media/report artifacts in Git.
- `generated-apple-log-rec709` remains absent/disabled and
  `generated_apple_log_conversion = false`. No output is labelled Rec.709 until the later
  transform feature satisfies its separate gate.

## Required Operator Input

- Before certification, the repository owner supplies a non-fixture technical source for each
  exact metadata predicate and explicitly approves the canonical rule input and digest
  sidecar. Infrastructure implementation may ship the schema and rejection tests first, but
  it must not invent placeholder predicates or enable the capability.
- Before enablement, the user supplies local paths under one repository-external fixture
  root for one self-owned Apple Log recording and one ordinary recording from the intended
  iPhone workflow. The files may be supplied after infrastructure implementation, but
  certification and migration remain blocked until both are present.
- The certification record stores only role, source label `user-owned-local-recording`,
  whole-file SHA-256, expected classification, exact approved metadata predicates, and
  detector/ffprobe versions. It does not store the local path, filename, capture location,
  timestamp, or media bytes.

## Open Questions

- The later Apple Log to Rec.709 transform's generator/library, parameters, quality
  threshold, Rec.709 tags, and source/license evidence. This does not block the unconverted
  Phase 2B release and cannot enable `generated-apple-log-rec709` here.

## Durable Docs Impact

- The durable product, fallback, provenance, and generation-fence direction is already
  present in `product-requirements.md`, `functional-design.md`, `architecture.md`,
  `development-guidelines.md`, `glossary.md`, and `repository-structure.md`.
- Planning/implementation must update those documents with the final detector manifest
  location/schema, `formal_preview_attempts`, exact migration columns/triggers, capability
  and Asset Detail schemas, client-version header, stable detector errors, and fixed Mobile
  labels introduced by this specification.
- The future Rec.709 transform remains documented as a separate gated feature; durable docs
  must not imply that Phase 2B bundles or enables it.
