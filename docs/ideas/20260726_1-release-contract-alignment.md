# Feature Spec

## Metadata

- Date: 2026-07-26
- Feature name: Release contract alignment
- Status: confirmed
- Priority: required before Phase 2C planning
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/ideas/iphone_applelog_app_distribution_and_lut_policy.md`
  - `app.json`
  - `ios/LatestTemplate/Info.plist`
  - `src/shared/api/mediaVaultApi.js`
  - `src/shared/services/settingsStorage.js`
  - `src/shared/services/secureTokenStorage.js`
  - `src/features/settings/`
  - `src/features/original-deletion/`
  - `src/features/asset-picker/`
  - `backend/tests/`

## Background

Phase 1, Phase 2A, processed-result delivery, managed rendition, and the Phase 2B
Apple Log closed-gate implementation are present. A cross-document review after the
Phase 2B implementation found that several durable requirements no longer match the
current release policy or code:

- The Mobile app warns about a public HTTP Backend URL but still stores it and can send
  the Bearer token to it.
- The original-deletion hook requires an enabled Phase 2B capability and a ready formal
  preview for every asset. This makes the Phase 1 manual-deletion flow unreachable for
  direct-upload images and historical Phase 1 direct-upload videos.
- Durable documents describe server name, server ID, and per-server token storage even
  though the confirmed initial-release policy is one manually entered Backend URL and
  one token stored in SecureStore.
- Durable documents describe `GET /jobs`, `GET /jobs/{job_id}`, and a dedicated Upload
  Queue screen even though these were excluded from the current release. Jobs remain an
  internal Backend execution model.
- The Expo display name is still `Latest Template`, and the LOG toggle implies that it
  controls the Backend LOG pipeline even though `is_log` is audit-only.
- The architecture requires HEIC, JPEG, and PNG decoding to be checked using the pinned
  Docker ffmpeg build, but only command-level and mocked image tests exist.

These are not one new product capability. They are one bounded release-contract
remediation that must make the durable documents, Mobile behavior, release metadata,
and automated validation agree before Phase 2C deletion-candidate work begins.

## Goal

Restore a coherent initial-release contract in which:

- the app sends a Bearer token only to an accepted private HTTP endpoint or a valid
  HTTPS endpoint;
- Phase 1 direct assets can use the existing explicit manual-deletion flow after a
  ready and confirmed preview, while session-derived Phase 2 videos retain the stronger
  formal-preview gate;
- the initial release remains a single-server configuration;
- `/jobs` and a dedicated Upload Queue remain outside the public release surface;
- the app identifies itself as MediaVault and describes the legacy LOG hint accurately;
- the Docker ffmpeg image-decoding assumptions are executable and reproducible.

## Confirmed Decisions

### Initial Server Configuration

- The initial release stores exactly one active Backend configuration.
- The user manually enters a Backend URL and fixed API token.
- The Backend URL remains in normal settings storage.
- The fixed API token remains in `expo-secure-store`.
- Server name, server ID, multiple saved servers, server switching, endpoint discovery,
  and QR import are future work.
- Existing single-server storage keys may be retained. This feature does not introduce
  a migration to a multi-server data model.

### Public API Surface

- `GET /jobs` and `GET /jobs/{job_id}` are not part of the current public API.
- A dedicated Upload Queue screen is not part of the current Mobile navigation.
- Upload progress, resumable state, and terminal errors remain in the existing picker,
  asset, and resumable-upload flows.
- Backend `jobs` records, worker claim/lease behavior, and internal job repositories
  remain unchanged.

### Manual Original Deletion

- iPhone original deletion is always initiated by an explicit user action and native
  confirmation.
- Phase 1 direct assets and Phase 2 session-derived videos use separate eligibility
  predicates.
- Phase 1 does not require `safe_to_delete_candidate`.
- Phase 2B does not reinterpret a managed rendition as a formal preview.
- Backend originals, derived files, and asset records are never deleted by this Mobile
  action.

## Target Users / Use Cases

- A user connecting the app to an MBA or Mac mini through a LAN or Tailscale private
  endpoint without risking token transmission to a public HTTP host.
- A user who uploaded a Phase 1 image or historical Phase 1 video and wants to delete
  the iPhone original only after playing or viewing and confirming the stored preview.
- A user with a Phase 2 session-derived video who must not see the deletion action until
  the compatible Phase 2B formal preview is ready and confirmed.
- A developer or Mac mini operator who needs the pinned Docker ffmpeg image to prove it
  can decode the supported Phase 1 image formats.

## Scope

- Add one shared Backend URL validator used before settings persistence and again at
  every API request boundary.
- Keep the initial single-server URL and SecureStore token model.
- Prevent an Authorization header from being constructed and prevent any network
  adapter from being invoked for a rejected URL.
- Split original-deletion eligibility by asset origin/verification contract.
- Preserve existing local mapping, explicit confirmation, native deletion, and terminal
  local outcome behavior.
- Remove current-release references to `/jobs`, job-detail API, dedicated Upload Queue,
  server name, server ID, and per-server token storage from the six durable documents.
- Preserve those capabilities only as explicit future work where relevant.
- Change the Expo user-facing display name to `MediaVault`.
- Synchronize the checked-in iOS display name, version, and ATS values with `app.json`.
- Change the LOG toggle copy so it is clearly an audit-only legacy hint and does not
  claim to activate detection or LUT processing.
- Add license-clear, deterministic HEIC, JPEG, and PNG test fixtures with source or
  generator provenance and SHA-256 values.
- Add an executable Docker integration check that uses the pinned Backend image and
  ffmpeg to generate valid JPEG previews from all three fixture formats.
- Update the six durable documents to match the implemented result.

## Out of Scope

- Multiple server profiles, server switching, server name/ID storage, discovery, QR
  import, or token migration to per-server keys.
- `GET /jobs`, `GET /jobs/{job_id}`, a public jobs schema, or a dedicated Upload Queue
  screen.
- Changes to the internal Backend job model, worker lease logic, or worker APIs.
- Phase 2C `safe_to_delete_candidate` computation or automatic transition.
- Automatic iPhone original deletion or Backend original deletion.
- Enabling the production Apple Log detector, supplying private user recordings,
  applying the Phase 2B offline migration, or enabling
  `generated-apple-log-rec709`.
- Apple Log to Rec.709 LUT creation, licensing, registration, or quality approval.
- Multiple output color spaces or changes to managed rendition selection.
- App Store slug, production bundle identifier, signing, review backend provisioning,
  or distribution automation.
- A general UI redesign or navigation framework replacement.
- Requiring physical-device validation to count toward Jest coverage. Existing
  Development Build checks remain separate operator acceptance work.

## User Flow

### Connection Settings

1. The user opens Settings and enters one Backend URL and API token.
2. The app parses and validates the complete Backend URL before writing either setting.
3. A rejected URL produces a stable, non-sensitive error and neither the URL nor a new
   token is persisted.
4. An accepted URL is normalized, the URL is stored in normal settings storage, and the
   token is stored in SecureStore.
5. Health checks and all later API calls repeat the same URL validation before building
   Authorization headers or invoking the network adapter.

### Phase 1 Direct-Asset Deletion

1. A direct-upload Phase 1 asset reaches `preview_status = preview_ready`.
2. The user opens and confirms the preview, producing
   `review_status = preview_confirmed`.
3. If the local original mapping is available and no successful local deletion outcome
   exists, the app displays the delete action without requiring Phase 2B capability or
   a formal preview.
4. The user confirms the native deletion prompt.
5. The app deletes only the mapped iPhone Photos asset and records the terminal local
   outcome. Backend state is unchanged.

### Phase 2 Session-Derived Video Deletion

1. The video has `verification_status = file_verified`.
2. The Backend advertises a compatible, enabled formal Apple Log preview capability.
3. The asset exposes the current `formal_preview.state = ready`, including a valid
   converted or explicitly unconverted formal result.
4. The user confirms that preview.
5. If the local mapping is available and the item was not already deleted, the app
   displays the delete action.
6. A missing/disabled capability, generating/failed/missing formal preview, incompatible
   client, missing mapping, or unconfirmed preview keeps the action hidden.

## Functional Requirements

### Backend URL Contract

- Parse the URL using a structured URL parser. Prefix matching alone is prohibited.
- Accept only an origin-style base URL:
  - scheme is `https`, or
  - scheme is `http` and the hostname is an explicitly accepted private endpoint.
- An origin-style base URL may contain a port and a trailing `/`, but must not contain
  username, password, query, fragment, or a non-root path.
- Normalize only the trailing root slash. Do not rewrite the hostname, port, or scheme
  into a different endpoint.
- Accepted private HTTP endpoint forms are:
  - IPv4 in `10.0.0.0/8`;
  - IPv4 in `172.16.0.0/12`;
  - IPv4 in `192.168.0.0/16`;
  - Tailscale IPv4 in `100.64.0.0/10`;
  - a syntactically valid single-label MagicDNS hostname other than `localhost`;
  - a syntactically valid `.local` LAN hostname.
- Reject public IPv4, IPv4 outside the stated ranges, loopback, link-local, unspecified
  addresses, `localhost`, deceptive suffix/prefix hosts, malformed URLs, unsupported
  schemes, and credential-bearing URLs when used with HTTP.
- HTTPS remains allowed for the future independent App Review Backend. Acceptance of an
  HTTPS URL does not imply that the current home Backend may be exposed publicly.
- A qualified `.ts.net` hostname is accepted only with HTTPS. For the initial private
  HTTP workflow, the user uses its Tailscale IPv4 address or single-label MagicDNS name.
  The app must not use a broad ATS exception solely to permit arbitrary qualified HTTP
  hostnames.
- The validator is shared by Settings persistence, health checks, JSON requests,
  multipart upload, upload-session/chunk requests, preview streaming, and processed
  result download.
- Invalid stored legacy values are treated as invalid settings. No Authorization header
  is constructed and no network adapter is called.
- URL validation errors use a stable code and do not include the token or full rejected
  URL in logs or routine UI errors.
- Settings validates the URL and selected token before either persistence write. A
  failed validation must not partially replace the last usable settings.
- `app.json` and the checked-in iOS plist use the exact ATS values defined in Product
  Identity and LOG Hint below. Application-level request validation remains mandatory;
  ATS is defense in depth and is not the endpoint allowlist authority.

### Single-Server Settings Contract

- Settings displays only Backend URL and API token fields for server identity.
- The app stores one normalized Backend URL under normal settings storage.
- The app stores one API token under the existing SecureStore key.
- The token input never displays the stored token value.
- Replacing a token requires explicit input and successful validation.
- Durable documents describe server name/ID and multiple profiles only as future work,
  not as initial-release acceptance criteria.

### Original-Deletion Eligibility

- The eligibility function must be testable independently from native deletion.
- Common conditions for all asset types are:
  - `preview_status = preview_ready`;
  - `review_status = preview_confirmed`;
  - local asset mapping status is `available`;
  - local deletion outcome is not `deleted`;
  - local mapping/outcome loading or deletion is not in progress.
- A Phase 1 direct asset is identified by
  `verification_status = server_hash_recorded`. It does not require Phase 2B capability
  or `formal_preview`.
- A Phase 2 session-derived video is identified by `type = video` and
  `verification_status = file_verified`. It additionally requires:
  - sanitized capabilities report a compatible
    `formalAppleLogPreview = true`;
  - `formal_preview.state = ready`.
- A Phase 2 session-derived video must not use a legacy preview, legacy `is_log` hint,
  managed rendition, or active processed-result pointer as deletion authority.
- Any asset that does not match one of the explicit predicates is ineligible.
- `delete_candidate_status = safe_to_delete_candidate` is not introduced or required by
  this feature.
- The confirmation dialog continues to identify the asset and states that only the
  iPhone original is deleted.
- Native deletion success is terminal in memory before local outcome persistence.
  Persistence failure must not redisplay the deletion action.

### Public Jobs and Upload Queue Contract

- Remove `GET /jobs` and `GET /jobs/{job_id}` from current public API tables, security
  endpoint lists, user stories, and test obligations.
- Remove the dedicated Upload Queue from the current screen table and component-test
  obligations.
- Do not remove internal `jobs` data-model, status, worker, repository, or glossary
  documentation that describes Backend execution.
- Existing upload progress and resumable recovery behavior remains unchanged.

### Product Identity and LOG Hint

- Set `expo.name` to `MediaVault`.
- Do not change `expo.slug` or the bundle identifier in this feature.
- Treat the checked-in `ios/LatestTemplate/Info.plist` as release input. Its
  `CFBundleDisplayName` must be `MediaVault`, and
  `CFBundleShortVersionString` must equal `expo.version`.
- Set the same explicit ATS policy in `app.json` and the checked-in plist:
  - `NSAllowsArbitraryLoads = false`;
  - `NSAllowsLocalNetworking = true`.
- The implementation may use a controlled Expo prebuild sync or update the checked-in
  native file directly, but the resulting native diff is reviewed and the final plist
  values are verified. `npx expo export` alone is not evidence that checked-in native
  settings were synchronized.
- Preserve the LOG toggle and the `is_log` payload as legacy audit data.
- Replace copy that claims to apply a Backend LOG pipeline with copy equivalent to:
  `Stored as a legacy hint. Apple Log detection is automatic.`
- The toggle must not affect automatic detector classification, formal preset
  resolution, or LUT authorization.

### Docker Image Codec Verification

- Store fixtures under a test-only Backend fixture directory, separate from
  `MEDIA_ROOT`, user media, and detector certification recordings.
- Each HEIC, JPEG, and PNG fixture is minimal, non-sensitive, license-clear, and
  reproducible. Record its generator or source, license/ownership statement, and
  lowercase SHA-256.
- The Docker integration check builds or uses the same pinned Backend image as API and
  worker services.
- For every input format, run the production image-preview ffmpeg command or adapter
  inside the container and assert:
  - exit status is successful;
  - output is a non-empty JPEG;
  - output dimensions are at most 2048 pixels on the long edge;
  - aspect ratio is preserved within integer scaling tolerance;
  - the original fixture is byte-for-byte unchanged.
- HEIC support must be proved by actual decode/render. Renaming JPEG bytes to `.heic`,
  mocking ffmpeg, or checking only `ffmpeg -formats` is insufficient.
- The check fails rather than silently skipping an unsupported codec.
- Fixture generation and validation must not require network access.

## Non-Functional / Technical Notes

- Keep React Native, Expo managed workflow, JavaScript, Node 24, and Expo SDK 54.
- Do not introduce TypeScript or upgrade Expo/Node.
- The iOS deployment target is 12 or later. The initial ATS policy relies on
  `NSAllowsLocalNetworking` for IP addresses, unqualified hostnames, and `.local`
  hostnames while keeping the global arbitrary-load exception disabled. This follows
  Apple's `NSAllowsLocalNetworking` contract:
  https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking
- Qualified `.ts.net` HTTP is intentionally rejected because it is not an unqualified
  or `.local` hostname. HTTPS `.ts.net`, Tailscale IPv4 HTTP, and single-label MagicDNS
  HTTP remain available.
- Keep URL parsing and endpoint classification in a shared pure module so Settings and
  every transport use one authority.
- Avoid DNS lookup as a security decision. The HTTP allowlist is based on the parsed
  hostname/IP syntax defined above.
- Use exact IPv4 numeric parsing and CIDR checks. Values such as `100.128.0.1`,
  `10.example.com`, `010.0.0.1`, and IPv4-in-credential syntax must not pass through
  string-prefix ambiguity.
- Preserve API timeouts, resumable upload idempotency, result-integrity checks, and token
  sanitization from preceding features.
- Device APIs remain in services; screens do not call `expo-media-library`,
  AsyncStorage, SecureStore, or HTTP adapters directly.
- The Docker codec test may be a dedicated Compose profile or a documented one-shot
  command. It must use the pinned production image and have a deterministic exit code.
- If the Docker daemon is unavailable during implementation validation, the unexecuted
  codec integration remains explicitly open and the feature is not represented as fully
  Docker-validated.

## Test Requirements

### Mobile

- URL validator table tests cover all accepted private IPv4 ranges and boundaries,
  Tailscale `100.64.0.0/10` boundaries, single-label MagicDNS, `.local`, and HTTPS.
- Rejection tests cover public HTTP, `localhost`, `127.0.0.0/8`, `169.254.0.0/16`,
  `0.0.0.0`, `100.128.0.1`, qualified `.ts.net` HTTP, malformed IPv4, deceptive
  hostname suffixes, credentials, query, fragment, non-root path, and unsupported
  schemes.
- HTTPS tests include a qualified `.ts.net` endpoint.
- Settings tests prove invalid values persist neither URL nor replacement token.
- API tests prove an invalid stored URL constructs no Authorization header and invokes
  no fetch/download/upload adapter.
- Original-deletion matrix tests cover:
  - eligible Phase 1 direct image;
  - eligible historical Phase 1 direct video;
  - unready or unconfirmed Phase 1 asset;
  - Phase 2 `file_verified` video while capability is disabled;
  - ready and confirmed Phase 2 formal converted preview;
  - ready and confirmed Phase 2 formal unconverted preview;
  - missing, generating, or failed formal preview;
  - unavailable mapping and already-deleted outcome.
- Existing permission denial, user cancellation, native success, and persistence-failure
  tests continue to pass.
- UI tests assert the MediaVault display contract where testable and the audit-only LOG
  hint copy.
- A native-config verification test parses `app.json` and
  `ios/LatestTemplate/Info.plist` and asserts:
  - Expo name and `CFBundleDisplayName` are both `MediaVault`;
  - Expo version and `CFBundleShortVersionString` are equal;
  - `NSAllowsArbitraryLoads` is false in both sources;
  - `NSAllowsLocalNetworking` is true in both sources.
- The native-config check uses `plutil` or another structured plist parser. Regex-only
  matching of plist XML is insufficient.

### Backend and Docker

- Existing Backend tests continue to pass.
- Fixture SHA-256 and provenance are checked without network access.
- The pinned Docker image successfully renders the HEIC, JPEG, and PNG fixtures using
  the production image-preview path.
- A negative fixture or controlled invalid input proves the integration command exits
  non-zero rather than reporting a false pass.

### Quality Commands

```bash
npm run lint
npm test
npm run test:coverage
npx expo install --check
npx expo export --platform ios
uv run --directory backend pytest
env API_TOKEN=test-token docker compose config
plutil -lint ios/LatestTemplate/Info.plist
```

- The implementation plan must define one canonical Docker image-codec integration
  command and record its exact result.
- The implementation plan must define one canonical checked-in iOS config verification
  command and record its exact result. The command checks values, not only plist syntax.
- `git diff --check` succeeds.
- Generated export, coverage, Docker, media, and temporary files do not appear as
  untracked release artifacts.

## Acceptance Criteria

- Public HTTP outside the explicit private endpoint allowlist cannot be saved or used
  by any authenticated API path.
- Rejected URLs cause no network call and do not expose or partially replace the token.
- Valid RFC1918, Tailscale IPv4, single-label MagicDNS, `.local`, and HTTPS endpoints
  remain usable. Qualified `.ts.net` HTTP is rejected and qualified `.ts.net` HTTPS is
  accepted.
- The initial-release Settings model remains one Backend URL and one SecureStore token.
- A ready and confirmed Phase 1 direct image or historical direct video with a valid
  local mapping exposes the explicit manual-delete action without requiring Phase 2B.
- A session-derived `file_verified` video exposes that action only with compatible
  Phase 2B capability, a ready formal preview, confirmation, and a valid local mapping.
- Managed renditions, legacy LOG hints, failed formal previews, and missing mappings
  never authorize deletion.
- Native deletion remains explicit, iPhone-only, and terminal after native success.
- `/jobs`, `/jobs/{job_id}`, server name/ID, per-server token storage, and dedicated
  Upload Queue are no longer described as current-release deliverables.
- Internal Backend job processing remains unchanged.
- `app.json` and checked-in iOS native configuration agree on the `MediaVault` display
  name, `0.2.0` application version, and exact ATS values. Slug and bundle identifier
  remain unchanged.
- The LOG toggle no longer claims to activate the Backend LOG pipeline.
- The pinned Docker Backend image actually decodes HEIC, JPEG, and PNG and creates valid
  constrained JPEG previews without modifying the fixtures.
- All quality commands pass, with coverage floors unchanged or raised.
- The six durable documents contain no contradictory current-release connection,
  deletion, jobs, queue, product-name, LOG-hint, or image-codec contract.

## Open Questions

There are no blocking product decisions for planning this feature.

The following remain explicit future decisions and must not expand this feature:

- production App Store slug, bundle identifier, signing, and review environment;
- multiple-server UX and token-key migration;
- endpoint discovery;
- production Apple Log detector artifacts and Rec.709 transform approval;
- Phase 2C deletion-candidate activation.

## Durable Docs Impact

| Durable document | Required changes | Unchanged / non-applicable checks |
|---|---|---|
| `docs/product-requirements.md` | Restore the single-server initial-release contract; remove public `/jobs` requirements; distinguish Phase 1 and Phase 2 manual-deletion gates; define private HTTP versus HTTPS acceptance | Keep MediaVault as the product name; keep Phase 2C and Phase 3+ scope unchanged; implementation file placement and fixture mechanics are non-applicable |
| `docs/functional-design.md` | Remove dedicated Upload Queue and public job endpoints; update Settings and URL-error flows; define the two deletion paths; describe the audit-only LOG hint | Keep internal job-driven preview flows and managed/formal preview wire contracts unchanged; native plist layout is non-applicable |
| `docs/architecture.md` | Define the shared request-boundary URL validator; record exact ATS values and checked-in native synchronization; distinguish direct-asset and formal-preview deletion authority; make Docker image-codec validation executable | Keep storage, worker, processed-result authority, LUT, and detector architecture unchanged |
| `docs/repository-structure.md` | Place the shared URL validator, native-config verifier, image fixtures, and Docker codec-check entry point; identify checked-in `ios/` as synchronized release input | Keep Backend job repositories/workers and existing naming rules unchanged |
| `docs/development-guidelines.md` | Update Mobile URL, single-server settings, deletion, and public-jobs rules; require URL/deletion matrices, exact native-config verification, and Docker codec integration | Keep current lint/coverage floors, Expo/Node/JavaScript rules, and internal worker rules unchanged |
| `docs/glossary.md` | Clarify Phase 1 direct asset and separate Phase 1/Phase 2 manual-deletion eligibility; clarify that jobs are internal rather than public API resources where necessary | Product display configuration, ATS key layout, UI copy, and fixture paths are non-applicable; keep existing Apple Log, LUT, provenance, and job-status definitions unchanged |

- Cross-document release checks:
  - product-name and LOG-hint semantics agree wherever they are mentioned;
  - URL and deletion contracts have one authority and no current-release server-profile
    or public-job requirements remain;
  - Docker image-codec verification is specified only in engineering documents where
    implementation and validation detail belongs.
- Additional policy document review required:
  - `docs/ideas/iphone_applelog_app_distribution_and_lut_policy.md` must continue to
    describe multiple servers and App Review Backend as future architecture while making
    the single-server initial release explicit.
- Update timing:
  - apply durable-document edits in the implementation feature, in the same change as
    the corresponding behavior and tests;
  - do not update `.steering/` in `define-feature`; create it with `plan-feature`.
