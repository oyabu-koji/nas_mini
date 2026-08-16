# Feature Spec

## Metadata

- Date: 2026-08-02
- Feature name: Apple Log container signaling detection
- Status: draft
- Related files:
  - `docs/ideas/20260711_2-apple-log-preview.md`
  - `docs/ideas/iphone_applelog_app_distribution_and_lut_policy.md`
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `.steering/20260711_2-apple-log-preview/`

## Background

Phase 2B already implements formal-preview provenance and a fail-closed detector
certification gate. Its current detector relies on allowlisted JSON fields emitted by
FFprobe. That is insufficient for the user-owned Apple Log recording used for release
validation: FFprobe reports only `color_space = bt2020nc` and does not expose the Apple
Log transfer-function identifier. Treating BT.2020 matrix metadata alone as Apple Log
would cause false positives for non-Log wide-color or HDR media.

The same QuickTime file contains one authoritative `logs` sample-description extension
inside its top-level `moov`; its payload is
`com.apple.apple-wide-gamut.apple-log`. A second byte sequence occurs inside a
non-authoritative `hoov` structure embedded in `mdat` and must be ignored. Apple
documents the identifier value as Apple Log 2 and documents
`com.apple.rec2020.apple-log` as Apple Log 1. The ordinary comparison recording has no
authoritative `logs` identifier and FFprobe reports `color_primaries = bt709`,
`color_transfer = bt709`, and `color_space = bt709`.

This feature adds a deterministic, portable QuickTime/ISO BMFF metadata parser so the
Mac mini and later Linux review environment use the same detection logic. It extends,
rather than bypasses, the existing Phase 2B certification, provenance, fallback, and
safe-delete contracts. It does not implement a LUT or a Rec.709 color transform.

Official identifier reference:

- Apple VideoToolbox `kVTCompressionPropertyKey_LogTransferFunction`:
  `https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_logtransferfunction`
- Apple Log 1 identifier: `com.apple.rec2020.apple-log`
- Apple Log 2 identifier: `com.apple.apple-wide-gamut.apple-log`

Apple's documentation is authoritative for the two identifier meanings, but it does not
serve as the file-format definition for the observed `logs` box placement or payload
serialization. That serialization is a project-owned, versioned observation validated by
the real Apple Log 2 container and deterministic synthetic boxes. The Apple APIs that
expose the identifiers are currently documented as Beta. The implementation therefore
pins the exact identifier strings, source URL, approval time, parser contract version,
serialization contract, and certification evidence instead of depending on a changing
SDK at runtime.

## Target Users / Use Cases

- An iPhone user who imports Apple Log 1 or Apple Log 2 video and needs the app to show
  the detected profile accurately.
- A user who must be able to review an explicitly unconverted lightweight preview while
  no compatible Rec.709 transform is registered.
- A Mac mini administrator who must certify detection with user-owned real media without
  committing the media or its absolute path.
- A future App Review environment that must reproduce Mac mini detection in Linux
  containers without AVFoundation-only behavior.

## Scope

- Parse QuickTime/ISO BMFF box structure from a no-follow regular-file descriptor and
  inspect `logs` only when it is a bounded extension of a video sample description.
- Map the exact identifier `com.apple.rec2020.apple-log` to
  `detection_status = apple_log` and `source_profile = apple-log-1`.
- Map the exact identifier `com.apple.apple-wide-gamut.apple-log` to
  `detection_status = apple_log` and `source_profile = apple-log-2`.
- Classify a well-formed video with no `logs` signal as `not_log` only when FFprobe
  reports all three values `color_primaries = bt709`, `color_transfer = bt709`, and
  `color_space = bt709` for the selected video stream.
- Classify a well-formed but unsupported, missing, or conflicting color signal as
  `unknown`, unless the container itself is malformed or exceeds a parser safety limit.
- Keep `detection_status` values unchanged as `apple_log`, `not_log`, and `unknown`.
  Use the existing `source_profile` field for the two stable profile identifiers.
- Reserve separate automatic transform IDs so a future Apple Log 1 transform cannot be
  applied to Apple Log 2:
  - `apple-log-1` requests `generated-apple-log-rec709`.
  - `apple-log-2` requests `generated-apple-log2-rec709`.
- Keep both automatic transforms absent or disabled in this feature. Both Apple Log
  profiles therefore apply `compress-only`, finish successfully, and persist
  `transform_kind = none`, `color_transform_status = unavailable`, and
  `color_transform_error_code = lut_preset_unavailable`.
- Show `Apple Log 1 (unconverted)` or `Apple Log 2 (unconverted)` on Mobile for the
  fallback result on both Asset Detail and Preview Review. Both screens use one shared
  pure label helper, and an applied transform remains impossible in this feature.
- Extend the detector rule input, manifest, certificate summary, runtime capability,
  formal-preview evidence, database constraints, and Mobile response sanitizer to cover
  container signaling and profile-specific automatic preset resolution.
- Release the Mobile/client contract as `0.4.0`. When the successor detector schema is
  present, Backend reports `minimum_client_version = 0.4.0` even while runtime detection
  is stopped, so an older client cannot enter a Phase 2 flow that requires profile-aware
  display and validation.
- Use these local-only user-owned recordings for certification input:
  - Apple Log 2: `data/A001_04301259_C047.mov`
  - ordinary Rec.709: `data/IMG_0812.MOV`
- Add repository-root `/data/` to `.gitignore` before any fixture automation is added.
  The two recordings remain local operator inputs and are never staged or committed.
- Update the six durable documents and the status/relationship notes in
  `docs/ideas/20260711_2-apple-log-preview.md` where the stable contract changes.

## Out of Scope

- Generating, bundling, registering, enabling, or applying an Apple Log to Rec.709 LUT.
- Decoding Apple Log 1 or Apple Log 2, tone mapping, gamut conversion, output color
  tagging, or comparing transformed output with Final Cut Pro.
- Extracting a LUT or other asset from Final Cut Pro or another Apple application.
- Treating `bt2020nc`, codec, camera model, capture application, filename, pixel values,
  or the legacy Mobile `is_log` field as sufficient Apple Log evidence.
- Running `strings`, regular expressions over whole media bytes, or an unbounded byte
  search in production detection.
- Depending on AVFoundation, CoreMedia, or a macOS-only helper for runtime detection.
- Uploading a LUT from Mobile or adding administrator LUT authoring/editing UI/API.
- Parsing non-QuickTime/ISO-BMFF containers for `logs`. A non-BMFF input has the explicit
  parser result `unsupported_container`; it is `not_log` only with exact triple-BT.709
  FFprobe evidence and is otherwise `unknown`.
- Committing real media, absolute fixture paths, raw FFprobe output, raw container
  metadata, location tags, or original filenames into detector artifacts or logs.

## User Flow

1. A Phase 2 upload session finalizes and verifies an immutable video original.
2. The formal-preview worker opens the server-owned original without following symlinks
   and verifies that it is the expected regular file.
3. The detector reads only bounded container metadata from that descriptor and runs the
   existing bounded FFprobe inspection against the same opened file identity.
4. The detector combines the `logs` signal and allowlisted FFprobe color fields using the
   classification table below.
5. For Apple Log 1 or Apple Log 2, the resolver records the profile-specific automatic
   preset request. Because no transform is registered, it renders a `compress-only`
   preview and persists the unavailable-transform provenance.
6. For `not_log` or `unknown`, it requests and applies `compress-only` without claiming a
   color transform.
7. Mobile reads the formal-preview response and shows the exact Apple Log version plus
   the unconverted status. Playback, confirmation, delivery, and safe-delete evaluation
   continue to use the existing formal authority.

## Functional Requirements

### 1. Container Parser Boundary

- Implement a project-owned parser for QuickTime/ISO BMFF box headers. Do not introduce
  an opaque media parser solely to locate `logs` unless its license and bounded behavior
  are separately approved in the feature plan.
- Open the source with `O_RDONLY` and `O_NOFOLLOW` where supported. Require a regular
  file, compare it with the expected stored size, and compare device, inode, size, and
  modification time before and after detection. Also compare the source path's no-follow
  `lstat` identity with the opened descriptor before and after detection. A missing,
  replaced, symlinked, or differently identified directory entry is a source-change
  failure even when the original descriptor remains readable.
- The parser and FFprobe must consume the same opened file identity. A path must not be
  accepted, closed, and later reopened as detection authority. The implementation may
  use an inherited read-only descriptor such as `/proc/self/fd/<n>` or `/dev/fd/<n>`
  with explicit descriptor passing.
- Decode unsigned big-endian 32-bit size, 64-bit extended size, and 4-byte box type.
  Every box must remain within its parent and the file size. Integer addition and offset
  calculation must be checked before seeking.
- A zero-sized top-level box may extend to EOF. A zero-sized nested box, a header shorter
  than 8 bytes, an extended-size header shorter than 16 bytes, a size smaller than its
  header, an out-of-parent end offset, or a truncated read is malformed.
- A complete first top-level `ftyp` box with a bounded ASCII major brand identifies the
  input as BMFF for this parser. If no such first box exists, return
  `unsupported_container` without interpreting later bytes as boxes. If the first box
  declares type `ftyp` but its size or payload is malformed, return `invalid`.
- Require exactly one valid top-level `moov`. A missing or duplicate top-level `moov` in
  an otherwise BMFF-identified file is `log_container_invalid`. Traverse only
  `moov/trak/tkhd`, `moov/trak/mdia/hdlr`,
  `moov/trak/mdia/minf/stbl/stsd`, the selected visual sample entries, and their direct
  `logs` extensions. Seek over `mdat` and ignore `hoov` and all unknown top-level boxes;
  never scan their payload for identifier strings.
- Parse `tkhd` version 0 and 1 sufficiently to obtain a nonzero unsigned 32-bit
  `track_ID`. Parse `hdlr.handler_type` and accept only `vide` tracks. Schema-v2 FFprobe
  output includes `stream.id`; normalize its exact hexadecimal `0x...` track ID and match
  it to one unique `tkhd.track_ID`. Missing, malformed, duplicate, or unmatched IDs are
  `unknown` for a well-formed file; they never combine evidence from different tracks.
- Parse `stsd` as a FullBox with version/flags, bounded `entry_count`, and entries wholly
  contained in the box. For a selected video track, skip the 78-byte ISO BMFF
  `VisualSampleEntry` fixed fields beginning immediately after its 8-byte box header.
  Direct child boxes therefore start at byte offset `86` from the sample-entry box start.
  After the final complete child box, allow only the observed 0 to 7 bytes of all-zero
  padding; any nonzero trailing byte is malformed. A sample entry shorter than 86 bytes
  is malformed. The plan must enumerate any
  explicitly supported QuickTime legacy visual-entry layout; an unenumerated layout is
  `unsupported_container`, not a guessed child offset.
- Collect `logs` only from sample descriptions belonging to the uniquely matched video
  track. Accept consistent duplicates across that track's sample descriptions. Different
  recognized identifiers are conflicting evidence and produce `unknown`. In the real
  fixture, the authoritative `moov` has one Apple Log 2 occurrence; the decoy occurrence
  inside `mdat/hoov` is ignored and covered by regression tests.
- Parse `logs` as the non-NUL-terminated ASCII identifier observed in the fixed real
  fixture. Its payload must be between 1 and 128 bytes. Any NUL, non-ASCII byte, empty
  payload, or payload over the limit makes that `logs` box malformed.
- Enforce hard-coded, non-configurable upper safety bounds:
  - maximum file size: the existing Phase 2 hard bound of `1099511627776` bytes;
  - maximum traversed box headers: `65536`;
  - maximum nesting depth: `12`;
  - maximum video tracks: `8`;
  - maximum sample descriptions across video tracks: `32`;
  - maximum cumulative bytes read for box headers and inspected metadata: `1048576`;
  - maximum recognized or unknown `logs` identifiers retained in memory: `16`.
- Attempting an operation that would exceed a hard bound, finding malformed structure on an inspected metadata path, or
  observing source identity change terminates detection with a stable failure. It must
  not fall back to an untrusted classification.

### 2. Classification Contract

- The parser returns one closed result before classification:
  `recognized_logs`, `no_logs`, `unknown_logs`, `conflicting_logs`,
  `unsupported_container`, `invalid`, or `resource_limit`. A recognized result contains
  exactly one allowed profile and its container `track_ID`. Other results contain no
  profile.
- Schema-v2 FFprobe reads only
  `stream=index,id,codec_type,color_space,color_transfer,color_primaries` for `v:0`.
  It does not request `stream_tags`, `stream_disposition`, or `format_tags`.
- For a recognized Apple Log signal, allowed FFprobe values are fixed below. A missing
  field and the literal `unknown` are equivalent. Any present value outside the row's
  allowlist is conflicting and produces `unknown`.

| Profile | `color_primaries` allowlist | `color_transfer` allowlist | `color_space` allowlist |
|---|---|---|---|
| `apple-log-1` | missing, `unknown`, `bt2020` | missing, `unknown` | missing, `unknown`, `bt2020nc` |
| `apple-log-2` | missing, `unknown` | missing, `unknown` | missing, `unknown`, `bt2020nc` |

| Parser result | FFprobe result | Detection | `source_profile` |
|---|---|---|---|
| `recognized_logs` / Apple Log 1 | matching track ID and all fields in allowlists | `apple_log` | `apple-log-1` |
| `recognized_logs` / Apple Log 2 | matching track ID and all fields in allowlists | `apple_log` | `apple-log-2` |
| `recognized_logs` | unmatched/invalid track ID or any field outside allowlist | `unknown` | null |
| `no_logs` | primaries, transfer, matrix all exactly `bt709` on matched track | `not_log` | null |
| `no_logs` | incomplete, non-709, or unsupported values | `unknown` | null |
| `unsupported_container` | primaries, transfer, matrix all exactly `bt709` | `not_log` | null |
| `unsupported_container` | incomplete, non-709, or unsupported values | `unknown` | null |
| `unknown_logs` or `conflicting_logs` | any | `unknown` | null |
| `invalid` or `resource_limit` | any | terminal detector failure | null |

- `apple_log` requires exactly one stable non-null `source_profile` from the allowlist.
  `not_log` and `unknown` require `source_profile = null`.
- `bt2020nc` alone is neither an Apple Log profile nor `not_log` evidence.
- The FFprobe output remains strict JSON with duplicate-key rejection and the existing
  output/time limits. Container evidence is combined before the evidence digest is made.
- Canonical evidence contains only classification, source profile, parser contract
  version, a bounded signal kind, and allowlisted color values. It does not contain file
  paths, raw atoms, arbitrary metadata, camera/location tags, or unknown identifier text.
- New stable detector errors are mapped without host paths or parser details:
  - `log_container_invalid` for malformed or truncated inspected structure;
  - `log_container_resource_limit` for a hard safety-bound violation;
  - `log_container_source_changed` when the opened file identity changes;
  - existing `log_probe_timeout`, `log_probe_failed`, and
    `log_probe_output_invalid` remain FFprobe failures.
- Backend and Mobile add the three new container errors to their closed safe-code maps.
  An unrecognized error code still fails closed and exposes no preview.

### 3. Certification and Runtime Gate

- Replace the pending schema-v1 detector input with an explicitly approved schema-v2
  input. Because Phase 2B is not yet enabled, no valid v1 production artifact is migrated
  or silently accepted.
- Schema v2 pins:
  - detector ID and rule version;
  - parser contract version;
  - the two exact Apple identifier-to-profile mappings;
  - profile-to-requested-preset mapping;
  - exact `not_log` FFprobe predicates;
  - rationale and official source reference for every identifier;
  - approving role, ISO 8601 approval time, and approval reference.
- Keep canonical JSON, duplicate-key rejection, unknown-field rejection, JCS SHA-256,
  and an adjacent lowercase-hex sidecar digest.
- Manifest schema v2 copies the approved mappings byte-for-byte and pins the parser
  contract version, FFprobe version/show-entries, resource limits, fixture identities,
  rule-input SHA-256, and manifest SHA-256.
- The local certification descriptor is fixed at
  `data/detector-certification-v2.json`. The `data/` fixture root must be an owner-owned
  no-symlink directory with mode `0700`; the descriptor must be an owner-owned no-symlink
  regular file with mode `0600`. It records only relative operator paths, expected
  media SHA-256, role, expected detection status, expected source profile, and
  `user-owned-local-recording`. Relative paths are confined below the fixture root. The
  descriptor is local-only and must be ignored by Git.
- Certification uses the current user-owned inputs and verifies their expected SHA-256
  before snapshotting:
  - Apple Log 2 SHA-256:
    `749f52937f62b1790ac71b37797cf817c877b87dde6ea44969544a46d87032c1`;
  - ordinary Rec.709 SHA-256:
    `1c70479d633927d82360322c7f77ba465aee2d31cd2b56dc55e784d09e52237c`.
- Certification copies each external input to an owner-only temporary snapshot using
  the existing no-follow and whole-file hash contract. Both the parser and pinned Docker
  FFprobe inspect only that snapshot.
- Certification immediately removes snapshots on normal completion, handled exception,
  timeout, interruption, and catchable termination signal. Because `SIGKILL` and power
  loss cannot run cleanup code, the next certification start performs a bounded stale
  sweep before creating new snapshots. It removes only entries under the fixed temporary
  name pattern `${TMPDIR}/mediavault-detector-fixtures-<32-lowercase-hex>` whose owner,
  regular-file/directory type, no-symlink path, restrictive mode, and age of at least
  `300 seconds` match the cleanup contract; an ambiguous entry stops certification.
- Unit tests construct minimal deterministic box byte sequences for Apple Log 1,
  Apple Log 2, ordinary, unknown, duplicate-consistent, conflicting, large-size,
  malformed, truncated, and resource-limit cases. These byte fixtures contain no real
  video or user metadata.
- The real Apple Log 2 and ordinary recordings validate actual-container integration.
  An Apple Log 1 real recording is not required to enable this release because the exact
  official identifier mapping and the same parser path are covered deterministically;
  when a controlled Apple Log 1 recording is available, it may be added as a third
  external certification fixture without changing classification semantics.
- Certificate summary marks Apple Log 2 and ordinary as `real-container` coverage and
  Apple Log 1 as `synthetic-container` coverage. A future Apple Log 1 transform cannot be
  enabled until a user-owned real Apple Log 1 fixture replaces or supplements that marker.
- API and worker report `formal_apple_log_preview = false` until the v2 rule input,
  manifest, certificate summary, parser contract version, runtime FFprobe version, and
  migration marker all match. There is no partial fallback to the old v1 detector.

### 4. Formal Preview and Persistence

- Automatic resolution uses this table:

| Detection | `source_profile` | Requested preset | Applied preset in this feature | Transform result |
|---|---|---|---|---|
| `apple_log` | `apple-log-1` | `generated-apple-log-rec709` | `compress-only` | `none` / `unavailable` / `lut_preset_unavailable` |
| `apple_log` | `apple-log-2` | `generated-apple-log2-rec709` | `compress-only` | `none` / `unavailable` / `lut_preset_unavailable` |
| `not_log` | null | `compress-only` | `compress-only` | `none` / `not_requested` / null |
| `unknown` | null | `compress-only` | `compress-only` | `none` / `not_requested` / null |

- `generated-apple-log2-rec709` is a reserved ID only. It is not added to the selectable
  managed-preset catalog and no LUT or manifest is created for it.
- Startup, migration, API, and worker guards allow each reserved Apple Log automatic
  preset only when it is absent or explicitly disabled. They reject a valid enabled
  preset, a registered-invalid preset, and any reserved-namespace collision during this
  feature.
- The successor database may retain a future-compatible applied-transform tuple, but this
  feature's worker, authority checks, Backend responses, and Mobile sanitizer must reject
  that tuple for Apple Log 1 and Apple Log 2. Existing applied Apple Log rows block the
  migration and are never silently rewritten as unconverted previews.
- Asset Detail, preview, processed-result, and confirmation endpoints map a persisted
  Apple Log applied tuple to `409 formal_preview_provenance_invalid` before response
  construction. Mobile maps an untrusted applied claim to `formal_preview_invalid` and
  does not play, confirm, download, or delete the local original.
- Extend formal-preview attempt/provenance validation, result authority,
  kind-aware delivery, confirmation, and safe-delete evaluation to accept the Apple Log
  2 unavailable fallback while retaining exact requested/applied/profile evidence.
- Add a forward migration rather than editing a migration already applied to an operator
  database. The migration validates existing rows before adding profile/preset consistency
  triggers or rebuilding constrained tables. It fails without partial schema/data changes
  if incompatible evidence exists.
- The forward migration requires exact 009 schema identity, stopped API, drained
  preview/rendition workers, no queued/running affected job, and a locked preflight. It
  repeats schema/data identity checks inside `BEGIN IMMEDIATE`, applies all changes in one
  transaction, runs `foreign_key_check` and successor schema identity verification, and
  either commits the new marker or rolls back schema, data, and marker completely.
- At locked preflight, the migrator records a no-follow identity/digest snapshot of both
  reserved preset namespaces. After schema/data validation and marker writes, it
  reclassifies both presets and compares the snapshot immediately before commit. Any
  filesystem identity, digest, classification, or namespace change rolls back the entire
  migration.
- Enforce these database invariants for `assets`:
  - `log_detection_status = apple_log` requires
    `source_profile IN ('apple-log-1', 'apple-log-2')`;
  - `not_evaluated`, `not_log`, and `unknown` require `source_profile IS NULL`;
  - `source_profile` is included in every relevant INSERT/UPDATE trigger watch set so a
    direct SQL profile-only mutation cannot bypass formal authority.
- Enforce the same detection/profile pair on `formal_preview_attempts` and
  `preview_provenance`. Once detector identity is present, `apple-log-1` permits only
  `requested_preset_id = generated-apple-log-rec709`, `apple-log-2` permits only
  `requested_preset_id = generated-apple-log2-rec709`, and `not_log`/`unknown` permit only
  `source_profile = null` plus `requested_preset_id = compress-only`. Pre-detection
  nullable attempt fields remain an all-null group and cannot form partial evidence.
- `phase_schema_identity.py`, the Phase 2 rollout authority, the Phase 2C evaluator, the
  offline migration/reconciliation CLIs, and minimum-client calculation formally
  recognize only the complete successor schema. A partial or extra schema is rejected.
- Existing Apple Log rows with `source_profile = null` are not silently reinterpreted.
  Before capability enablement they must be absent, explicitly reprocessed from the
  immutable original under detector v2, or cause migration preflight to stop.
- The feature does not make a managed identity/test/custom rendition a formal preview.
  It does not change the meaning of `safe_to_delete_candidate`: both Apple Log fallback
  profiles remain eligible only because the original is verified and the exact unconverted
  formal preview was explicitly confirmed.

### 5. API and Mobile UI

- Keep API schema version `1` unless the serialized shape changes. The existing nullable
  `source_profile` field is sufficient, but its value becomes a strict allowlist:
  `apple-log-1`, `apple-log-2`, or null according to detection status.
- The JSON shape remains schema version `1`, but the allowed value contract and required
  profile-specific UI change. Bump `package.json`, `app.json`, and
  `src/shared/constants/clientVersion.js` to `0.4.0` without changing Node or Expo SDK.
- When the successor migration marker is present, capability discovery reports
  `minimum_client_version = 0.4.0` regardless of detector runtime availability. Clients
  at `0.3.0` or below may still open Settings and read capabilities but receive
  `409 incompatible_client` before Phase 2 formal preview, result delivery,
  confirmation, or original-deletion operations.
- Backend response validation rejects `apple_log` without an allowed source profile and
  rejects `not_log` or `unknown` with a non-null source profile.
- Mobile sanitizer mirrors the same invariant and fails closed on an unknown profile
  value. It does not synthesize a generic Apple Log label from unvalidated text.
- Asset Detail uses these exact user-facing states:
  - `Apple Log 1 (unconverted)`;
  - `Apple Log 2 (unconverted)`;
  - applied transform labels remain unavailable in this feature;
  - `Ordinary video` for `not_log`;
  - `Video profile unknown (unconverted)` for `unknown`.
- The existing requested/applied preset and transform status remain visible. No UI claims
  Rec.709 output for either Apple Log profile.
- No TypeScript, Expo SDK upgrade, Node version change, native module, or AVFoundation
  dependency is introduced. Mobile changes remain React Native + Expo managed workflow +
  JavaScript.

### 6. Existing Specification Alignment

- `docs/ideas/20260711_2-apple-log-preview.md` remains the Phase 2B formal-preview and
  provenance specification. This feature supersedes only its FFprobe-only detection
  input, nullable Apple Log profile, Apple Log 2-as-unknown assumption, and single
  automatic-preset resolution details.
- All existing Phase 2B rules remain in force unless explicitly changed here:
  verified original prerequisite, automatic formal resolution, immutable attempts and
  provenance, absent/disabled fallback, registered-invalid terminal failure, exact result
  authority, and no identity/test/custom promotion.
- The Apple Log distribution/LUT policy is updated to show Phase C test-LUT completion,
  the Apple Log 2 detection requirement, and the continued absence of Rec.709 transforms.

## Non-Functional / Technical Notes

- Parsing work is proportional to inspected metadata box count, not media payload size.
  The parser seeks over `mdat` and must not allocate based on declared box size.
- On the supplied 33 MiB Apple Log 2 file and 2.9 MiB ordinary file, container parsing
  excluding FFprobe should complete within `1000 ms` per file on the development Mac.
  Unit tests use a deterministic clock or broad enough threshold to avoid CI flakiness;
  an integration timing check is diagnostic, not a correctness gate on slower CI hosts.
- Peak parser-owned retained bytes must remain below the `1048576` cumulative read bound.
- All identifiers and box types are ASCII. Code and generated artifacts remain ASCII
  except existing user-facing localized content.
- Logs may contain asset IDs, attempt IDs, stable error codes, counts, parser version, and
  timings. They must not contain host paths, filenames, tokens, raw metadata, raw atom
  payloads, unknown identifier text, location data, or FFprobe stderr.
- Parser tests must include random malformed box sequences or property-based generation
  with deterministic seed, in addition to hand-authored boundary cases. A test failure
  must report only the generated case identity, not user media bytes.
- Docker remains the canonical Backend runtime. The same Python parser and pinned FFprobe
  binary are used on the Mac mini and future review environment.
- The current Docker build contexts must remain unable to include repository-root
  `data/`. Compose/build regression tests fail if a build context expands to include the
  local fixture workspace without an explicit reviewed exception.

## Acceptance Criteria

- `data/` is ignored by Git and neither supplied recording appears in `git status`, the
  index, committed objects, Docker build context, test snapshots, or application logs.
- The parser recognizes the two exact official identifiers and no prefix, suffix,
  case-folded, embedded, or substring variant.
- The supplied Apple Log fixture is classified as `apple_log` with
  `source_profile = apple-log-2` and requests `generated-apple-log2-rec709`.
- The supplied ordinary fixture is classified as `not_log` with
  `source_profile = null` and requests `compress-only`.
- Deterministic unit fixtures prove `com.apple.rec2020.apple-log` produces
  `source_profile = apple-log-1` and requests `generated-apple-log-rec709`.
- Both Apple Log profiles complete with `compress-only` fallback and the exact unavailable
  provenance while both profile-specific transforms are absent or disabled.
- For either reserved transform, only `absent` or explicitly `disabled` is allowed.
  Valid-enabled, registered-invalid, hash-mismatched, or reserved-namespace-collision
  states prevent migration, startup, API use, and worker processing for this feature.
- Asset Detail and Preview Review show the exact profile-specific unconverted label through
  one shared pure helper and never show Rec.709 or an applied color-transform claim.
- Missing `logs` plus exact triple-BT.709 is `not_log`; missing or unsupported signal is
  `unknown`; conflicting recognized signals are `unknown`.
- Track-correlation tests cover multiple video tracks, duplicate/missing/malformed
  `track_ID`, FFprobe `stream.id` mismatch, multiple sample descriptions, and fields
  outside each profile's explicit color allowlists. No test may combine one track's
  `logs` with another track's FFprobe fields.
- Decoy tests place recognized strings inside `mdat`, `hoov`, audio sample entries, and
  unknown boxes. Only a direct `logs` child of the matched visual sample description in
  the single authoritative `moov` is evidence. Missing/duplicate `moov`, invalid `stsd`,
  short VisualSampleEntry fixed fields, and wrong child offsets fail safely.
- Non-BMFF inputs produce `unsupported_container`; exact triple-BT.709 is `not_log` and
  every other FFprobe combination is `unknown`.
- Malformed size, extended size, nesting, truncation, out-of-parent offset, excessive box
  count, excessive metadata reads, excess tracks/descriptions/identifiers, symlink input,
  non-regular input, and source replacement all fail with stable safe codes and no preview.
- Parser and FFprobe identity race tests prove bytes from a replacement path are never
  combined with evidence from the originally opened file.
- Migration race tests mutate each reserved preset after locked preflight and after schema
  rebuild. Every mutation is detected by the final registry snapshot comparison and rolls
  back schema, data, and marker.
- Detector v1 artifacts cannot enable v2 runtime capability. Missing or mismatched v2
  rule, manifest, certificate, parser version, FFprobe version, or migration marker keeps
  Phase 2B disabled without changing existing assets.
- Direct SQL tests reject every invalid asset/attempt/provenance combination, including
  profile-only UPDATE, wrong profile/preset pair, non-Log with a profile, Apple Log with
  null/unknown profile, and partial pre-detection evidence. Migration failure injection
  proves exact rollback, schema identity, and foreign-key integrity.
- Successor schema reports minimum client `0.4.0` even when runtime detection is stopped.
  A `0.3.0` client is rejected before Phase 2 confirmation, delivery, and deletion, while
  `0.4.0` renders the profile-specific unconverted labels.
- `data/` and `data/detector-certification-v2.json` are Git-ignored. Certification rejects
  wrong ownership/mode, symlinked descriptor/root/media, path escape, hash mismatch, and
  immediately cleans temporary snapshots on success, handled failure, timeout,
  interruption, and catchable signal. A forced-termination residue is removed at the next
  certification start only after fixed namespace, owner, type, no-symlink, mode, and
  minimum `300-second` age validation; unsafe or ambiguous residue fails closed.
- Formal-preview, processed-result delivery, preview confirmation, safe-delete candidate,
  managed rendition, upload, Mobile sanitizer/UI, and migration regression suites pass.
- `cd backend && uv run pytest`, `npm test`, `npm run lint`,
  `npx expo install --check`, `npx expo export --platform ios`,
  `docker compose config`, detector certification with the local fixture root, and
  `git diff --check` all pass.

## Release Checklist

- Before App Store submission, re-check the official Apple LogTransferFunction identifier
  documentation and record the source URL, check time, and approving role. A changed,
  removed, or still-unreviewed Beta contract blocks submission until the detector rule and
  parser contract are reviewed and versioned; remote configuration must not bypass this
  gate.

## Open Questions

- A controlled real Apple Log 1 recording is not currently available. It is optional for
  this release because the official identifier and deterministic parser path are fixed,
  but should be added to external certification evidence before enabling a future Apple
  Log 1 Rec.709 transform.
- The exact forward migration number is assigned by `plan-feature` after confirming the
  deployed schema ledger. Existing migration files are not edited solely to obtain a
  preferred number.

## Durable Docs Impact

- Update candidates:
  - `docs/product-requirements.md`: Apple Log 1/2 detection, separate automatic preset
    identities, unconverted fallback, and `0.4.0` minimum client rollout.
  - `docs/functional-design.md`: combined container/FFprobe classification, strict
    `source_profile` invariant, API/UI states, and certification flow.
  - `docs/architecture.md`: bounded descriptor-based ISO BMFF parser, same-file identity,
    manifest v2 gate, profile-specific resolver, and forward migration.
  - `docs/repository-structure.md`: parser module/tests, detector v2 artifacts, and ignored
    local `data/` fixture workspace.
  - `docs/development-guidelines.md`: bounded binary parser rules, no whole-file string
    search, external media handling, race tests, and safe logging.
  - `docs/glossary.md`: Apple Log 1, Apple Log 2, `logs` signal, `source_profile`, and
    unconverted formal preview.
  - `docs/ideas/20260711_2-apple-log-preview.md`: explicit supersession/linkage notes and
    removal of the FFprobe-only/Apple Log 2 unknown assumption.
  - `docs/ideas/iphone_applelog_app_distribution_and_lut_policy.md`: current phase status
    and Apple Log 2 detection before any Rec.709 transform.
- Update timing: `plan-feature` identifies exact sections; `implement-feature` updates the
  durable documents in the same change as the stable implementation contract.
- Reason: the feature changes durable detection authority, profile vocabulary,
  automatic preset identity, formal-preview behavior, security boundaries, and Mobile
  user-facing states.
