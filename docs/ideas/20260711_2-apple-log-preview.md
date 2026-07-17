# Feature Spec

## Metadata

- Date: 2026-07-11
- Feature name: Apple Log preview with selectable managed LUT provenance
- Status: draft
- Related files:
  - docs/product-requirements.md
  - docs/functional-design.md
  - docs/architecture.md
  - docs/development-guidelines.md
  - docs/glossary.md
  - docs/repository-structure.md
  - docs/ideas/20260711_3-resumable-original-finalization.md (Phase 2A prerequisite)

## Background

Current LOG safety gates correctly prevent an identity LUT from being presented as Rec.709 conversion, but they also prevent users from reviewing a verified Apple Log original when no transform is available. Phase 2B operates only on the file_verified original produced by Phase 2A. It always preserves the exact detection, requested preset, applied preset, and transform evidence used for each preview.

Apple documents that Final Cut Pro uses an Apple 3D LUT to convert Apple Log to Rec.709, but that documentation is not a grant to extract or redistribute a LUT file. The project must not bundle a LUT copied from Final Cut Pro or another Apple product. Source: https://support.apple.com/ja-jp/guide/final-cut-pro/ver24f966423/mac

## Implementation Gate

Phase 2Bの`compress-only` fallbackは、Rec.709変換用LUTが未登録でも実装できる。以下の証跡は、それぞれ該当機能を有効化する前に承認manifestへ記録する。

1. Apple Logの自動判定を有効化する前に、controlled Apple Log/non-Apple-Log fixtureで、pinned Docker ffmpeg/ffprobe上の正確なmetadata ruleを検証する。detector manifestにはfield path、許容値、rule version、fixture digest、source referenceを記録し、filename、Mobile flag、pixel analysisを根拠にしない。
2. `generated-apple-log-rec709`を有効化する前に、公開プロファイルまたは利用条件を確認済みのsource、generator/library version、parameter、LUT SHA-256、Rec.709 tag、fixture比較結果をmanifestへ記録する。
3. custom LUTを有効化する前に、Mac mini側のrepo外LUT rootに置くmanifestへpreset id、由来、利用条件、version、SHA-256、形式・grid検証結果を記録する。

要求presetが未登録または無効化済みの場合、identity LUTを変換として扱わず、`compress-only`の未変換previewを生成する。登録済みLUTの改ざん、hash不一致、形式不備、FFmpeg適用失敗はterminal failureとする。

## Target Users / Use Cases

- iPhone users who record Apple Log video and need to review a verified backup before and after a Rec.709 transform becomes available.
- Users who need to distinguish a verified Apple Log conversion, an explicitly unconverted Apple Log preview, and a normal video preview.
- A Mac mini administrator who manages server-side custom LUT presets without allowing arbitrary LUT files from Mobile.

## Scope

- Run only after Phase 2A has finalized the original with verification_status = file_verified; do not run during chunk upload or assembly.
- Persist log_detection_status as apple_log, not_log, or unknown, plus nullable log_profile, detector rule version, and a bounded evidence summary. is_log remains a legacy hint and never authorizes a transform.
- Detect only the single initial profile apple_log. Apple Log 2, unrecognized future profiles, and ambiguous metadata are unknown until separately specified and approved.
- Always expose the server-provided `compress-only` preset. When the detector identifies apple_log, request `generated-apple-log-rec709` by default unless the user selected an enabled custom LUT.
- Register the generated Apple Log to Rec.709 preset only after its transform evidence is approved. Its manifest includes source profile, target profile, generator/source, version, LUT SHA-256, and license/terms reference.
- Allow Mac mini-side custom LUT presets only from the repo-external LUT root and only when their manifest validates. Mobile selects a returned preset but never uploads LUT files.
- Create exactly one formal preview_provenance record for every Phase 2B ready video preview. Apple Log Rec.709/custom-LUT output uses transform_kind = lut. Non-Log and Apple Log fallback use transform_kind = none, with requested/applied preset and color-transform status recorded.
- Replace the temporary LOG safety trigger with SQLite triggers that reject preview_ready without a matching formal provenance record. Trigger conditions are based on log_detection_status, never on legacy is_log.
- Show detection, requested/applied preset, and transform state in Asset Detail. Show that an Apple Log fallback is unconverted; suppress preview and confirmation only for failed or unprovenanced assets.
- Keep Phase 1 is_log = true assets failed and audit-only. They must be uploaded through Phase 2A before they can be considered for Apple Log processing.
- At Phase 2B rollout, migrate only Phase 2A session-derived assets where type = video and verification_status = file_verified. Direct Phase 1 assets are excluded and retain their existing preview behavior.

## Out of Scope

- Pixel-based LOG inference, approximate color heuristics, filename inference, or silently treating unknown videos as Apple Log.
- Manual override for unknown profiles.
- LUT upload from Mobile, Pixel-based custom-LUT recommendation, Apple Log 2, multiple output color spaces, HDR targets, or creative-LUT authoring UI.
- Administrator UI/API for LUT preset management. This feature manages manifests and files on the Mac mini only.
- Preview retry UI, original alteration, Backend original deletion, and automatic iPhone deletion.
- Upload-session creation, chunk transfer, resume, hash verification, and original finalization, which belong to Phase 2A.

## User Flow

1. Phase 2A finalizes the original with verification_status = file_verified.
2. A profile-aware preview worker reads only approved, sanitized ffprobe fields and records the detection result before rendering a preview.
3. For apple_log with an enabled Rec.709 or custom preset, the worker validates the manifest and LUT SHA-256, applies the preset, records transform_kind = lut provenance, and commits provenance and preview_ready together.
4. For not_log, unknown, or a missing/disabled requested preset, the worker generates a `compress-only` preview, records transform_kind = none provenance, and commits it with preview_ready. Apple Log fallback includes `color_transform_status = unavailable` and `color_transform_error_code = lut_preset_unavailable`.
5. For a registered preset with manifest validation, hash, format, or ffmpeg failure, the worker terminally fails the job and preview without exposing a preview stream or confirmation action.
6. Mobile shows the detection and transform result and permits playback/confirmation only for a provenance-backed ready preview. Apple Log fallback is visibly unconverted.
7. Phase 2B migration atomically creates one unique profile-aware preview job with dedup key `phase2b-profile-preview:{asset_id}` for each eligible Phase 2A session-derived video. Only when that insert succeeds does it invalidate the old formal preview/review state and increment the asset preview generation. It does not delete historical derived files or requeue Phase 1 direct assets.

## Functional Requirements

### Detection and Preset Contracts

- is_log remains a legacy Mobile hint and never authorizes LUT use.
- Apple Log processing accepts only a Phase 2A-finalized, file_verified video original.
- The detector reads an approved detector manifest, not ad hoc code constants. The manifest identifies an exact ffmpeg/ffprobe version, allowed metadata field paths and values, detector rule version, fixture SHA-256, and source reference.
- The preset manifest is verified before ffmpeg executes. A missing or disabled requested preset causes `compress-only` fallback; an altered, hash-mismatched, malformed, or ffmpeg-rejected registered LUT is a terminal failure.
- A generated Apple Log transform output is tagged Rec.709 and retains H.264/AAC/1080p preview constraints. `compress-only` and custom LUT output must not claim Rec.709 unless its approved preset explicitly establishes that target.
- The project retains managed generator/test assets inside the Backend image and repo-external custom LUT files under the configured Mac mini LUT root. It does not log LUT contents, raw full metadata, original paths, local URIs, or tokens.

### Detection and Provenance Persistence

- assets stores log_detection_status, nullable log_profile, log_detection_rule_version, and bounded log_detection_evidence_json. Evidence stores only the approved field/value summary and fixture/rule identifiers.
- preview_provenance has one row per formal derived preview and references its asset and derived file. It stores transform_kind, source profile, target color space, detector rule version, detector_manifest_sha256, evidence digest, creation time, requested preset ID, applied preset ID, color_transform_status, nullable color_transform_error_code, and nullable LUT preset version, SHA-256, and preset_manifest_sha256.
- assets has nullable formal_preview_id, pointing to its one active formal derived preview. preview_provenance.derived_file_id is unique, and a transaction may replace formal_preview_id only after writing the matching provenance.
- For apple_log with a registered applied LUT, transform_kind = lut and the applicable LUT/preset-manifest fields are required. For apple_log fallback, transform_kind = none, applied preset = compress-only, color_transform_status = unavailable, and color_transform_error_code = lut_preset_unavailable are required. For not_log or unknown, transform_kind = none and color_transform_status = not_requested unless a valid user-selected LUT was applied.
- A BEFORE INSERT OR UPDATE SQLite trigger applies only when type = video AND verification_status = file_verified. It rejects preview_status = preview_ready unless formal_preview_id has matching provenance. It permits lut or the specified fallback none provenance for apple_log, and none or a valid selected-lut provenance for not_log/unknown.
- Preview streaming and confirmation use formal_preview_id and its provenance row for those Phase 2B videos; a direct database write or old worker cannot bypass it. Phase 1 direct images and videos remain outside this provenance trigger.
- Existing identity-LUT derived files and historical jobs remain for audit and are never served as Rec.709 formal previews. A new `compress-only` fallback preview is formal only when it records the required provenance and unconverted state.

### Phase 2B Migration and Preview Generation Fence

- `assets.preview_generation` is a non-null monotonically increasing integer. `jobs.preview_generation` is null for non-preview jobs and is required for every session-derived video preview job. A preview worker may mutate its asset, formal preview, or review state only when its job generation equals the current asset generation at both claim and commit time.
- The Phase 2B migration adds both columns, initializes existing eligible Phase 2A assets and their `preview` or `lut_preview` jobs to generation `0`, and gives every new profile-aware job generation `1`. Future formal-preview invalidations must increment the generation and create a job carrying that exact new value.
- Deploy the migration in maintenance mode: stop and drain every pre-Phase-2B API/worker process before the transaction begins, apply the schema and data migration, then start only workers that enforce the generation check. A running pre-fence worker is not allowed to coexist with the migration.
- For each eligible asset, one SQLite transaction first performs `INSERT ... ON CONFLICT(dedup_key) DO NOTHING` for the generation-`current + 1` profile-aware job. Only if that insert creates a row does the transaction increment `assets.preview_generation`, clear `formal_preview_id`, set `preview_status = preview_generating`, and reset `review_status = not_reviewed`. An existing dedup key, regardless of its job status, leaves every asset field unchanged.
- A queued, recovered, or otherwise stale Phase 2A preview job whose generation does not equal the asset generation is terminally marked `failed` with stable error code `preview_generation_superseded`. It writes no derived file, provenance, asset preview status, formal preview, or review state. The error is operational history, not a user-visible preview failure.

### Phase 2C Compatibility

- Phase 2C requires provenance-backed preview_ready for every video. Apple Log Rec.709/custom-LUT preview is backed by lut provenance, and non-Log or unconverted Apple Log fallback by none provenance. All may satisfy the same safe-delete precondition after preview_confirmed because the immutable original's verification and the user's content confirmation, not a color-quality claim, are the deletion basis.

## Non-Functional / Technical Notes

- Keep React Native + Expo, FastAPI, SQLite, ffmpeg, and the original non-mutation rule.
- Keep detector lookup, manifest validation, ffmpeg command construction, provenance persistence, and SQLite trigger installation in separate Backend services/repositories.
- The Phase 2A finalization boundary is reusable by normal and Apple Log preview jobs. Phase 2B replaces new session lut_preview creation with one profile-aware preview job; historical lut_preview jobs remain audit-only.

## Acceptance Criteria

- A detected Apple Log fixture whose requested Rec.709 preset is not registered or is disabled produces a `compress-only` preview, completes the job, records `color_transform_status = unavailable` and `color_transform_error_code = lut_preset_unavailable`, and displays that it is unconverted.
- The approved Apple Log fixture produces a Rec.709 preview with complete lut provenance and becomes preview_ready only after preset and detector manifest hashes validate.
- The approved non-Apple-Log and unknown-profile fixtures generate a normal `compress-only` preview with complete none provenance and become preview_ready.
- An Apple Log 2 fixture remains unknown and is never presented as Rec.709 converted.
- A missing or disabled LUT falls back before ffmpeg LUT processing. An altered, malformed, hash-mismatched, or ffmpeg-rejected registered LUT fails before serving a preview.
- Direct updates and an old-worker simulation cannot set any Phase 2B video to preview_ready without the required provenance. apple_log may use none only for the specified unconverted fallback; a lut provenance requires a valid applied preset.
- Altering either stored manifest after preview generation causes the provenance validation path to reject that preview for stream/confirmation until a new formal preview is generated.
- Existing Phase 1 is_log = true assets remain failed until they are uploaded through Phase 2A.
- Phase 2B migration requeues every eligible Phase 2A session-derived file_verified video exactly once. In one transaction it inserts the generation-`current + 1` profile-aware preview job with dedup key `phase2b-profile-preview:{asset_id}` and, only when that insert is new, increments the asset generation, clears formal_preview_id, sets preview_status = preview_generating, and resets review_status = not_reviewed. Repeating the migration after a queued, done, or failed Phase 2B job creates no duplicate job and leaves the formal preview/review state unchanged.
- A queued or lease-recovered Phase 2A preview or lut_preview job at generation `0` is fenced after Phase 2B increments the asset generation: it cannot change the asset, formal preview, or review state and becomes `preview_generation_superseded`. The deployment procedure stops all pre-fence workers before migration, and an integration test covers a stale job attempting to commit after the migration.
- The Phase 2A validation suite has already demonstrated that no preview job is created before original finalization and hash verification.
- Development Build tests cover transformed Apple Log playback, unconverted Apple Log fallback playback and label, normal video playback, custom-LUT selection, and registered-invalid-LUT failure using Phase 2A-finalized originals.

## Open Questions

- The fixture-verified ffprobe metadata fields and values are an implementation gate for Apple Log auto-detection. Before approval, the system must not identify a video as Apple Log or claim Rec.709 conversion.
- The Apple Log transform's exact formula/library, generator version, fixture comparison threshold, and Rec.709 tags must be approved before `generated-apple-log-rec709` is enabled.
- The custom-LUT manifest schema, supported formats/grid limits, and Mac mini administration workflow need separate specification. Mobile LUT upload remains out of scope.

## Durable Docs Impact

- Updated now: product requirements, functional design, architecture, repository structure, development guidelines, and glossary.
- This feature follows the validated Phase 2A finalization boundary. Before planning implementation, review this specification together with the Apple Log distribution and LUT policy, then define the custom-LUT manifest and versioned API contract.
