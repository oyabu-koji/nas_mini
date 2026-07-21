# Feature Spec

## Metadata

- Date: 2026-07-11
- Feature name: Apple Log detection and formal preview provenance
- Status: draft
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

## Implementation Gate

The following evidence must exist before the corresponding behavior is enabled.

1. Before the worker sets `log_detection_status = apple_log`, controlled Apple
   Log/non-Apple-Log fixtures must prove the exact metadata field paths and values on the
   pinned Docker ffmpeg/ffprobe version. The detector manifest records the rule version,
   fixture digests, allowed values, and source reference. Filenames, the Mobile `is_log`
   hint, and pixel heuristics are never detector evidence.
2. The managed-preset feature must provide a validated `compress-only` record and the
   requested/applied-preset snapshot contract before this feature enables an unconverted
   fallback.
3. `generated-apple-log-rec709` remains disabled until a later dedicated feature has
   recorded its legal/source evidence, generator or library version, parameters, LUT
   SHA-256, Rec.709 output tags, and fixture comparison result. This feature must not
   create, bundle, extract, or imply that transform.

When a requested preset is missing or disabled, this feature creates an unconverted
`compress-only` preview/result and completes its job. A registered preset that is altered,
hash-mismatched, malformed, or rejected by ffmpeg is a terminal failure. Availability and
integrity are intentionally different outcomes.

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
- Use the managed-preset feature's requested-preset snapshot. For a detected Apple Log
  asset with no user-selected valid custom preset, request
  `generated-apple-log-rec709` only when that preset is enabled. While it is disabled or
  absent, request and apply `compress-only`.
- Create formal preview provenance for each ready Phase 2B session-derived video. It
  records detection evidence; requested and applied preset; transform kind; color
  transform state; and, when a LUT was actually applied, the preset version, LUT SHA-256,
  and preset-manifest SHA-256. Its derived file is also the active `processed_result`;
  the result's generation must equal the asset generation.
- For a detected Apple Log fallback, require `transform_kind = none`,
  `applied_preset_id = compress-only`,
  `color_transform_status = unavailable`, and
  `color_transform_error_code = lut_preset_unavailable`. Mobile displays this result as
  unconverted Apple Log, never as Rec.709.
- Replace the temporary LOG safety trigger with Phase 2B SQLite constraints that reject
  `preview_ready` for a verified session-derived video unless its active formal preview
  has matching provenance. A direct database update or an old worker cannot bypass this
  gate.
- Migrate only Phase 2A session-derived `file_verified` videos. The migration creates one
  profile-aware preview job per eligible asset and fences old-generation preview jobs so
  they cannot overwrite the active preview, review state, or provenance.
- Permit playback, confirmation, and the preceding result-delivery feature only for the
  active provenance-backed ready preview. An Apple Log fallback is eligible because the
  user is confirming an immutable verified original and a visibly unconverted derived
  result, not a color-quality claim.

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

## User Flow

1. Phase 2A retains and verifies a video original.
2. The managed-preset feature snapshots the user's selection or provides the current
   `compress-only` default for the rendition.
3. A profile-aware worker reads only the approved, sanitized ffprobe metadata described
   by the detector manifest and persists `apple_log`, `not_log`, or `unknown`.
4. For `apple_log` without an enabled approved Rec.709 preset, the worker renders the
   `compress-only` output, writes formal fallback provenance, and marks it ready.
5. For `not_log` or `unknown`, the worker writes formal `transform_kind = none`
   provenance for the managed-preset result unless a valid selected LUT was applied.
6. A registered preset with validation, hash, format, or ffmpeg failure terminally fails
   its job and exposes neither playback, confirmation, nor result delivery.
7. Mobile shows detection, requested/applied preset, and transform state. It visibly
   labels Apple Log fallback as unconverted and may then play, confirm, or explicitly
   save the derived result.
8. When a later approved Rec.709 preset is enabled, it creates a new generation from the
   same immutable original. It never rewrites the historical fallback result or its
   provenance.

## Functional Requirements

### Detection and Provenance Contract

- The detector reads an approved manifest, not ad hoc code constants. The manifest pins
  ffmpeg/ffprobe version, approved field paths/values, rule version, fixture digest, and
  source reference.
- `is_log` remains a legacy hint only. It cannot set `log_detection_status`, select a
  LUT, or make a preview deliverable.
- `preview_provenance` has one row per formal derived preview and references its asset
  and derived file. It stores detection result, source profile, target color space,
  detector evidence digest, requested/applied preset IDs, transform kind, color
  transform state/error, and nullable LUT/preset evidence.
- For `apple_log` with a validated applied LUT, `transform_kind = lut` and all applicable
  LUT/preset evidence is required. For the Apple Log fallback, the required
  `compress-only` unavailable fields are mandatory. For `not_log` or `unknown`,
  `transform_kind = none` and `color_transform_status = not_requested` are required
  unless the managed-preset feature applied a valid selected LUT.
- A formal preview cannot be marked ready until its provenance and derived file are
  committed together in one transaction. The same transaction creates the new active
  `processed_result` with matching generation and changes
  `active_processed_result_id`. Preview streaming, confirmation, and result delivery
  validate the active provenance and active-result relation each time. The
  `processed_results` foreign-key, same-asset, ready-state, and unique-derived-file
  invariants from the delivery feature apply to this transaction.

### Migration and Preview Generation Fence

- `assets.preview_generation` is a non-null monotonically increasing integer.
  `jobs.preview_generation` is null for non-preview jobs and required for every
  session-derived video preview job.
- The Phase 2B migration initializes eligible Phase 2A assets and their preview jobs at
  generation `0`, then inserts one new profile-aware job at generation `1` with dedup key
  `phase2b-profile-preview:{asset_id}`. Only a successful new insert increments the asset
  generation, clears its active formal preview and `active_processed_result_id`, sets
  `preview_generating`, and resets review status in the same transaction.
- Deployment enters maintenance mode before this migration: all pre-Phase-2B API and
  worker processes stop and drain, migration completes, and only generation-aware workers
  start afterwards.
- A claimed, recovered, or late old-generation preview job may not write a derived file,
  provenance, asset preview status, or review state. It terminates with stable operational
  error code `preview_generation_superseded`.

### Failure and Presentation Contract

- A missing or disabled requested preset falls back before any LUT filter invocation.
  It is not a job failure.
- An enabled registered LUT whose manifest, file hash, format, grid, numeric contents,
  or ffmpeg application fails is terminally failed. It is not silently replaced by a
  different LUT.
- The UI must show the distinction among transformed, unconverted/unavailable, and
  failed states. It must not label `compress-only`, identity, test, or custom output as
  Apple Log to Rec.709 without the later approved transform evidence.

## Non-Functional / Technical Notes

- Keep React Native + Expo, FastAPI, SQLite, ffmpeg, managed preset manifests, and the
  immutable-original rule.
- The detector consumes bounded, sanitized metadata only. It does not retain raw full
  ffprobe output, original paths, local URIs, tokens, or LUT contents in routine logs or
  API responses.
- Controlled fixtures must cover Apple Log, ordinary video, unknown profile, Apple Log
  2, a missing preset, a disabled preset, and a registered invalid preset.
- Historical identity-LUT derived files and old `lut_preview` jobs remain audit-only.
  They are never served as formal Rec.709 previews.

## Acceptance Criteria

- A verified Apple Log fixture with no enabled Rec.709 preset produces a ready
  `compress-only` result with formal provenance,
  `color_transform_status = unavailable`, and
  `color_transform_error_code = lut_preset_unavailable`; the UI calls it unconverted.
- Approved ordinary and unknown-profile fixtures produce ready provenance-backed results
  without a false Apple Log or Rec.709 claim.
- An Apple Log 2 fixture remains `unknown` and is never presented as Rec.709 converted.
- A missing or disabled preset falls back before LUT processing. An altered, malformed,
  hash-mismatched, or ffmpeg-rejected registered LUT fails without playback,
  confirmation, or delivery.
- Direct updates and stale-worker simulations cannot set an eligible Phase 2B video to
  `preview_ready` without matching formal provenance.
- Migration requeues each eligible Phase 2A video exactly once. Repeating it with an
  existing queued, done, or failed Phase 2B job does not change asset state or duplicate
  a job.
- A generation-`0` Phase 2A preview or `lut_preview` job cannot overwrite the formal
  preview or review state after the Phase 2B migration.
- This feature does not enable `generated-apple-log-rec709` or make a Rec.709 claim until
  the future transform feature satisfies its separate implementation gate.

## Open Questions

- The exact fixture-verified ffprobe metadata fields and allowed values for the initial
  Apple Log detector.
- The later Apple Log to Rec.709 transform's generator/library, parameters, quality
  threshold, Rec.709 tags, and source/license evidence.
- The detailed migration path by which the managed-preset rendition model becomes the
  active formal preview model. It must preserve existing Phase 1 behavior and the
  generation fence above.

## Durable Docs Impact

- Update candidates: `product-requirements.md`, `functional-design.md`,
  `architecture.md`, `development-guidelines.md`, `glossary.md`, and
  `repository-structure.md`.
- Update timing: review the delivery, managed-preset, and this Apple Log specification
  together; then update durable documentation once their shared result/provenance model
  is confirmed.
- Reason: this stage preserves the durable safety requirements but moves generic result
  delivery and preset ownership into explicit prerequisite features.
