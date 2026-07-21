# Feature Spec

## Metadata

- Date: 2026-07-18
- Feature name: Processed video delivery and iPhone library save
- Status: implemented (physical device validation deferred)
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/repository-structure.md`
  - `docs/ideas/20260711_3-resumable-original-finalization.md` (Phase 2A prerequisite)
  - `docs/ideas/20260718_2-managed-preview-presets.md` (subsequent LUT feature)
  - `docs/ideas/20260711_2-apple-log-preview.md` (subsequent Apple Log feature)
  - `docs/ideas/iphone_applelog_app_distribution_and_lut_policy.md`

## Background

Phase 2A can retain a verified immutable video original and generate a lightweight
preview for eligible assets, but it does not provide an explicit flow to download that
derived video to an iPhone and save it to the photo library. The product policy requires
the first complete Mac mini loop to be:

1. retain and verify the original;
2. produce a lightweight derived video without changing the original; and
3. explicitly return and save that derived video on the iPhone.

This feature establishes that delivery loop before adding LUT selection or Apple Log
classification. It must not weaken the existing LOG safety gate, expose an unverified
file, or silently substitute a newly active result while the user is saving another one.

## Target Users / Use Cases

- A user who has uploaded a verified video and wants to keep a smaller H.264/AAC copy
  in the iPhone photo library.
- A user who needs to retry a failed result download without re-uploading the original
  or creating another asset.
- A user who wants to distinguish saving a processed copy from confirming or deleting
  the source original.

## Scope

- Introduce an immutable Backend `processed_result` record for every deliverable video
  result. It has an opaque `result_id`, `asset_id`, `derived_file_id`, MIME type,
  `size_bytes`, `sha256`, nullable `preview_generation`, creation time, and ready/failed
  state. Phase 2A results have no generation; the Phase 2B migration assigns generation
  `0` before profile-aware results use a required generation. A result always refers to
  one immutable derived file and never to an original path.
- Store these records in `processed_results`. Its `asset_id` and `derived_file_id` are
  foreign keys, and one derived file has at most one processed result. SQLite constraints
  and same-asset triggers prevent a result from referring to a derived file owned by a
  different asset or an inactive/failed result from becoming active.
- Add nullable `assets.active_processed_result_id`. A result becomes active only after
  the final derived file exists, its size and SHA-256 are committed, and all applicable
  readiness checks pass. Creating the result, recording its digest, and changing the
  active pointer occur in one transaction.
- On rollout, backfill an active result only for an existing eligible Phase 2A normal
  video preview after validating its file and computing its result SHA-256. Missing,
  failed, or legacy LOG safety-gated previews do not receive a result record.
- Return result metadata from asset detail as an `active_processed_result` object with
  immutable `result_id`, MIME type, size, SHA-256, creation time, and an authenticated
  URL that includes that exact result ID. It exposes no filesystem path.
- Add an authenticated download endpoint for a named result:
  `GET /assets/{asset_id}/results/{result_id}`. It serves only the exact active result
  specified by both IDs; it never resolves an unspecified request to whichever result is
  active at response time.
- Add a deliberate Mobile action to download that exact result to an app-managed
  temporary file, verify its response identity, size, and SHA-256, then save it to the
  iPhone photo library with `expo-media-library`.
- Store Mobile save state in a new `processedResultSaveStore`, separate from the existing
  source-original `localAssetMappingStore`. A processed result save record contains at
  least `backend_result_id`, `backend_asset_id`, result SHA-256,
  `saved_local_asset_identifier`, `save_status`, and timestamps. Original-deletion code
  must not read this store, and result-saving code must not write source-delete mapping.
- Make retry explicit. The initial release may restart a failed result download from byte
  zero, but it must not save a partial file or automatically repeat an unknown
  photo-library operation.
- Preserve the current Phase 1 and Phase 2A LOG safety behavior. This feature does not
  create a result for a legacy `lut_preview` failure. The later Apple Log feature expands
  eligibility through formal provenance.
- Keep the current manually configured Backend URL and SecureStore token contract. This
  feature does not introduce QR import, cloud deployment, or a fixed Tailscale URL.

## Out of Scope

- Apple Log detection, Apple Log to Rec.709 conversion, LUT manifests, preset selection,
  or custom LUT upload.
- Changing preview generation, confirmation, safe-delete eligibility, or the rule that
  an iPhone original is deleted only after an explicit user action.
- Background or resumable result downloads, batch save, automatic photo-library save, or
  automatic cleanup of Backend originals.
- An unauthenticated public endpoint, App Review cloud environment, or API version
  migration.
- Downloading the immutable original to the iPhone from this flow.

## User Flow

1. Phase 2A finalizes a video original with `verification_status = file_verified`.
2. The worker completes an eligible lightweight derived video. The Backend validates it,
   creates its immutable result record, and atomically makes that result active.
3. Asset Detail reads `active_processed_result` and renders a save command bound to its
   exact `result_id`, digest, size, and authenticated URL. Failed or non-ready assets
   show no save command.
4. The user explicitly chooses to save that result. Mobile writes a `downloading` record
   in `processedResultSaveStore` and downloads only the specified result URL to a
   temporary file.
5. Mobile verifies that response headers still name the expected `result_id`, size, and
   SHA-256, then computes the temporary file's SHA-256 and compares it with the expected
   digest.
6. Only after integrity verification succeeds does Mobile request photo-library access
   and create a photo-library asset from the temporary file.
7. Mobile records `saved` with the new local photo-library asset identifier, removes the
   temporary file, and updates the UI. It never alters the source-original mapping.
8. A superseded result, network failure, digest mismatch, permission denial, or
   library-save failure leaves the Backend asset and iPhone original unchanged. Mobile
   cleans up the temporary file and offers an explicit refresh or retry without
   re-uploading the source video.

## Functional Requirements

### Result Identity, Active Pointer, and Eligibility

- `processed_result.result_id` is immutable and opaque. `derived_file_id`, MIME type,
  size, SHA-256, and any assigned generation cannot be changed after the record becomes
  ready. A new render or preset always creates a new result record and derived file.
- `processed_results` enforces `FOREIGN KEY(asset_id) REFERENCES assets(id)`,
  `FOREIGN KEY(derived_file_id) REFERENCES derived_files(id)`, and
  `UNIQUE(derived_file_id)`. An insert or update is rejected unless
  `processed_results.asset_id = derived_files.asset_id` and the referenced derived file
  is a ready video file.
- `assets.active_processed_result_id` identifies at most one deliverable result. It is
  updated only in the same transaction that commits the ready result's digest and active
  derived-file relation. A deferred foreign key plus SQLite trigger rejects an active
  pointer unless it names a ready result of that same asset. Historical results are audit
  records, not active downloads.
- Asset detail returns the active result's immutable metadata and a URL for that result.
  Mobile captures all four identity values (`result_id`, `sha256`, `size_bytes`, URL)
  before beginning a download; it does not call an active-result resolver later.
- One shared Backend eligibility service is used by asset detail and download endpoints.
  It applies the following rules:

| Runtime phase | Deliverable result requirements |
| --- | --- |
| Phase 2A | `verification_status = file_verified`; `preview_status = preview_ready`; `active_processed_result_id` refers to a ready video derived file with matching stored size/SHA-256; the asset is not in the legacy LOG safety-gated state; and storage validation succeeds. |
| Phase 2B and later | All Phase 2A checks, plus `processed_result.derived_file_id = assets.formal_preview_id`, `processed_result.preview_generation = assets.preview_generation`, and a matching formal provenance record that passes the Apple Log feature's stream/confirmation validation. |

- An absent active result, failed result, missing storage file, digest/size mismatch,
  stale generation, `formal_preview_id` mismatch, or missing/invalid provenance returns
  `409 processed_result_not_ready`. It never falls back to an old derived file or the
  original.

### HTTP Download Contract

- `GET /assets/{asset_id}/results/{result_id}` requires the same bearer token as existing
  asset and preview endpoints. It queries by both `asset_id` and `result_id`; an unknown
  asset, unknown result, or result owned by another asset returns `404`.
- The endpoint verifies that `result_id` is still the asset's active result before it
  opens the file. If the result exists but the active pointer changed, it returns
  `409 processed_result_superseded`; it never substitutes the newer active result.
- A full successful response is `200` and includes `Content-Length` for the full file,
  `Accept-Ranges: bytes`, `Content-Type`, a sanitized `Content-Disposition: attachment`
  filename, `ETag`, `X-Processed-Result-Id`, `X-Processed-Result-SHA256`, and
  `X-Processed-Result-Size`.
- A valid single-range request is `206`. It includes the same identity headers,
  `Content-Length` for the returned segment, and
  `Content-Range: bytes {start}-{end}/{total}`. Multiple ranges are unsupported.
- A malformed or unsatisfiable range returns `416`,
  `Content-Range: bytes */{total}`, and the stable error code
  `processed_result_range_not_satisfiable`. The API does not stream bytes for that
  response.
- A result URL, attachment filename, headers, API error body, and routine logs must not
  contain a bearer token, local URI, original path, storage path, or full media metadata.

### Mobile Download and Save Contract

- The save command is visible only for the result identity returned by asset detail. It
  is an explicit user command and never runs as part of upload completion, preview
  confirmation, or iPhone original deletion.
- Mobile downloads into an application-controlled temporary location using an Expo SDK
  54-compatible streaming-to-file service. It must not load the complete video into
  JavaScript memory. It rejects a response whose result identity headers, full size, or
  final SHA-256 differ from the captured asset-detail metadata.
- `processedResultSaveStore` is a separate namespaced local store. It may contain
  `downloading`, `saved`, `failed`, or `unknown` save status, but it is never queried by
  original deletion. `localAssetMappingStore` remains the sole source for the selected
  source-original deletion identity.
- Mobile requests photo-library permission only when the user initiates saving. On
  denial, restriction, cancellation, insufficient device storage, download failure, or
  `MediaLibrary` failure, it records a non-success state and does not create a Backend
  job or alter `review_status`.
- After a successful save, Mobile stores `saved_local_asset_identifier` together with the
  exact result ID and digest. A later active result is independently saveable.
- On startup and after a terminal failure, Mobile removes stale temporary downloads. A
  process interruption after invoking the photo-library save but before its result is
  known becomes `unknown`; it does not claim success or auto-save again.

### Safety and State Separation

- Saving a processed result is independent from `preview_confirmed` and
  `safe_to_delete_candidate`. It does not by itself permit deletion of the iPhone
  original.
- The Backend permits result playback/download only through the shared eligibility
  service. Phase 2B result delivery therefore has the same formal-provenance gate as
  preview streaming and confirmation.
- No endpoint in this feature reads, serves, or deletes an original for delivery.

## Non-Functional / Technical Notes

- Keep React Native, Expo managed workflow, JavaScript, FastAPI, SQLite, ffmpeg, and
  immutable-original storage.
- Saving requires a Development Build because `expo-media-library` uses native iOS
  photo-library APIs. Expo Go is not the validation target for this flow.
- Hashing a result uses the existing native streaming hash service in bounded memory.
- Initial Mobile download retry starts from byte zero. The range contract is required for
  media interoperability and a later resumable-download feature, not for silently
  resuming an incomplete initial save.
- Tests cover a normal ready result; active-result change before download; active-result
  change during retry; result-header mismatch; missing file; server/result digest
  mismatch; `200`, `206`, and `416` responses; permission denial; save cancellation;
  unknown save outcome; startup cleanup; and proof that the result-save store cannot be
  used by source-original deletion.

## Acceptance Criteria

- A `file_verified` Phase 2A video with an eligible ready derived video has exactly one
  active immutable result containing its derived-file ID, MIME type, size, and SHA-256.
  Phase 2B results also contain the matching required generation. Asset detail returns
  the same identity in its download metadata.
- Database tests prove that a result cannot reference another asset's derived file, one
  derived file cannot have two result records, and an asset cannot point at another
  asset's, failed, or inactive result. Endpoint tests prove that an asset/result ID
  mismatch returns `404` without streaming bytes.
- The result download endpoint returns only the requested active `result_id`; after an
  active-pointer change, the old URL returns `409 processed_result_superseded` and never
  returns the new result's bytes.
- `200`, valid `206`, and `416` responses follow the specified identity and range
  headers. Mobile rejects an identity, size, or digest mismatch before photo-library
  save.
- An asset without an eligible active result, including a Phase 2B provenance mismatch,
  returns `processed_result_not_ready` and shows no Mobile save command.
- `processedResultSaveStore` records a successful local library asset only under the
  exact result ID and digest, and source-original deletion code cannot read that record.
- Permission denial, download interruption, digest mismatch, supersession, and
  library-save failure leave the Backend asset and iPhone original unchanged and do not
  claim that a result was saved.
- A legacy LOG asset that remains in the current failed safety state is not exposed by
  the result endpoint. This feature does not claim Apple Log or Rec.709 behavior.

## Open Questions

- Whether a later release should resume partial result downloads rather than restart
  them after a network interruption.
- The user-facing filename policy for saved results, including duplicate names in the
  photo library.
- The derived-file and historical-result retention period after a result has been saved.
  This is separate from immutable-original retention and must not be decided by Mobile.

## Durable Docs Impact

- Update candidates: `product-requirements.md`, `functional-design.md`,
  `architecture.md`, `development-guidelines.md`, `glossary.md`, and
  `repository-structure.md`.
- Update timing: after this feature specification is reviewed and confirmed.
- Reason: returning and saving an identified, provenance-gated processed derived video
  is a stable user workflow that is not represented by the current preview-only durable
  documentation.
