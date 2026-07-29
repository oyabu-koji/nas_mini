# Feature Spec

## Metadata

- Date: 2026-07-28
- Feature name: Safe delete candidate
- Status: confirmed
- Phase: 2C
- Priority: required before the initial Development Build release validation
- Related files:
  - `docs/ideas/initial-requirements.md`
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/repository-structure.md`
  - `docs/ideas/20260711_3-resumable-original-finalization.md`
  - `docs/ideas/20260711_2-apple-log-preview.md`
  - `docs/ideas/20260726_1-release-contract-alignment.md`
  - `backend/app/services/asset_read.py`
  - `src/features/original-deletion/`

## Background

Phase 2A can resumably transfer a video, verify each chunk, compare the completed
original with the Mobile-supplied SHA-256, and create a session-derived asset with
`verification_status = file_verified`. Phase 2B can associate that verified original
with one current, immutable, provenance-backed formal preview. The user can then play
and explicitly confirm the formal preview.

The Backend already stores `delete_candidate_status`, but all assets remain
`not_candidate`. The current Mobile deletion predicate independently checks the verified
asset, compatible Phase 2B capability, ready formal preview, review confirmation, local
mapping, and local deletion state. It does not prove the completed upload-session and
chunk relation when deciding whether to expose the deletion action.

Phase 2C adds the missing Backend authority. It promotes only a session-derived,
whole-file-verified video with a valid current formal preview and explicit review
confirmation to `safe_to_delete_candidate`. This state means that Mobile may offer the
existing explicit iPhone-original deletion flow. It never requests or performs a
deletion by itself.

## Goal

- Make the Backend the authority for the Phase 2 safe-delete candidate decision.
- Derive the decision from one complete upload session, verified chunks, whole-file
  identity, current formal-preview provenance, and user confirmation.
- Include the visibly unconverted Apple Log `compress-only` fallback as an eligible
  content-review preview without claiming Rec.709 conversion.
- Make stale or partially valid relations fail closed and never leave an invalid
  `safe_to_delete_candidate` value behind.
- Require a Phase 2C-compatible Mobile before the Phase 2 iPhone-original deletion
  action can be shown.
- Preserve the Phase 1 direct-asset manual deletion behavior.

## Confirmed Decisions

### Candidate Is Not Deletion

- `safe_to_delete_candidate` is a Backend status and an additional Mobile eligibility
  input. It is not a command, queue item, or proof that local deletion occurred.
- Mobile continues to require explicit user action and the native Photos confirmation.
- Only the mapped iPhone Photos original may be deleted. Backend originals, derived
  files, processed results, provenance, upload sessions, chunks, and asset records are
  retained.
- Successful or failed local deletion does not update
  `assets.delete_candidate_status`; `local_delete_status` remains Mobile-local.

### Phase Boundaries

- Phase 1 direct images and historical direct videos keep
  `delete_candidate_status = not_candidate`.
- Phase 1 direct assets keep their existing manual-deletion predicate and do not require
  the Phase 2C capability or candidate status.
- Only `type = video`, `verification_status = file_verified` assets linked to a completed
  Phase 2 upload session can become candidates.
- Managed renditions, user-selected identity/test/custom LUTs, processed-video saves,
  the legacy `is_log` hint, and the active processed-result pointer are never candidate
  authority.

### Versioned Rollout

- The Phase 2C Mobile and application version is `0.3.0`.
- The offline Phase 2C schema migration identity is
  `009_safe_delete_candidate`, with
  `008_apple_log_formal_preview` as its exact predecessor.
- `GET /api/v1/capabilities` adds `features.safe_delete_candidate`.
- Capability discovery and every Phase 2 endpoint use one shared rollout resolver in
  this fixed order:
  1. if any closed Phase 2B or Phase 2C presence signal defined below is present,
     validate the complete `008_apple_log_formal_preview` marker and exact SQL identity;
  2. validate the `009_safe_delete_candidate` marker, metadata, and exact SQL identity
     when any closed Phase 2C presence signal defined below is present;
  3. return `503 phase2b_migration_schema_identity_mismatch` or
     `503 phase2c_migration_schema_identity_mismatch` for the first invalid identity,
     regardless of client version or runtime availability;
  4. derive the immutable rollout minimum from the valid markers;
  5. reject a missing, malformed, or lower client version with
     `409 incompatible_client`; and
  6. only then evaluate the Phase 2B runtime detector and feature flags.
- The closed Phase 2B presence-signal set is:
  - the `008_apple_log_formal_preview` row in `schema_migrations`, any row or the table
    named `phase2b_schema_metadata`, either `formal_preview_attempts` or
    `preview_provenance`, either `idx_formal_preview_attempts_asset_generation` or
    `idx_jobs_preview_generation`;
  - any of the `assets` columns `preview_generation`, `formal_preview_id`,
    `log_detection_status`, `source_profile`, `detector_rule_version`,
    `detector_manifest_sha256`, or `detector_evidence_sha256`, or the
    `jobs.preview_generation` column; and
  - any of these Phase 2B-exclusive triggers:
    `validate_phase2b_preview_job_insert`,
    `prevent_phase2b_lut_preview_job_insert`,
    `validate_non_preview_job_generation_insert`,
    `validate_formal_preview_attempt_insert`,
    `prevent_formal_preview_attempt_identity_update`,
    `prevent_formal_preview_related_job_update`,
    `validate_preview_provenance_insert`,
    `prevent_current_formal_preview_supersede`,
    `prevent_dual_formal_rendition_provenance`,
    `validate_managed_result_preview_generation`,
    `validate_asset_detection_identity_update`,
    `validate_formal_preview_pointer`, `validate_formal_preview_ready`,
    `prevent_terminal_formal_preview_attempt_update`,
    `prevent_terminal_formal_preview_attempt_delete`,
    `prevent_preview_provenance_update`, or
    `prevent_preview_provenance_delete`.
- Once a Phase 2B presence signal exists, identity validation requires the full expected
  `008` set and exact SQL digest, including the Phase 2B definitions of the rewritten
  `validate_active_processed_result` and
  `supersede_replaced_active_processed_result` triggers. The Phase 1/managed-preset
  `007` objects `renditions`, `rendition_provenance`,
  `assets.rendition_selection_generation`, and the pre-`008` versions of those two
  rewritten triggers are explicitly not Phase 2B presence signals.
- A database with valid migrations through `007_managed_preview_presets` and none of
  the closed Phase 2B signals is a normal Phase 2B-disabled state. It returns the
  existing capability response instead of a `503`.
- The closed Phase 2C presence-signal set is:
  - the `009_safe_delete_candidate` row in `schema_migrations`, any row or the table
    named `phase2c_schema_metadata`;
  - the named table constraint token `ck_assets_delete_candidate_status` in the
    `assets` entry of `sqlite_master.sql`; and
  - any of these Phase 2C-exclusive triggers:
    `prevent_safe_delete_candidate_asset_insert`,
    `enforce_safe_delete_candidate_asset_update`,
    `prevent_completed_upload_session_update`,
    `prevent_completed_upload_session_delete`,
    `prevent_completed_upload_chunk_insert`,
    `prevent_completed_upload_chunk_update`,
    `prevent_completed_upload_chunk_delete`,
    `prevent_finalized_session_asset_update`,
    `prevent_finalized_session_asset_delete`,
    `prevent_current_formal_derived_file_update`, or
    `prevent_current_formal_derived_file_delete`.
- Once any Phase 2C signal exists, identity validation first requires valid Phase 2B
  identity and then the full expected `009` marker, metadata row, named constraint,
  trigger set and definitions, repository SQL digest, and rebuilt `assets` table SQL
  digest. Any missing, extra, or mismatched Phase 2C identity component returns
  `503 phase2c_migration_schema_identity_mismatch`.
- The pre-existing `assets.delete_candidate_status` column is introduced by
  `001_initial` and is explicitly not a Phase 2C presence signal. A valid Phase 2B
  database with that original unconstrained column and none of the closed Phase 2C
  signals is a normal Phase 2C-disabled state, not a partial migration.
- A valid `009_safe_delete_candidate` marker and SQL identity establish the immutable
  Phase 2C rollout floor. While that floor is present, the Phase 2 endpoint minimum is
  always `0.3.0`, even when the detector is temporarily unavailable.
- `features.safe_delete_candidate` is a separate runtime flag. It is true only when the
  Phase 2B formal-preview runtime capability and the valid Phase 2C rollout floor are
  both present; otherwise it is false.
- A present but digest-mismatched or incomplete Phase 2B/2C marker, metadata row, or
  phase-specific schema object is a deployment configuration failure, not a disabled
  feature.
  Candidate promotion is disabled.
- Mobile `0.3.0` fails closed for a Phase 2 asset unless both
  `formalAppleLogPreview` and `safeDeleteCandidate` sanitize to `true`.
- Mobile refreshes capabilities when Asset Detail gains focus, on manual refresh, and
  immediately before it opens the native deletion confirmation. A failed refresh or a
  changed capability hides the Phase 2 deletion action.
- Before production Phase 2C rollout, the operator distributes `0.3.0` and requires
  every `0.2.0` client to restart or retire. A Backend cannot revoke a deletion action
  already held in memory by an old native client, so this is an explicit operational
  prerequisite rather than a property claimed from the API version gate alone.

## Target Users / Use Cases

- An iPhone user who wants a clear, conservative indication that a large video was
  completely verified on the Mac mini and its formal preview was confirmed.
- An Apple Log user who wants to free iPhone storage after reviewing either a valid
  LUT-applied formal preview or the explicitly labelled unconverted fallback.
- A Mac mini operator who needs an auditable query showing why a Phase 2 asset is or is
  not a safe-delete candidate.
- A developer who needs deterministic tests for every candidate condition and
  invalidation path before physical-device release validation.

## Scope

- Add one Backend candidate evaluator for session-derived Phase 2 videos.
- Validate upload-session completion, the exact required chunk set, and whole-file hash
  identity.
- Validate the current Phase 2B formal-preview result and its immutable provenance.
- With an available Phase 2B runtime snapshot, promote the candidate in the same
  transaction that first confirms an eligible formal preview.
- Preserve idempotency when an already confirmed eligible preview is confirmed again.
- Demote or prevent a candidate when any authoritative dependency is invalidated.
- Backfill already confirmed eligible Phase 2B assets during the Phase 2C migration.
- Protect candidate promotion and dependency mutation at the SQLite boundary so direct
  SQL cannot persist an invalid candidate.
- Return the resulting `delete_candidate_status` through the existing asset list, detail,
  and preview-confirmation responses.
- Add and sanitize the Phase 2C capability and effective minimum client version.
- Update the Mobile Phase 2 deletion predicate to require the capability and
  `safe_to_delete_candidate`, while leaving the Phase 1 predicate unchanged.
- Show a concise candidate state in Asset Detail without adding an automatic-delete
  action.
- Update the application version consistently in Expo, npm, the checked-in iOS project,
  and the shared client-version constant.
- Update the six durable documents with the implemented Phase 2C contract.

## Out of Scope

- Automatic iPhone original deletion, background deletion, or deletion scheduling.
- Backend original, derived-file, processed-result, provenance, asset, upload-session, or
  chunk deletion.
- A Backend endpoint that commands Mobile to delete local media.
- Changing the existing native Photos deletion and local outcome persistence mechanism.
- Making Phase 1 direct assets `safe_to_delete_candidate`.
- Re-verifying or upgrading historical Phase 1 direct assets to `file_verified`.
- Treating a managed rendition or downloaded processed-video copy as a formal preview.
- Apple Log to Rec.709 LUT creation, acquisition, licensing, registration, enablement,
  or quality approval.
- Enabling or certifying the Phase 2B detector, supplying controlled external recordings,
  or running the operator's production Phase 2B migration.
- Multiple Backend profiles, public cloud access, background synchronization, or
  original download.
- A new public jobs API, candidate queue API, or dedicated deletion queue screen.

## Eligibility Matrix

An asset is `safe_to_delete_candidate` only when every common condition and exactly one
allowed formal-preview row below are true.

### Common Conditions

| Area | Required state |
| --- | --- |
| Asset origin | `assets.type = video` and exactly one `upload_sessions.asset_id` relation |
| Session | `upload_sessions.type = video` and `status = completed` |
| Session identity | session `size_bytes` equals asset `size_bytes`; session expected SHA-256 equals asset `server_sha256` |
| Operational bounds | `1 <= size_bytes <= 1099511627776` and `1 <= chunk_size_bytes <= 8388608` |
| Chunk cardinality | `total_chunks = (size_bytes + chunk_size_bytes - 1) // chunk_size_bytes`, `1 <= total_chunks <= 131072`; each required index is an integer from `0` through `total_chunks - 1` |
| Chunks | `upload_chunks` is a verified-only sparse ledger: every persisted row has `status = verified`; every required index exists exactly once; no other index exists; for index `i`, start is `i * chunk_size_bytes`, end is `min(size_bytes - 1, ((i + 1) * chunk_size_bytes) - 1)`, and size is `end - start + 1` |
| Original verification | `assets.verification_status = file_verified` |
| Formal generation | `assets.preview_generation >= 1` |
| Preview state | `assets.preview_status = preview_ready` |
| Review state | `assets.review_status = preview_confirmed` |
| Formal pointer | non-null `assets.formal_preview_id` identifies the current ready result for the same asset and generation |
| Formal provenance | exactly one matching immutable `preview_provenance` and ready `formal_preview_attempt` exist |

### Allowed Formal-Preview Provenance

| Detection | Requested / applied preset | Required transform claim | Candidate |
| --- | --- | --- | --- |
| `apple_log` | `generated-apple-log-rec709` / `generated-apple-log-rec709` | `transform_kind = lut`, `color_transform_status = applied`, no transform error, non-null preset version, manifest SHA-256, and LUT SHA-256 | allowed for the future enabled transform |
| `apple_log` | `generated-apple-log-rec709` / `compress-only` | `transform_kind = none`, `color_transform_status = unavailable`, `color_transform_error_code = lut_preset_unavailable`, and no LUT identity fields | allowed as visibly unconverted fallback |
| `not_log` or `unknown` | `compress-only` / `compress-only` | `transform_kind = none`, `color_transform_status = not_requested`, no transform error, and no LUT identity fields | allowed |
| Any | managed identity, test, custom, stale, failed, superseded, missing, or mixed provenance | any | rejected |

The evaluator uses the formal relation addressed by `formal_preview_id` and
`preview_generation`. It does not fall back to another ready result or infer authority
from `active_processed_result_id`.

An unverified chunk is represented by its required ledger row being absent. Phase 2C
does not introduce a second chunk status. A non-`verified` insert remains a database
constraint violation, not an evaluator state.

## User Flow

### New Phase 2 Video

1. Mobile finishes a resumable upload and receives a session-derived
   `file_verified` video asset.
2. Phase 2B produces one current formal preview. An Apple Log asset may receive the
   unconverted `compress-only` fallback when the Rec.709 preset is unavailable.
3. The user plays or views the formal preview and sees its converted, unconverted, or
   no-transform presentation.
4. Mobile sends the existing authenticated preview-confirmation request with
   `X-MediaVault-Client-Version: 0.3.0`.
5. Backend resolves schema, client, and request-scoped runtime capability, performs the
   existing filesystem-integrity preflight for the formal preview, then starts a write
   transaction that revalidates relational authority and records
   `review_status = preview_confirmed`.
6. With a true runtime snapshot, an eligible asset becomes
   `safe_to_delete_candidate`. With a false runtime snapshot, a newly confirmed
   `not_candidate` remains `not_candidate`.
7. The confirmation response returns the updated asset. Mobile refreshes Asset Detail
   and shows the candidate state.
8. When the local original mapping is available and all existing local safety conditions
   hold, Mobile offers the explicit iPhone-original delete action.
9. Native deletion and local outcome persistence continue through the existing flow.
   Backend candidate state remains unchanged.

### Existing Confirmed Phase 2B Asset

1. The offline Phase 2C migration evaluates each existing Phase 2B session-derived
   video with `review_status = preview_confirmed`.
2. It promotes only assets satisfying the complete current predicate.
3. Ineligible or internally inconsistent rows remain `not_candidate`.
4. A migration summary reports aggregate promoted/skipped counts and stable reason
   counts without filenames, paths, media metadata, tokens, or hashes.

### Phase 1 Direct Asset

1. The asset remains `not_candidate`.
2. The existing `server_hash_recorded`, preview-ready, preview-confirmed, local mapping,
   local outcome, and non-busy checks continue to govern the explicit delete action.
3. Phase 2C capability availability does not affect this Phase 1 path.

## Functional Requirements

### Backend Candidate Evaluator

- Implement one reusable evaluator that returns an eligible/ineligible result and one
  stable internal reason code.
- The evaluator accepts an existing SQLite connection and asset ID so callers use the
  same transaction and database snapshot.
- It derives all authority from persisted relational state. Request payload fields,
  Mobile claims, filenames, `is_log`, UI state, and file paths are not inputs.
- It verifies the complete common-condition and provenance matrices above.
- Chunk completeness is computed from the stated `total_chunks`, inclusive range, and
  expected-size formulas. A count-only check that can accept gaps, wrong ranges, or
  extra indexes is prohibited.
- Hash comparison uses the canonical lowercase 64-hex values already stored by Phase 2A.
  The evaluator does not open the original or derived media files.
- The evaluator returns ineligible for a missing Phase 2B/2C schema, multiple or missing
  authority rows, malformed state, database inconsistency, or an unsupported future
  provenance combination.
- It uses this fixed first-failure order and closed internal reason taxonomy:
  `schema_unavailable`, `asset_not_session_video`, `session_not_completed`,
  `upload_limit_exceeded`, `chunk_limit_exceeded`, `chunk_set_incomplete`,
  `file_identity_mismatch`, `formal_preview_not_ready`,
  `formal_preview_provenance_invalid`, then `preview_not_confirmed`. The evaluator
  returns `upload_limit_exceeded` or `chunk_limit_exceeded` before any chunk-ledger
  aggregation. These values are for safe operator aggregation only and are not a new
  public failure taxonomy.

### Atomic Promotion and Idempotency

- The existing `POST /assets/{asset_id}/preview-confirmation` endpoint remains the only
  user-driven promotion boundary.
- After authentication and before filesystem preflight, confirmation invokes the shared
  rollout resolver. Schema-identity and client-version failures return their defined
  `503` or `409` without media I/O or writes. A successful resolution supplies one
  request-scoped Phase 2B runtime-capability snapshot; runtime probing is not performed
  while the SQLite write transaction is held.
- Before its write transaction, confirmation performs the existing read-only formal
  preview filesystem-integrity preflight. It may verify the immutable derived preview's
  bytes against the immutable result size and SHA-256, but it does not write review or
  candidate state. The preflight snapshot contains the result ID, derived-file ID,
  derived-file relative path, MIME type, size, processed-result SHA-256, asset ID, and
  preview generation.
- After preflight succeeds, confirmation opens `BEGIN IMMEDIATE`, re-reads the asset,
  formal pointer/result/provenance, session, and chunk aggregates, and runs the pure
  relational candidate evaluator without media I/O. It verifies that every field in
  the preflight snapshot still exactly matches the current formal relation.
- Review confirmation, relational candidate evaluation, conditional candidate write,
  and response read occur in that one write transaction. A changed or invalid relation
  after preflight returns the existing applicable `409` and writes neither review nor
  candidate state.
- With a true runtime-capability snapshot, the first eligible confirmation changes:
  - `review_status: not_reviewed -> preview_confirmed`
  - `delete_candidate_status: not_candidate -> safe_to_delete_candidate`
- Repeating confirmation against the same current eligible generation returns `200` and
  the same candidate state without creating or mutating provenance, results, jobs, or
  upload records.
- If the preview is not confirmable, existing Phase 2B `409` behavior remains and neither
  review nor candidate state changes.
- If confirmation is valid but another candidate condition is not met, the review may
  become `preview_confirmed` while the candidate remains `not_candidate`. No client
  override or retry flag can force promotion.
- With a false runtime-capability snapshot, valid confirmation still returns `200` and
  records `review_status = preview_confirmed`, but it never changes
  `not_candidate -> safe_to_delete_candidate`. If relational eligibility is still true,
  an already-safe candidate remains safe because a temporary runtime outage alone is
  not an invalidation; Mobile nevertheless hides Phase 2 deletion. If relational
  eligibility is false, the transaction writes `not_candidate`.
- After runtime capability recovers, reconciliation may promote an eligible,
  already-confirmed `not_candidate`; no second user confirmation is required.
- A database failure rolls back both review and candidate writes. The response must not
  claim that either succeeded.

### Invalidation and Reconciliation

- `delete_candidate_status` is a stored projection of the authoritative predicate, not
  an independently editable business decision.
- The Phase 2C schema adds a closed enum check for `not_candidate` and
  `safe_to_delete_candidate` by rebuilding `assets` with a table-level `CHECK`, plus a
  promotion trigger. The rebuild preserves and recreates every pre-existing column,
  foreign key, index, and trigger, including those introduced through migrations `004`
  through `008`; a trigger-only enum is prohibited. Every direct SQL write to
  `safe_to_delete_candidate` must satisfy the complete eligibility matrix in the same
  database snapshot; all other values are rejected.
- The rebuilt table uses this exact named constraint:
  `CONSTRAINT ck_assets_delete_candidate_status CHECK (delete_candidate_status IN
  ('not_candidate', 'safe_to_delete_candidate'))`. The trusted migration records the
  SHA-256 of the exact rebuilt `assets` entry from `sqlite_master.sql`.
- `prevent_safe_delete_candidate_asset_insert` rejects inserting a new asset directly
  as safe. `enforce_safe_delete_candidate_asset_update` validates the complete matrix
  whenever an UPDATE would retain or enter safe state and enforces same-statement
  demotion for an authority change.
- For an asset linked to a completed upload session, SQLite rejects update or deletion of
  the session's `type`, `size_bytes`, `expected_file_sha256`, `chunk_size_bytes`,
  `original_relative_path`, `asset_id`, or `status`; it also rejects deleting that
  completed session. The exact trigger names are
  `prevent_completed_upload_session_update` and
  `prevent_completed_upload_session_delete`.
- SQLite rejects insert, update, or deletion of an `upload_chunks` row belonging to a
  completed session. This preserves the verified-only ledger that established the final
  original. The exact trigger names are `prevent_completed_upload_chunk_insert`,
  `prevent_completed_upload_chunk_update`, and
  `prevent_completed_upload_chunk_delete`.
- For a `file_verified` asset linked to a completed session, SQLite rejects update or
  deletion of `type`, `original_path`, `size_bytes`, `server_sha256`, or
  `verification_status`, and rejects deletion of the asset. These fields are the
  finalized-original identity, not candidate state. The exact trigger names are
  `prevent_finalized_session_asset_update` and
  `prevent_finalized_session_asset_delete`.
- A statement that changes an asset's preview generation, formal pointer, detection
  identity, preview status, or review status must set
  `delete_candidate_status = not_candidate` in that same statement. A trigger rejects a
  state-changing statement that retains a safe candidate. The formal-preview finalizer
  and migration paths therefore explicitly demote before publishing a new generation.
- Existing ready processed-result identity/SHA-256, formal-attempt, and provenance
  immutability remains in force. Phase 2C additionally rejects UPDATE or DELETE of a
  derived file referenced by the current ready formal result/provenance, and freezes its
  `asset_id`, `kind`, relative `path`, `mime_type`, and `size_bytes`. A direct SQL
  mutation of any other evaluator-owned formal relation must demote the candidate in the
  same statement or be rejected. The promotion trigger is the only database path that
  may set a safe candidate. The exact derived-file trigger names are
  `prevent_current_formal_derived_file_update` and
  `prevent_current_formal_derived_file_delete`.
- These database constraints protect relational identity. They cannot prove that bytes
  outside SQLite were not replaced. Confirmation's pre-transaction filesystem preflight
  verifies those bytes against the immutable result size and SHA-256 before the writer
  revalidates the complete relational snapshot.
- A new formal-preview generation resets review and candidate to `not_reviewed` and
  `not_candidate` in one statement. A later ready generation requires a new explicit
  confirmation.
- Managed-rendition selection, completion, failure, supersession, active managed pointer
  changes, and processed-video save state do not promote or demote a valid candidate.
- Asset list/detail GET operations are read-only and do not repair candidate state.
- Provide an operator reconciliation command that runs under an explicit SQLite write
  transaction, supports dry-run, uses the same evaluator as confirmation, and reports
  aggregate changes and safe reason counts.
- Reconciliation may promote an already confirmed eligible asset or demote an invalid
  candidate. It never changes review, preview, upload, provenance, result, or local
  deletion state.
- When the Phase 2B runtime capability is unavailable, reconciliation may demote a
  relationally invalid candidate but never promotes one. A temporary runtime outage
  alone does not demote a candidate whose persisted relations remain eligible; Mobile
  still hides Phase 2 deletion because the capability is false.

### Persistence and Migration

- Phase 2C is an explicit schema/capability migration after
  `008_apple_log_formal_preview`; it must not be added as an unconditional normal startup
  migration that assumes Phase 2B tables exist.
- Keep its trusted SQL and identity helper under a Phase 2C-specific database module,
  separate from the normal startup migration directory. Record
  `009_safe_delete_candidate` in `schema_migrations` and store the exact SQL SHA-256 in
  Phase 2C schema metadata.
- The trusted SQL creates `phase2c_schema_metadata` with the same version primary-key,
  lowercase 64-hex digest validation, and timestamp pattern as Phase 2B metadata, plus
  required `schema_sql_sha256` and `assets_table_sql_sha256` fields. The sole expected
  row uses version `009_safe_delete_candidate`; both digests must equal repository-owned
  expected values.
- Migration requires:
  - the exact expected Phase 2B migration marker and schema identity;
  - the Phase 2B runtime formal-preview capability to be enabled;
  - the Phase 2B API and worker drain preconditions required for an offline write;
  - no queued/running profile-aware preview work or nonterminal formal attempt;
  - every existing upload session to satisfy the hard 1 TiB size and 8 MiB chunk-size
    bounds;
  - every existing upload session to satisfy `1 <= total_chunks <= 131072`;
  - `008_apple_log_formal_preview` as the latest applied feature migration;
  - none of the closed Phase 2C presence signals, unless this is an idempotent
    already-applied verification of the complete exact `009` identity.
- A Phase 2C signal without the complete expected identity fails migration preflight
  with `phase2c_migration_schema_identity_mismatch` before runtime or drain checks and
  performs no writes.
- If the Phase 2B runtime capability is unavailable, migration returns
  `phase2c_migration_phase2b_runtime_unavailable`. An existing session outside the hard
  size/chunk-size bounds returns `phase2c_migration_upload_limit_exceeded`; one outside
  the chunk-count bound returns `phase2c_migration_chunk_limit_exceeded`. These failures
  expose aggregate counts only, occur before schema writes, and leave schema, markers,
  metadata, and assets unchanged.
- The locked migration order is fixed and runs inside one `BEGIN IMMEDIATE` transaction:
  1. repeat the marker, digest, runtime-capability, drain, upload-bound, chunk-bound, and
     partial-state preflight under the lock;
  2. apply the trusted `009_safe_delete_candidate` SQL, including the complete `assets`
     table rebuild, closed enum, and immutable identity/promotion triggers;
  3. insert the `009_safe_delete_candidate` schema marker and metadata containing the
     exact trusted-SQL and rebuilt-`assets` SQL SHA-256 values;
  4. run backfill through the same evaluator used by confirmation and reconciliation;
  5. run foreign-key and candidate-relation integrity checks; and
  6. commit.
- Backfill therefore evaluates only after the Phase 2C schema marker and metadata are
  visible in its transaction. An ineligible row is counted and retained as
  `not_candidate`; a schema, trigger, integrity-check, or SQL error rolls back schema,
  marker, metadata, and every candidate change.
- Dry-run follows the same six steps, produces the same aggregate reason counts, and
  always rolls back after step 5. It does not use a special evaluator mode or temporarily
  bypass the schema-identity requirement.
- Re-running a successfully applied migration verifies the marker, both metadata
  digests, named constraint, every Phase 2C trigger definition, and full expected object
  set before returning an idempotent `already_applied` result.
- A failed or dry-run migration does not change asset statuses or leave a partial marker.
- A fresh installation follows the same ordered Phase 2B then Phase 2C schema contract;
  it does not bypass the closed Phase 2B detector gate.

### API and Capability Contract

- `GET /api/v1/capabilities` adds the snake-case wire field
  `features.safe_delete_candidate`.
- Mobile sanitizes it to `features.safeDeleteCandidate`.
- The shared rollout resolver distinguishes these states:
  - no Phase 2 state: the pre-Phase 2 minimum/version behavior and
    `safe_delete_candidate = false`;
  - valid Phase 2B state without Phase 2C: the existing Phase 2B minimum/version
    behavior and `safe_delete_candidate = false`;
  - valid Phase 2C marker with an unavailable Phase 2B runtime detector: minimum
    `0.3.0`, `formal_apple_log_preview = false`, and `safe_delete_candidate = false`;
  - valid Phase 2C marker with available Phase 2B runtime capability: minimum `0.3.0`
    and `safe_delete_candidate = true`;
  - present but invalid Phase 2B or Phase 2C identity: fail closed with the applicable
    `503 phase2b_migration_schema_identity_mismatch` or
    `503 phase2c_migration_schema_identity_mismatch` rather than silently downgrading.
- Schema identity errors take precedence over client-version errors. With valid schema
  identities, client-version errors take precedence over runtime capability checks.
- When the runtime flag is false, the Backend does not promote new candidates and
  Mobile does not offer Phase 2 deletion. Preview confirmation may still record
  `preview_confirmed`, but it preserves a relationally valid candidate status and never
  creates a new safe candidate. Phase 1 deletion remains available.
- `minimum_client_version` and endpoint enforcement come from the same resolver. The
  Phase 2C schema rollout floor is `0.3.0` and never lowers merely because a runtime
  detector check becomes unavailable.
- Phase 2B asset preview stream, confirmation, and exact-result delivery reject a
  missing, malformed, or lower client version with `409 incompatible_client` using that
  effective minimum, but only after both managed schema identities have been validated.
- Asset list, Asset Detail, and preview-confirmation responses continue to expose
  `delete_candidate_status` as `not_candidate` or `safe_to_delete_candidate`.
- No new candidate mutation endpoint is added.
- API responses never expose evaluator reason codes, database schema details, upload
  chunk hashes, expected file hashes, paths, or tokens.

### Mobile Contract

- Update the shared client version and product versions to `0.3.0`.
- Capability sanitization rejects missing, malformed, or semantically incompatible
  Phase 2C data and fails closed.
- Asset Detail obtains deletion capability through a read-only capability hook that is not
  conditional on managed-rendition eligibility. It refreshes on screen focus, manual
  refresh, and immediately before deletion confirmation; a refresh failure makes the
  Phase 2 predicate false.
- The common manual-deletion conditions remain:
  - `preview_status = preview_ready`;
  - `review_status = preview_confirmed`;
  - local mapping status is `available`;
  - local deletion outcome is not `deleted`;
  - mapping/outcome loading or deletion is not in progress.
- A Phase 1 direct image or video with `server_hash_recorded` remains eligible from the
  common conditions alone.
- A Phase 2 session-derived video additionally requires:
  - `verification_status = file_verified`;
  - `capabilities.features.formalAppleLogPreview = true`;
  - `capabilities.features.safeDeleteCandidate = true`;
  - `formal_preview.state = ready`;
  - `delete_candidate_status = safe_to_delete_candidate`.
- Mobile never reconstructs the Backend session/chunk/provenance predicate. It only
  sanitizes the status and combines it with capability, formal-preview, and local state.
- Unknown candidate values sanitize to `not_candidate`.
- Asset Detail uses these exact status strings and accessibility labels:
  `safe_to_delete_candidate` is `Ready for explicit iPhone deletion`; `not_candidate`
  and unknown values are `Not ready for iPhone deletion`. Neither label implies that
  deletion already occurred or that an Apple Log fallback is Rec.709-converted.
- A candidate does not bypass the existing explicit confirmation dialog, native Photos
  API, local mapping lookup, terminal in-memory success state, or local outcome storage.

### Failure and Audit Contract

- Safety ambiguity always resolves to `not_candidate`.
- Logs and migration summaries may include asset numeric IDs and stable reason codes.
  They must not include tokens, local asset identifiers, local URIs, host paths,
  filenames, capture metadata, complete hashes, LUT contents, detector evidence, or raw
  SQL rows.
- The pure candidate evaluator, migration backfill, reconciliation, and database triggers
  do not enqueue work, invoke ffmpeg/ffprobe, open original or derived media, contact
  Mobile, or call a Photos API. The existing confirmation filesystem-integrity preflight
  is explicitly outside that evaluator and completes before the write transaction.
- Evaluation and migration remain deterministic for one database snapshot.

## Non-Functional / Technical Notes

- Keep React Native + Expo managed workflow + JavaScript, FastAPI, SQLite, Docker, and
  the immutable-original rule.
- No TypeScript, Expo SDK upgrade, Node version change, or new cloud service is
  introduced.
- Define the Backend hard-bound constants
  `MAX_UPLOAD_SESSION_SIZE_BYTES = 1099511627776`,
  `MAX_UPLOAD_CHUNK_SIZE_BYTES = 8388608`, and
  `MAX_UPLOAD_CHUNKS = 131072`. Settings validation enforces both byte maxima and
  `(upload_session_max_size_bytes + upload_session_chunk_size_bytes - 1) //
  upload_session_chunk_size_bytes <= MAX_UPLOAD_CHUNKS`.
- Upload-session creation rejects `size_bytes` above the configured or hard maximum and
  rejects a calculated `total_chunks` outside `1..MAX_UPLOAD_CHUNKS` before persistence.
- The existing 8 MiB chunk-size and 1 TiB maximum-size defaults remain unchanged.
  Configuration may reduce either value only when the resulting ratio still satisfies
  the bound; neither hard maximum may be increased by configuration.
- Candidate evaluation uses at most four parameterized SQL statements. Its chunk check
  returns aggregate values only, never individual chunk rows, uses the session-indexed
  chunk access path, and retains O(1) Python memory.
- The automated performance fixture contains the Phase 2A maximum of `131072` verified
  chunks (1 TiB at 8 MiB). On the repository test runner, evaluator execution completes
  in under 2 seconds, and `EXPLAIN QUERY PLAN` demonstrates use of the session-indexed
  chunk lookup. Fixture construction occurs before timing starts, and media is never
  loaded for this test.
- SQLite operations use the existing WAL mode, `busy_timeout = 5000ms`, foreign keys,
  and explicit transaction boundaries.
- The evaluator, migration backfill, reconciliation command, database protection, API
  response, capability sanitizer, and Mobile deletion predicate must share one semantic
  eligibility matrix through repository-owned tests.
- Existing project quality gates remain mandatory:
  - `npm run lint`
  - `npm test`
  - `npm run test:coverage`
  - `npx expo install --check`
  - iOS export
  - Backend pytest
  - Docker Compose config
  - `git diff --check`
- Physical-device validation remains a separate release acceptance activity and is not
  counted as automated test coverage.

## Test Requirements

### Backend

- Unit-test every common predicate and allowed/denied provenance row independently.
- Test missing, duplicate, gapped, extra, wrong-range, and wrong-size verified-ledger
  relations. Separately prove that a non-`verified` chunk insert is rejected by the
  existing database constraint.
- Test settings reject a session maximum above 1 TiB, a chunk size above 8 MiB, and a
  max-size/chunk-size ratio above `131072`. Test session creation rejects a media size
  or calculated chunk count above its configured/hard bound and accepts each boundary.
- Test the evaluator returns `upload_limit_exceeded` or `chunk_limit_exceeded` without
  scanning or aggregating the chunk ledger.
- Test session/asset size mismatch and expected/server SHA-256 mismatch.
- Test ordinary, unknown, Apple Log unavailable fallback, and future valid LUT-applied
  formal previews.
- Reject managed identity/test/custom LUT provenance, stale generation, superseded or
  failed results, missing provenance, duplicate authority, and mixed detection fields.
- Test confirmation's filesystem-integrity preflight occurs before `BEGIN IMMEDIATE`, and
  that its transaction performs only relational revalidation before promotion.
- Test a changed formal relation between preflight and `BEGIN IMMEDIATE` returns `409`
  and writes neither review nor candidate state.
- Test each preflight snapshot field, including relative path, MIME type, size,
  processed-result SHA-256, asset ID, and generation. A mismatch before the locked
  recheck returns `409` with no review or candidate write.
- Test confirmation promotion and idempotent repeat in one transaction.
- Test confirmation with a false request-scoped runtime snapshot returns `200`, records
  `preview_confirmed`, and does not change `not_candidate` to
  `safe_to_delete_candidate`. Test that it preserves an already-safe relationally valid
  candidate, while Mobile capability remains false, and that later reconciliation may
  promote the eligible confirmed non-candidate after runtime recovery.
- Inject a failure between review and candidate writes and prove full rollback.
- Test direct SQL cannot force an ineligible candidate, mutate a completed-session or
  verified-chunk identity, or mutate a finalized original identity.
- Test direct SQL UPDATE/DELETE cannot change a current ready formal derived file,
  including its relative path or size, and cannot change ready result identity/SHA-256.
- Test every permitted asset authority change explicitly demotes the candidate in the
  same statement; test that omitting the demotion is rejected.
- Test migration preflight, dry-run, rollback, idempotent repeat, backfill counts, and
  schema-identity mismatch. Prove dry-run executes the post-marker evaluator path and
  rolls back schema, marker, metadata, and candidate changes.
- Test migration refuses a disabled Phase 2B runtime with
  `phase2c_migration_phase2b_runtime_unavailable`, refuses a session outside the hard
  size/chunk-size bounds with `phase2c_migration_upload_limit_exceeded`, and refuses an
  excessive chunk count with `phase2c_migration_chunk_limit_exceeded`; all leave the
  database unchanged and expose aggregate counts only.
- Test the `assets` rebuild preserves every migration `004` through `008` column,
  foreign key, index, and trigger, and rejects values outside the closed candidate enum.
- Test reconciliation promotion, demotion, no-op, dry-run, and safe audit output.
- Test capability false before Phase 2C, true only after valid Phase 2B/2C enablement,
  and effective minimum client version `0.3.0`.
- Test a valid `007_managed_preview_presets` database with no closed Phase 2B presence
  signal returns the normal Phase 2B-disabled capability response, not `503`. Use a
  table-driven partial-state test proving that each listed `008` presence-signal class
  requires the full marker, metadata, schema objects, rewritten-trigger definitions,
  and exact digest.
- Test a valid `008_apple_log_formal_preview` database with none of the closed Phase 2C
  signals returns the normal Phase 2B-enabled/Phase 2C-disabled response, not `503`.
  Prove that the pre-existing `assets.delete_candidate_status` column alone is not a
  Phase 2C signal.
- Use a table-driven Phase 2C partial-state test in which the `009` marker, metadata
  table/row, named `assets` constraint, and each listed exclusive trigger remains alone
  or is missing/tampered. Every case returns
  `503 phase2c_migration_schema_identity_mismatch`; the complete exact set returns the
  Phase 2C response.
- Test that an unavailable runtime detector after a valid Phase 2C marker keeps the
  endpoint minimum at `0.3.0` while both formal-preview and candidate flags are false;
  test the invalid-marker configuration failure separately.
- Test resolver priority: invalid `008` plus an old client returns its `503`, invalid
  `009` plus an old client returns its `503`, and valid identities plus an old client
  returns `409 incompatible_client`. Capability discovery uses the same cases.
- Test reconciliation under runtime unavailability demotes relationally invalid
  candidates, does not promote eligible non-candidates, and does not demote a
  relationally valid candidate solely because the runtime is unavailable.
- Test Phase 2 endpoints reject client `0.2.0` after Phase 2C enablement while asset
  list/detail remain readable.
- Test the `131072`-chunk aggregate path uses at most four SQL statements, O(1) Python
  memory, the session-indexed query plan, and completes within the specified duration.
- Preserve all Phase 1 upload, preview, confirmation, and deletion-support API tests.

### Mobile

- Test capability snake-case to camel-case sanitization and malformed-value rejection.
- Test semantic-version rejection below `0.3.0`.
- Test the dedicated deletion-capability hook refreshes on screen focus, manual refresh,
  and immediately before native confirmation; a failed or changed response hides or
  cancels Phase 2 deletion.
- Test Phase 2 deletion is hidden when the Phase 2C capability is false/missing, status
  is `not_candidate`/unknown, or formal preview is not ready.
- Test both an Apple Log fallback and a future applied-LUT formal preview with
  `safe_to_delete_candidate`.
- Test Phase 1 direct image/video deletion remains eligible without Phase 2C capability
  or candidate status.
- Test managed result, saved processed copy, legacy `is_log`, or active result cannot
  substitute for candidate status.
- Test the exact candidate display/accessibility strings and the existing native
  confirmation/deletion flow.
- Test local mapping missing, already deleted, loading, deleting, permission denial,
  cancellation, and persistence failure paths remain fail-closed.
- Keep canonical Jest coverage floors without adding exclusions.

## Acceptance Criteria

- With a true request-scoped runtime snapshot, an eligible session-derived video becomes
  `safe_to_delete_candidate` in the same committed transaction that confirms its current
  formal preview.
- Confirmation's existing filesystem-integrity preflight completes before its write
  transaction; the transaction revalidates only relational authority and cannot promote
  after a changed preflight relation.
- The confirmation response and subsequent list/detail responses expose the promoted
  state.
- The exact completed session, required verified chunks, size, expected/server SHA-256,
  `file_verified` asset, current formal generation, ready result/attempt, immutable
  provenance, `preview_ready`, and `preview_confirmed` are all required.
- Apple Log `compress-only` fallback with
  `none / unavailable / lut_preset_unavailable` is eligible and remains visibly
  unconverted.
- A future valid Apple Log LUT result with complete `lut / applied` provenance is
  eligible without changing this feature.
- Ordinary and unknown formal `compress-only` previews are eligible only with the exact
  `none / not_requested` provenance.
- Managed, stale, failed, superseded, missing, duplicated, or internally inconsistent
  results never create a candidate.
- A new formal-preview generation or any other authority invalidation cannot leave
  `safe_to_delete_candidate` persisted.
- Completed-session/chunk and finalized-original identity fields are immutable at the
  SQLite boundary, including against direct SQL.
- Current ready formal derived-file path, MIME type, size, asset/kind identity, and
  processed-result identity/SHA-256 are immutable at the SQLite boundary. Confirmation
  binds the filesystem preflight to that exact relational snapshot before promotion.
- Existing eligible confirmed Phase 2B assets are backfilled atomically; migration
  runs only with the Phase 2B runtime capability enabled, and failure leaves schema and
  data unchanged. Dry-run follows the same post-marker evaluator path and rolls back
  every schema and data write.
- Reconciliation uses the same predicate and can safely correct candidate projection
  drift without changing review or media state.
- Phase 1 direct assets remain `not_candidate`, and their existing explicit manual
  deletion flow still works.
- Mobile Phase 2 deletion requires both compatible capabilities, ready formal preview,
  Backend candidate, and all existing local conditions.
- A valid Phase 2C marker never lowers the `0.3.0` endpoint version floor; an invalid
  Phase 2B or Phase 2C identity produces its stable fail-closed configuration error
  before client-version or runtime evaluation.
- A valid database through `007_managed_preview_presets` alone is Phase 2B-disabled,
  not schema-invalid. Only the closed `008` presence signals trigger full Phase 2B
  identity validation and partial-state `503`.
- A valid `008_apple_log_formal_preview` database with only the original
  `delete_candidate_status` column is Phase 2C-disabled, not schema-invalid. Any closed
  `009` presence signal requires the complete exact marker, metadata digests, named
  constraint, and trigger definitions or returns the stable Phase 2C `503`.
- Runtime unavailability never creates a new safe candidate through confirmation:
  confirmation may record `preview_confirmed`, preserves a relationally valid existing
  candidate, and leaves a new candidate at `not_candidate` until runtime recovery and
  reconciliation.
- Settings, new sessions, migration preflight, and the evaluator enforce the same 1 TiB
  size, 8 MiB chunk-size, and `131072` chunk-count hard maxima.
- The `0.3.0` app refreshes deletion capability before display and confirmation. An
  operator retires/restarts every `0.2.0` app before production enablement, addressing
  the unavoidable in-memory legacy-client residual risk.
- No operation automatically deletes an iPhone or Backend file.
- The pure candidate evaluator, migration, reconciliation, and database protection do
  not read media, run a codec process, or expose sensitive paths, hashes, detector
  evidence, local identifiers, or tokens.
- Application/client versions are consistently `0.3.0`; automated Mobile, Backend,
  migration, coverage, Expo, iOS export, Compose, and diff checks pass.

## Required Operator Input

- Phase 2C adds no new media, LUT, licensing, or detector input.
- Production enablement inherits the Phase 2B requirement for repository-owner-approved
  detector rules, controlled external user-owned Apple Log and ordinary recordings,
  successful certification, and the Phase 2B offline migration.
- The Phase 2C migration must be run only while the validated Phase 2B runtime formal
  preview capability is enabled. Otherwise it exits unchanged with
  `phase2c_migration_phase2b_runtime_unavailable`.
- Before Phase 2C production migration, the operator distributes Mobile `0.3.0` and
  verifies that every `0.2.0` client has been restarted or retired. This is necessary
  because a previously loaded native screen cannot be remotely prevented from executing
  its legacy local-only deletion action.
- The Phase 2C code and automated tests may be completed while that operator input is
  unavailable. Capability remains false and no production candidate is promoted until
  both Phase 2B and Phase 2C gates validate.
- Physical-device validation is deferred to the consolidated Phase 1 through Phase 2C
  Development Build release check.

## Open Questions

- None for Phase 2C. Apple Log to Rec.709 generation, source/license evidence, color
  quality criteria, and preset enablement remain a separate future feature.

## Durable Docs Impact

- Update required during implementation:
  - `docs/product-requirements.md`: mark Phase 2C as implemented and document the
    versioned rollout and Phase 2 deletion requirement.
  - `docs/functional-design.md`: add the exact evaluator, transition, invalidation,
    capability, migration, and Mobile flow.
  - `docs/architecture.md`: add the candidate authority boundary, transaction ownership,
    schema protection, and offline migration order.
  - `docs/repository-structure.md`: add the final evaluator, migration, reconciliation,
    capability, Mobile UI/service, and test locations.
  - `docs/development-guidelines.md`: add Phase 2C migration/reconciliation and validation
    commands plus safe logging rules.
  - `docs/glossary.md`: change `safe delete candidate` from future terminology to the
    active Phase 2C definition and distinguish it from local deletion outcome.
- `docs/ideas/initial-requirements.md` is updated in this define-feature step to remove
  the conflicting permission for a user-selected LUT to act as `not_log`/`unknown`
  formal-preview authority. Managed LUT renditions remain outside candidate authority.
- No durable document is updated by this `define-feature` step because the existing
  product direction is unchanged. The concrete implementation paths, schema identity,
  command names, and rollout state must be recorded after `plan-feature` and
  implementation settle them.
