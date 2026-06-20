# Initial Requirements

Use this document as the starting point for `setup-project`.

`define-project` owns this file. Do not use this file as input to `plan-feature`.

## Project Overview

- Project name: MediaVault
- One-sentence summary: iPhoneで撮影した写真・動画、特に大容量ProRes/LOG動画をMac miniへ安全に退避し、Mac mini側でoriginal保存とpreview生成を行い、iPhone側でpreview確認できるメディア保管アプリ。
- Problem to solve:
  - iPhone内の大容量写真・動画、特にProRes/LOG動画が容量を圧迫する。
  - Mac miniと外部SSDへ退避した後、元ファイルを安全に削除してよいか判断する材料が不足する。
  - ファイル完全性確認と内容確認の役割が混ざると、誤削除リスクが高くなる。
- Core value:
  - originalファイルを改変せずMac mini側へ保存する。
  - Phase 1ではMac mini側でSHA256を計算・記録する。
  - Phase 2以降ではchunk hashと結合後ファイルhashでファイル完全性を検証する。
  - preview確認でユーザーが内容を確認する。
  - 将来的に安全な削除候補判定へ進めるための状態管理を設計段階から入れる。

## Users

- Primary users:
  - iPhoneで写真・動画を多く撮影する個人ユーザー。
  - ProRes/LOGなど大容量動画を扱うユーザー。
  - Mac miniと外部SSDを自宅メディア保管先として使うユーザー。
- Usage context:
  - iPhoneから自宅LAN上のMac miniへ写真・動画を退避する。
  - Mac miniに接続した外部SSDへoriginalを保存する。
  - iPhoneアプリでpreviewを確認し、退避済み素材の内容を確認する。
- Accessibility or age considerations:
  - 誤削除を避けるため、削除関連の文言は明確にする。
  - Phase 1では実削除を行わない。
  - 確認済み/未確認/失敗状態が一目でわかるUIにする。

## Product Goals

- Goal 1: iPhoneからMac miniへoriginalファイルを退避し、Mac mini側でSHA256を計算・記録できる。
- Goal 2: Mac mini側でpreviewを生成し、iPhoneアプリでpreview再生/確認できる。
- Goal 3: 将来の安全削除候補判定に備え、転送状態、検証状態、preview状態、確認状態、削除候補状態、job管理を分離して記録できる。

## Important Principles

- Mobile AppはExpo React Nativeで作る。
- BackendはFastAPIで作る。
- DBはSQLiteを使う。
- Preview生成はMac mini側でffmpegを使う。
- 保存先はMac miniに接続した外部SSDを想定する。
- 保存先ルートはハードコードせず、環境変数 `MEDIA_ROOT` で指定する。
  - 例: `/Volumes/MediaVault`
- originalファイルは絶対に改変しない。
- 削除は自動実行しない。
- preview確認はiPhoneアプリ側で行う。
- Phase 1のSHA256はサーバー側計算・記録であり、iPhone側期待hashとの照合は行わない。
- Phase 2以降のhash検証はファイル完全性確認、preview確認はユーザーによる内容確認として役割を分ける。
- LOG動画はLUT適用previewを生成できるようにする。
- LOG素材のPhase 1 previewはRec.709変換用LUTを適用して生成する。
- MVPでは、LOG素材かどうかはユーザーがアップロード時に選択できるようにする。
- 将来のAI解析/画像解析/動画解析に備えてjob管理方式にする。
- Expo Goは開発初期確認のみ。
- 実運用はApple Developer Program前提のDevelopment Build / Internal Distributionを想定する。

## In Scope

### Phase 1 MVP

Phase 1は安全削除の本番運用ではなく、アップロードからpreview確認までのユーザー体験を検証するMVPとする。

- Phase 1の通常アップロード対象は、1ファイル `104857600 bytes` 以下に制限する。
- Mobileはupload開始前に超過を案内し、backendも超過要求を強制拒否する。
- `104857600 bytes`を超える大容量ProRes/LOG動画の本番退避はPhase 2以降のchunk/resume uploadで扱う。
- iPhoneアプリからoriginalファイルを選択する。
- 可能な範囲で撮影日時・位置情報・EXIFを取得する。
- LOG素材として扱うかをユーザーが選択できる。
- Mac miniへ通常アップロードする。
- `originals` ディレクトリへ保存する。
- Mac mini側でSHA256を計算して記録する。
- preview生成jobを登録する。
- ffmpegでpreviewを生成する。
- LOG対象動画はLUT適用previewを生成する。
- iPhoneアプリでpreviewをダウンロード/再生する。
- ユーザーがpreviewを確認する。
- `review_status` を `preview_confirmed` に更新する。

### Phase 1 Media Access Assumptions

- Phase 1では `expo-media-library` を起点に写真/動画を選択する。
- 取得できるメタデータはOS/APIが返せる範囲に限定する。
- 取得できないEXIF、位置情報、撮影日時はnullableとして扱う。
- iCloud上にあり端末ローカルに存在しない素材は、Phase 1では対象外またはアップロード失敗として扱う。
- ProRes/LOGの自動判定はPhase 1では行わない。
- LOG素材かどうかはユーザー選択を正とする。
- 実運用に必要な権限・ファイルアクセス検証はDevelopment Buildで行う。

### Phase 1 Security Assumptions

- Phase 1ではBackend URLをSettingsで手入力する。
- Phase 1では固定APIトークンをSettingsに保存し、各APIリクエストで `Authorization` ヘッダーとして送る。
- 固定APIトークンは`expo-secure-store`へ保存する。
- `/assets/upload`, `/assets`, `/assets/{asset_id}`, `/assets/{asset_id}/preview`, `/assets/{asset_id}/preview-confirmation`, `/jobs`, `/jobs/{job_id}`はすべて`Authorization: Bearer <token>`を要求する。
- 本格的なユーザー管理、OAuth、複数ユーザー管理はPhase 1対象外とする。
- Mac mini backendはLAN内利用を前提にするが、認証なしのアップロード/閲覧は許可しない。

### Phase 1 Preview Defaults

- Container: MP4
- Video codec: H.264
- Audio codec: AAC if source has audio
- Resolution: 1080p upper bound, aspect ratio preserved
- Color:
  - Non-LOG素材は通常previewを生成する。
  - LOG指定素材はRec.709変換用LUTを適用したpreviewを生成する。
- Image preview:
  - Format: JPEG
  - Resolution: 長辺2048px上限、縦横比を維持する。
  - EXIF orientationを反映した表示向きで生成する。
- LUT:
  - Rec.709変換用LUTをbackend設定で指定できるようにする。
  - originalにはLUTを適用しない。

### MVP対象外だが将来必須の機能

以下はPhase 1では実装しないが、大容量ProRes動画を安全に扱うための最終設計前提として残す。

- chunk upload
- resume upload
- chunk hash verification
- Wi-Fi切断後のresume
- 全chunk完了後のファイル結合
- Mac mini側での全体SHA256計算
- iPhone側で算出した `expected_file_sha256` とMac mini側で算出した `server_sha256` の照合
- `file_verified + preview_confirmed` を満たした場合のみ `safe_to_delete_candidate` にする判定
- Wi-Fi/充電中のみ同期
- originalダウンロード
- LUT設定管理
- 顔検出
- 笑顔判定
- ピント/ブレ判定
- ベストショット抽出
- 動画シーン解析
- AIタグ付け
- FCPXML出力
- Mac miniからMBAへのoriginal取得

## Out of Scope

### Phase 1 Out of Scope

- chunk upload
- resume upload
- chunkごとのhash確認
- `104857600 bytes`を超える大容量ProRes/LOG動画の本番退避
- 安全削除候補の本番運用
- iPhone内originalの実削除
- 自動削除
- AI解析
- originalの再ダウンロード
- 外部SSD管理UI
- Mac mini以外のサーバー配布

### Never Do Automatically

- originalファイルの改変
- iPhone内ファイルの自動削除
- hash未検証またはpreview未確認の素材を安全削除候補として扱うこと

## Screens

- Asset Picker Screen:
  - iPhone内の写真・動画を選択する。
  - 取得可能なメタデータを表示する。
  - LOG素材として扱うかを選択する。
- Upload Queue Screen:
  - upload進捗、失敗、再試行可能状態を表示する。
  - Phase 1では通常アップロードの進捗を表示する。
  - Phase 2以降ではchunk/resume状態を表示する。
- Asset Detail Screen:
  - originalのメタデータ、server SHA256、転送状態、検証状態、preview状態、確認状態、削除候補状態を表示する。
  - previewを再生/確認する。
- Preview Review Screen:
  - previewを再生する。
  - ユーザーが内容確認済みにする。
  - `preview_confirmed` への更新を行う。
- Settings Screen:
  - Backend URLなど接続設定を扱う。
  - 固定APIトークンを保存する。
  - 将来的にLUT設定や同期条件を扱う。

## API Design

### Phase 1 API

- `POST /assets/upload`
  - 通常アップロードでoriginalファイルとメタデータを送る。
  - Request:
    - file
    - type: `image` / `video`
    - filename
    - taken_at
    - latitude
    - longitude
    - exif_json
    - is_log
  - Headers:
    - `Authorization`: fixed API token
  - Response:
    - asset
    - server_sha256
    - transfer_status
    - verification_status
    - preview_status
    - review_status
    - delete_candidate_status
- `GET /assets`
  - asset一覧を取得する。
- `GET /assets/{asset_id}`
  - asset詳細を取得する。
- `GET /assets/{asset_id}/preview`
  - previewファイルを取得する。
- `POST /assets/{asset_id}/preview-confirmation`
  - ユーザーがpreview確認済みにする。
  - `review_status` を `preview_confirmed` に更新する。
- `GET /jobs`
  - job一覧を取得する。
- `GET /jobs/{job_id}`
  - job詳細を取得する。

### Phase 2 API

- `POST /upload-sessions`
  - upload sessionを作成する。
  - iPhone側で算出した `expected_file_sha256` を登録する。
- `PUT /upload-sessions/{session_id}/chunks/{chunk_index}`
  - chunkをアップロードする。
  - chunk hashを送る。
- `GET /upload-sessions/{session_id}`
  - session状態とアップロード済みchunkを取得する。
- `POST /upload-sessions/{session_id}/complete`
  - 全chunk完了後に結合と全体SHA256計算を開始する。
- `POST /upload-sessions/{session_id}/resume`
  - resumeに必要な状態を返す。

## DB Design

### assets

- id
- type: `image` / `video`
- filename
- original_path
- size
- server_sha256
- taken_at
- latitude
- longitude
- exif_json
- is_log
- transfer_status
- verification_status
- preview_status
- review_status
- delete_candidate_status
- created_at
- updated_at

### derived_files

- id
- asset_id
- kind: `preview` / `thumbnail` / `proxy` / `lut_preview`
- path
- size
- created_at

### jobs

- id
- asset_id
- job_type: `preview` / `thumbnail` / `lut_preview` / `ai_score` / `face_detect` / `scene_detect`
- status: `queued` / `running` / `done` / `failed`
- error_message
- created_at
- updated_at

### Future upload_sessions

- id
- asset_id
- filename
- total_size
- chunk_size
- total_chunks
- uploaded_chunks
- expected_file_sha256
- server_sha256
- status
- created_at
- updated_at

### Future upload_chunks

- id
- upload_session_id
- chunk_index
- chunk_path
- expected_hash
- actual_hash
- size
- status
- created_at
- updated_at

### Phase 1 Worker Contract

- APIとは別に単一workerプロセスを起動する。
- workerはSQLite transaction内で`queued` jobを1件だけatomic claimし、`running`へ更新する。
- jobに`claimed_at`と`lease_expires_at`を記録する。
- worker異常終了時は、lease期限切れの`running` jobを`queued`へ戻して再実行可能にする。
- SQLiteはWAL modeと`busy_timeout = 5000ms`を設定する。
- Dockerではworkerを独立serviceとして起動し、`restart: unless-stopped`を設定する。

## Status Model

MediaVaultは将来の安全削除候補判定に備え、状態を役割ごとに分離して保存する。

### transfer_status

- `local_only`
- `uploading`
- `uploaded`
- `failed`

### verification_status

- `not_started`
- `server_hash_recorded`
- `chunk_verified`
- `file_verified`
- `failed`

Phase 1では `server_hash_recorded` までを扱う。`chunk_verified` と `file_verified` はPhase 2以降で扱う。

### preview_status

- `not_started`
- `preview_generating`
- `preview_ready`
- `failed`

### review_status

- `not_reviewed`
- `preview_confirmed`

### delete_candidate_status

- `not_candidate`
- `safe_to_delete_candidate`

## Safe Delete Candidate Conditions

Phase 1では本格的な削除候補判定は行わない。

Phase 2以降で、以下をすべて満たした場合のみ `safe_to_delete_candidate` とする。

- `upload_sessions.status = completed`
- 対象sessionの全`upload_chunks.status = verified`
- `assets.verification_status = file_verified`
  - iPhone側の `expected_file_sha256` とMac mini側の `server_sha256` が一致している。
- `assets.preview_status = preview_ready`
- `assets.review_status = preview_confirmed`

## Directory Structure

### Repository

- `App.jsx`
- `index.js`
- `app.json`
- `package.json`
- `src/mobile/`
  - `screens/`
  - `components/`
  - `hooks/`
  - `services/`
  - `api/`
  - `utils/`
- `backend/`
  - `app/`
    - `main.py`
    - `api/`
    - `models/`
    - `services/`
    - `jobs/`
    - `db/`
  - `pyproject.toml`
  - `uv.lock`
- `docs/`
- `docs/ideas/`
- `.steering/`

### Media Root

`MEDIA_ROOT` で指定する。例: `/Volumes/MediaVault`

- `${MEDIA_ROOT}/originals/`
- `${MEDIA_ROOT}/previews/`
- `${MEDIA_ROOT}/thumbnails/`
- `${MEDIA_ROOT}/jobs/`
- `${MEDIA_ROOT}/tmp/`

Rules:

- `originals/` 内のoriginalファイルは改変しない。
- preview/thumbnail/proxy/lut_previewはderived fileとして別ディレクトリに保存する。
- 一時ファイルは `tmp/` に置き、処理完了後に安全に片付ける。

## Technical Constraints

- Mobile App: Expo React Native
- Mobile language: JavaScript (not TypeScript)
- Mobile runtime: Expo SDK 54
- Node: 24
- Backend: FastAPI
- Backend language: Python
- DB: SQLite
- Preview generation: ffmpeg
- Media root: `MEDIA_ROOT`
- Device library access: `expo-media-library`
- Authentication:
  - Phase 1 uses a fixed API token.
  - Requests send `Authorization: Bearer <token>`.
  - Mobile stores the token with `expo-secure-store`.
- Development:
  - Expo Go is only for early development checks.
  - Real operation assumes Development Build / Internal Distribution with Apple Developer Program.
- Docker:
  - Docker is the canonical runtime when moving to Mac mini.
  - Do not copy local `node_modules` into Docker.
  - Install dependencies inside Docker.

## Development Rules

- Start command: `npx expo start`
- Remote device testing: `npx expo start --tunnel`
- Expo dependencies must be added with `npx expo install`
- Expo SDK must not be upgraded automatically
- Node version must not be changed automatically
- original files must never be modified.
- Delete actions must never run automatically.
- Backend must not hardcode the storage root; use `MEDIA_ROOT`.
- Phase 1 SHA256 recording, Phase 2 hash verification, and preview confirmation must be modeled separately.
- Authentication must not be omitted from upload/preview APIs, even in Phase 1.

## Device or Platform Features

- Sound: Not required in Phase 1.
- Haptics: Optional for upload/confirmation feedback.
- Camera: Not required in Phase 1; selection from library is required.
- Notifications: Future option for long-running upload/job completion.
- Offline support:
  - Phase 1: limited; upload failure handling only.
  - Phase 2: resume upload required.
- Media library:
  - `expo-media-library` is expected for photo/video library access.
  - Missing EXIF/location/taken_at must be handled as nullable metadata.
  - iCloud-only assets are out of scope or treated as upload failures in Phase 1.
- Network:
  - Local network access to Mac mini backend is required.
  - Settings must allow manual backend URL and fixed API token configuration.
  - API requests use `Authorization: Bearer <token>`.

## Implementation Phases

### Phase 1 MVP

Goal: Validate the upload to preview confirmation user experience.

Tasks:

- Mobile app:
  - Create basic navigation and screens.
  - Select photo/video from iPhone library.
  - Read available metadata: taken_at, location, EXIF where possible.
  - Treat missing metadata as nullable.
  - Allow user to mark an upload as LOG material.
  - Configure backend URL and fixed API token in Settings.
  - Upload selected file with metadata to backend.
  - Show upload progress/status.
  - Fetch asset detail and preview status.
  - Download/play preview.
  - Confirm preview and update `review_status` to `preview_confirmed`.
- Backend:
  - Create FastAPI project baseline.
  - Configure `MEDIA_ROOT`.
  - Create SQLite schema for `assets`, `derived_files`, `jobs`.
  - Store separated asset status fields.
  - Require fixed API token for upload and preview APIs.
  - Implement normal upload endpoint.
  - Enforce the Phase 1 `104857600 bytes` file size limit.
  - Save original under `originals/`.
  - Compute server SHA256.
  - Record asset metadata.
  - Register preview generation job.
  - Run ffmpeg preview generation.
  - Generate H.264 MP4 preview with 1080p upper bound.
  - Generate Rec.709 LUT-applied preview for LOG assets.
  - Generate JPEG image preview with 2048px long-edge upper bound and applied EXIF orientation.
  - Serve preview files.
  - Implement preview confirmation endpoint.
- Quality:
  - Verify original is never modified.
  - Verify storage path is based on `MEDIA_ROOT`.
  - Verify Phase 1 SHA256 recording and preview confirmation are separate states.
  - Verify upload/preview APIs require the fixed API token.
  - Verify no delete action exists in Phase 1.

### Phase 2

Goal: Strengthen safe transfer for large files.

Tasks:

- Create upload session.
- Upload files by chunk.
- Verify chunk hash per chunk.
- Resume upload after Wi-Fi interruption.
- Assemble chunks after completion.
- Compute full server SHA256 on Mac mini.
- Generate preview.
- Confirm preview on iPhone.
- Set `safe_to_delete_candidate` only when all safety conditions are met.

### Phase 3+

Goal: Expand media management and analysis.

Backlog:

- Wi-Fi/charging-only sync.
- original download.
- LUT settings management.
- Face detection.
- Smile scoring.
- Focus/blur scoring.
- Best shot extraction.
- Video scene analysis.
- AI tagging.
- FCPXML export.
- Original retrieval from Mac mini to MBA.

## Acceptance Criteria

### Phase 1

- User can select an iPhone photo/video and upload it to the Mac mini backend.
- Backend saves the original under `MEDIA_ROOT/originals/`.
- Backend computes and records server SHA256 without claiming end-to-end hash verification in Phase 1.
- Backend creates a preview generation job.
- Backend generates an H.264 MP4 preview with 1080p upper bound using ffmpeg.
- LOG-marked videos can produce Rec.709 LUT-applied preview.
- Image assets can produce JPEG preview with 2048px long-edge upper bound and applied EXIF orientation.
- iPhone app can fetch and play/download the preview.
- User can mark preview as confirmed.
- Asset `review_status` can become `preview_confirmed`.
- Upload and preview APIs reject requests without the configured fixed API token.
- Mobile and backend enforce the Phase 1 file limit of `104857600 bytes`.
- No automatic delete action exists.
- `safe_to_delete_candidate` is not used for production deletion in Phase 1.

### Phase 2+

- Large file upload supports chunk upload and resume.
- Each chunk can be hash verified.
- Full assembled file SHA256 matches the iPhone-side `expected_file_sha256`.
- `safe_to_delete_candidate` is assigned only when all required safety conditions are satisfied.

## Open Questions

- Which concrete Rec.709 LUT file should be bundled or configured for LOG preview in Phase 1?
- Which preview bitrate should be the default?
- How should the Phase 1 single worker be supervised and restarted in Docker?
- What local network discovery or backend URL setup is required for iPhone to connect to Mac mini?
- Which EXIF/location fields are reliably available through `expo-media-library` and platform APIs?
- Should thumbnails be generated in Phase 1 or deferred?
- How should failed ffmpeg jobs be retried?
