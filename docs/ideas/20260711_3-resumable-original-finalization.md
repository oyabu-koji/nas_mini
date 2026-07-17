# Feature Spec

## Metadata

- Date: 2026-07-11
- Feature name: Resumable video upload and verified original finalization
- Status: confirmed
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/repository-structure.md`
  - `docs/ideas/20260711_2-apple-log-preview.md` (dependent Phase 2B feature)

## Background

Phase 1 accepts only files up to `104857600 bytes` through one multipart request and
records a server-side SHA-256. It cannot safely resume a large ProRes transfer or
prove that the iPhone-selected file and the stored original are identical. Phase 2A
creates the durable finalization boundary required by Phase 2B Apple Log preview and
Phase 2C safe deletion candidates.

## Target Users / Use Cases

- iPhone users who back up large video originals over LAN or Tailscale and need to
  resume after an interruption without restarting the transfer.
- Users who need the application to distinguish an uploaded chunk from a verified,
  finalized original before any preview is generated.
- Phase 2B Apple Log processing, which must consume only a verified finalized video.

## Scope

- Route every newly selected video through a resumable upload session. Existing Phase 1
  direct upload remains only for images; legacy video assets are not retroactively
  verified.
- At the Phase 2A cutover, `POST /assets/upload` rejects `type = video` with `409`
  and the stable error code `video_session_required`. It creates neither an asset nor
  a job for the rejected request.
- Create authenticated upload sessions that persist immutable upload metadata,
  including a Mobile-generated `client_upload_id`, total byte size, media type,
  original filename, capture metadata, legacy `is_log` hint, and
  `expected_file_sha256`.
- Add the in-repository Expo Module `streaming-sha256`. It calculates whole-file and
  byte-range SHA-256 natively in bounded memory for iOS and Android Development Builds.
- Upload numbered chunks to a session-scoped temporary location and verify each chunk
  against its supplied SHA-256 before it becomes `verified`.
- Provide session status and missing or failed chunk information so Mobile can resume
  after an app restart, a request timeout, or a network interruption.
- Finalize asynchronously through a durable `upload_finalize` job only after every
  required chunk is verified. The worker assembles chunks, compares the completed
  SHA-256 with `expected_file_sha256`, promotes the original, creates the asset with
  `verification_status = file_verified`, and registers the initial preview job.
- Make session creation, same-content chunk re-upload, and finalization idempotent.
  Reject conflicting bytes or hashes for an already verified chunk index rather than
  mixing data from different files.
- Keep the current LOG safety behavior during Phase 2A. Apple Log classification, LUT
  conversion, and formal preview provenance belong to Phase 2B.
- Persist only the Mobile resume data needed for the same local video: local asset ID,
  session ID, `client_upload_id`, byte size, and expected SHA-256. Backend records do
  not store API tokens, local URIs, or host paths.
- Phase 2A accepts only videos with a non-null ImagePicker `assetId`. A video without a
  MediaLibrary identifier is rejected before hashing or session creation with the local
  error code `resumable_video_requires_library_asset`; Phase 2A does not create a cache
  copy as an alternate identity.

## Out of Scope

- Apple Log detection, LUT manifests, LUT conversion, preview provenance, or a LUT
  registry.
- `safe_to_delete_candidate`, automatic iPhone deletion, or Backend original deletion.
- Background synchronization, Wi-Fi or charging scheduling, and multi-file batch
  orchestration.
- Retroactively upgrading Phase 1 direct-upload assets to `file_verified`.
- Resumable image upload, arbitrary client-supplied storage paths, or public endpoint
  access.

## Transition Policy

| Phase | Input | Backend behavior | Preview result |
| --- | --- | --- | --- |
| Phase 2A | `POST /assets/upload` image | Keep Phase 1 direct upload. | Existing `preview` job. |
| Phase 2A | `POST /assets/upload` video | Reject with `409 video_session_required`; no asset/job. | None. |
| Phase 2A | Finalized session video, `is_log = false` | Queue one `preview` job after `file_verified`. | Normal preview. |
| Phase 2A | Finalized session video, `is_log = true` | Queue one `lut_preview` job after `file_verified`; the existing safety gate terminally fails it. | `preview_status = failed`. |
| Phase 2B | Finalized session video | Queue one profile-aware `preview` job with `profile_detection_required = true`. Do not create new `lut_preview` jobs. | `not_log`/`unknown`: `compress-only` preview; `apple_log`: managed LUT preview when enabled, otherwise visibly unconverted `compress-only` preview. |

Historical `lut_preview` jobs and identity-LUT files remain audit-only. They are never
reclassified as formal Phase 2B previews.

## User Flow

1. The user selects a video in Asset Picker. Mobile requires a non-null MediaLibrary
   asset ID, resolves it through `MediaLibrary.getAssetInfoAsync(assetId, {
   shouldDownloadFromNetwork: true })`, and stops before session creation when the
   download or local URI resolution fails.
2. Mobile calculates the resolved file's whole-file SHA-256 using `streaming-sha256`.
3. Before creating a session, Mobile creates and persists a UUID `client_upload_id`
   together with the local asset ID, size, and expected SHA-256.
4. Mobile creates or recovers the upload session using that same idempotency key, then
   receives the server-approved chunk size, expiration, and missing chunk indexes.
5. Mobile calculates each byte-range SHA-256 natively and uploads only missing chunks.
   Backend writes a chunk to a session-scoped temporary path and marks it `verified`
   only after the stored bytes match the supplied hash.
6. After all chunks are verified, Mobile requests finalization. The request returns the
   existing finalization job while it is assembling and the same asset result after it
   completes; it never creates a duplicate asset or preview job.
7. The finalization worker assembles the chunks, verifies the completed SHA-256,
   promotes the original, commits the asset/session/job state, and only then queues the
   initial preview job allowed by the transition policy.
8. After an app restart, Mobile resolves the saved local asset ID with
   `shouldDownloadFromNetwork: true`, validates size, and recomputes its whole-file
   SHA-256 before resuming the saved session. A mismatch removes only the obsolete
   resume record, enters a terminal state, and requires the user to explicitly start a
   new upload with a new `client_upload_id` and session. Resolution failure keeps the
   existing session paused and shows media-unavailable state; it does not create another
   session.
9. Mobile obtains terminal or retryable failure details from session status. It offers
   explicit resume/finalization retry only for retryable failures and never deletes the
   iPhone original.
10. For `cancelled` or `expired`, Mobile treats the session as terminal, removes its
    session-specific resume record, and offers an explicit new upload flow with a new
    idempotency key. It clearly distinguishes this from deleting the iPhone original.

## Functional Requirements

### HTTP Contract

All endpoints require `Authorization: Bearer <token>`.

| Endpoint | Request | Success response | Required failure behavior |
| --- | --- | --- | --- |
| `POST /upload-sessions` | JSON metadata, `client_upload_id`, `size_bytes`, `expected_file_sha256` | `201` for a new session, `200` for the same idempotency key; both return session ID, fixed chunk size, total chunks, status, expiry, and missing indexes. | `409` when the key is reused with different immutable metadata; `422` for invalid metadata/hash; `413` for configured size limit. |
| `GET /upload-sessions/{session_id}` | None | `200` with state, retryability, failure class, missing/failed indexes, and completed asset/job IDs when available. | `404` for unknown session; `410` for expired session. |
| `PUT /upload-sessions/{session_id}/chunks/{chunk_index}` | Raw bytes, `Content-Range`, `X-Chunk-SHA256` | `201` when newly verified; `200` when the exact verified chunk is repeated. | `409` for a conflicting range/hash or an assembling/completed session; `422` for malformed range/hash. |
| `POST /upload-sessions/{session_id}/finalize` | None | `202` with the unique finalization job while assembling or after a retryable failure; `200` with the existing asset and preview job after completion. | `409` until all chunks verify or for terminal failure; `410` after expiry. |
| `DELETE /upload-sessions/{session_id}` | None | `204` after marking the unfinished session cancelled and scheduling temp cleanup. | `409` for assembling/completed sessions. |

- The server uses a fixed `8388608` byte (8 MiB) chunk size. `Content-Range` for index
  `i` must be exactly `i * chunk_size` through `min(size_bytes - 1, ((i + 1) *
  chunk_size) - 1)`.
- Session creation has `UNIQUE(client_upload_id)`. Mobile retries creation after a
  timeout with the same key, so an acknowledged-but-unreceived response is recoverable.
- Chunk upload is sequential per session. The server permits at most two active sessions
  for the configured token and rejects more with `429` and `Retry-After`.

### Persistence Contract

`upload_sessions` stores at least:

- opaque `id`, `client_upload_id` (unique), immutable metadata, `size_bytes`,
  `expected_file_sha256`, fixed `chunk_size_bytes`, reserved `original_relative_path`,
  status, `failure_class`, `retryable`, `expires_at`, `finalization_job_id` (unique),
  `asset_id` (unique after completion), `finalization_attempt_count`, timestamps, and
  finalization lease metadata.

`upload_chunks` stores at least:

- `session_id`, `chunk_index`, start/end byte offsets, byte size, verified SHA-256,
  status, and timestamps, with `UNIQUE(session_id, chunk_index)`.

The database also enforces unique `assets.original_path` and non-null `jobs.dedup_key`.
`upload_finalize` uses `finalize:{session_id}` as its dedup key and has one job per
session. The initial preview uses `initial-preview:{asset_id}`. The asset is created only
after a completed-file hash match and is linked to the completed session.

The Phase 2A database migration adds nullable `jobs.dedup_key`, backfills every existing
job with `legacy:{job_id}`, creates a unique index, then requires a non-null dedup key
for every new job. This preserves historical jobs while allowing finalization and preview
migrations to use conflict-safe insertion.

### Finalization, Recovery, and Failure Contract

- `POST .../finalize` atomically changes an eligible session to `assembling` and creates
  or returns its unique `upload_finalize` job. A worker lease owns assembly; expired
  leases are reclaimed using the existing job-lease mechanism.
- A retryable `failed` session with all required verified chunks accepts the same
  `POST .../finalize`: in one transaction it changes `failed -> assembling`, increments
  `finalization_attempt_count`, clears the retryable failure state, and changes only its
  existing `upload_finalize` job from `failed` to `queued`. It returns `202`; it never
  inserts a second finalization job.
- The worker assembles into a deterministic session finalization path under `tmp/`,
  computes the completed SHA-256, and promotes the file only after the expected hash
  matches. Final and temporary paths are derived only from session identifiers and
  configured roots.
- In one SQLite transaction after the promoted file is known valid, the worker inserts
  the asset, records `server_sha256`, sets `verification_status = file_verified`, links
  and completes the session, marks the finalization job done, and inserts at most one
  initial preview job.
- On lease recovery, if a matching promoted final file exists but `asset_id` is absent,
  the worker verifies its hash and completes the same transaction. If it is absent, the
  worker rebuilds from verified chunks. A mismatched final file is removed and rebuilt
  only from verified chunks.
- `completed_hash_mismatch`, `chunk_conflict`, `cancelled`, and `expired` are terminal.
  `storage_unavailable`, `capacity_unavailable`, `database_transient`, and
  `worker_interrupted` are retryable only while the session has not expired and all
  required chunks remain verified.
- Only the explicit retryable-finalization transition may requeue a failed
  `upload_finalize` job. Generic workers never retry terminal finalization failures.
- A client timeout after database commit is recovered through `GET /upload-sessions`;
  it returns the completed asset and job instead of accepting another finalization.
- A database failure after promotion never creates a `file_verified` asset. Recovery
  either completes the single reserved session transaction from the hash-verified final
  file or removes the unreferenced final file and reports a residual-file warning.

### Mobile Hashing and Resume Behavior

- `streaming-sha256` is an in-repository Expo Module used only in a Development Build.
  iOS reads the selected file in 1 MiB native blocks and updates SHA-256 with
  CommonCrypto; Android uses `ContentResolver` or file input streams and `MessageDigest`
  in the same bounded block size. It exposes whole-file and byte-range digest methods.
- Mobile must not derive the completed-file SHA-256 by hashing chunk digests, and it
  must not load a video into JavaScript memory. `expo-crypto` may create random UUIDs
  but is not the streaming file-hash implementation.
- Mobile sends each fixed byte range as a raw PUT body using Expo SDK 54 `File.slice()`
  and `expo/fetch`. The hash module supplies the same range SHA-256 but does not own
  network transport.
- Expo SDK 54 exposes file streams, while `expo-crypto` digests a supplied value and
  exposes no incremental hash context. The native module is therefore the required
  implementation: https://docs.expo.dev/versions/v54.0.0/sdk/filesystem/ and
  https://docs.expo.dev/versions/v54.0.0/sdk/crypto/
- Session state contains `local_asset_identifier`, `client_upload_id`, `session_id`,
  `size_bytes`, expected SHA-256, and progress only. API tokens remain in SecureStore;
  local URIs are resolved on demand and are not persisted in Backend records.
- A cancelled or expired session starts over with a new idempotency key. A retryable
  failure keeps its key and session until its expiry.
- Mobile presents cancellation and expiry as terminal session states, never as a failed
  deletion or a retryable transfer. It removes only the saved session-resume record and
  requires the user to start a new upload explicitly; the iPhone original remains
  untouched.
- The current Expo contract allows ImagePicker `assetId` to be null, and MediaLibrary
  returns `localUri` through `getAssetInfoAsync`; iCloud download is requested with
  `shouldDownloadFromNetwork: true`. Phase 2A rejects the null-ID case rather than
  inventing an unstable resume identity: https://docs.expo.dev/versions/v54.0.0/sdk/imagepicker/
  and https://docs.expo.dev/versions/v54.0.0/sdk/media-library/

## Non-Functional / Technical Notes

- Keep React Native + Expo managed workflow, JavaScript, FastAPI, SQLite, and the
  immutable-original rule. The custom Expo module requires a Development Build and is
  not supported in Expo Go.
- The initial operational limits are an 8 MiB chunk, two active sessions per token, a
  1 TiB maximum session size, and a seven-day inactivity expiry. They are Backend
  configuration values and may be reduced for a deployment, never bypassed by Mobile.
- Expired and cancelled sessions delete only session-scoped temporary chunks and
  assembly files. They never delete a finalized original, derived file, or asset.
- Chunk writes, assembly, hash verification, and final-path promotion must tolerate
  process interruption without serving partial data. Session recovery must be safe with
  SQLite WAL and the configured busy timeout.
- Error messages and logs must not include tokens, local URIs, server paths, or full
  media metadata.

## Acceptance Criteria

- Direct video upload to `/assets/upload` returns `409 video_session_required` and
  creates no asset/job; direct image upload preserves the Phase 1 behavior.
- A Development Build uses `streaming-sha256` to hash a photo-library ProRes video in
  bounded native memory, and its whole-file digest equals the Backend-computed digest.
- A video larger than `104857600 bytes` is accepted through a session and can complete
  without the Phase 1 multipart limit.
- Retrying session creation after a lost response with the same `client_upload_id`
  returns the same session. Restarting Mobile resolves the same local asset, rechecks
  its SHA-256, and resumes without a duplicate session or asset.
- A null ImagePicker `assetId`, failed iCloud download, or missing `localUri` creates no
  session and presents a media-unavailable/reselect flow. An existing session remains
  paused until its same MediaLibrary asset can be resolved or expires.
- Correct chunks become `verified`; malformed ranges, hash mismatches, and conflicting
  re-uploads cannot contribute to finalization.
- A correct completed-file hash produces one original, one `file_verified` asset, one
  completed session, and at most one initial preview job.
- A completed-file hash mismatch produces no asset, preview, or preview job. Repeating
  finalization, racing finalization requests, a worker kill during assembly, a database
  failure after promotion, and a timeout after commit all recover without duplicates or
  exposing partial data.
- A retryable finalization failure requeues the same finalization job and increments its
  attempt count; terminal finalization failures return `409` and cannot be requeued.
- Session authorization, maximum size, active-session limit, expiry, cancellation, and
  SSD/capacity failures return defined states and do not expose server paths.
- Backend integration tests cover the full HTTP/status contract, chunk verification,
  duplicate creation, duplicate and concurrent finalization, lease reclaim, crash
  recovery, expiry, cancellation, and authorization. Mobile tests cover persisted
  resume state and terminal-failure presentation.
- A Development Build test transfers a large ProRes video, interrupts the connection,
  resumes, verifies the final hash, and confirms that preview starts only afterwards.

## Open Questions

- None. Cancellation and expiry are terminal session states; the only next action is an
  explicit new upload, and neither state alters the iPhone original.

## Durable Docs Impact

- Updated now: product requirements, functional design, architecture, development
  guidelines, repository structure, and glossary with the Phase 2A session/finalization
  contract and Phase 2B provenance boundary.
- Phase 2B planning must use this finalized Phase 2A contract and
  `docs/ideas/20260711_2-apple-log-preview.md`.
