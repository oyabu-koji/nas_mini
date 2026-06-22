# Feature Spec

## Metadata

- Date: 2026-06-22
- Feature name: backend-upload-api
- Status: confirmed
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/ideas/initial-requirements.md`
  - `docs/ideas/20260602-backend-foundation.md`

## Background

MediaVault Phase 1 MVPは、iPhoneからMac mini backendへ写真・動画のoriginalを通常アップロードし、Mac mini側でoriginal保存、SHA256記録、preview生成job登録を行う。

`backend-foundation`でFastAPI、SQLite、`MEDIA_ROOT`、Bearer Token認証、job/worker基盤、Docker Composeは整った。次に、Phase 1の中心となる`POST /assets/upload`を実装し、後続のpreview生成worker featureへつなげる。

このfeatureではuploadからasset/job記録までを扱う。ffmpeg preview生成、preview配信、asset一覧/詳細、mobile画面は別featureへ分離する。

## Target Users / Use Cases

- iPhoneユーザーとして、選択した写真・動画をMac miniへ退避したい。
- 開発者として、mobile upload flowを接続できるbackend APIが欲しい。
- 運用者として、LAN内backendでも認証なしuploadを許可したくない。
- 後続featureとして、登録済みpreview jobをworkerで処理できる状態にしたい。

## Scope

### API

- `POST /assets/upload`を追加する。
- Requestは`multipart/form-data`とする。
- Bearer Token認証を必須にする。
- `104857600 bytes`を超えるuploadをbackend側で拒否する。
- metadataをnullableとして受け取る。
- `type`は`image`または`video`のみ許可する。
- `is_log`はbooleanとして受け取る。

### Storage

- upload中ファイルは`${MEDIA_ROOT}/tmp/`へ保存する。
- 確定保存時にbackend生成パスで`${MEDIA_ROOT}/originals/`へ移動する。
- クライアント由来pathを保存先に使わない。
- original保存後にoriginalを改変しない。
- upload失敗時はtmp fileを残さない。

### SHA256

- Mac mini backend側で保存済みoriginalのSHA256を計算する。
- Phase 1ではserver-side SHA256記録のみ行い、end-to-end hash verificationとは扱わない。
- `verification_status`は`server_hash_recorded`にする。

### Database

- `assets`へrecordを作成する。
- `jobs`へpreview生成jobを登録する。
- 初期statusを分離して保存する。
  - `transfer_status = uploaded`
  - `verification_status = server_hash_recorded`
  - `preview_status = preview_generating`
    - このfeatureでは「preview作成待ち、または作成中」を意味する。
  - `review_status = not_reviewed`
  - `delete_candidate_status = not_candidate`

### Worker Compatibility

- このfeatureではffmpeg preview生成は実装しない。
- ただし、upload APIが登録した`preview` / `lut_preview` jobが、preview worker未実装のため即座に`failed`へ落ちないようにする。
- workerは処理可能なjob typeだけをclaimする方式へ調整する。
- job claim関数は`claim_next_job(conn, lease_seconds, supported_job_types)`のように、workerが処理できる`job_type`の集合を受け取る。
- `supported_job_types`が空の場合、workerはqueued jobをclaimしない。
- `preview` / `lut_preview` processorが未実装の間、通常workerの`supported_job_types`には`preview` / `lut_preview`を含めない。
- preview processorが未登録の間、`preview` / `lut_preview` jobは`queued`のまま残る。

## Out of Scope

- ffmpeg preview生成。
- 画像preview生成。
- LUTファイル選定、preview bitrate、ffmpeg retry。
- `GET /assets`
- `GET /assets/{asset_id}`
- `GET /assets/{asset_id}/preview`
- `POST /assets/{asset_id}/preview-confirmation`
- `GET /jobs`
- `GET /jobs/{job_id}`
- Mobile App画面実装。
- `expo-media-library`連携。
- `expo-secure-store`連携。
- chunk upload、resume upload、chunk hash verification。
- iPhone側expected hashとの照合。
- `safe_to_delete_candidate`判定。
- iPhone内original削除。
- 自動削除。

## User Flow

### Upload

1. Mobile appがBackend URLと固定API tokenを設定済みである。
2. Mobile appが写真または動画を選択する。
3. Mobile appが可能な範囲でmetadataを取得する。
4. Mobile appがLOG素材として扱うかを指定する。
5. Mobile appが`POST /assets/upload`へ`multipart/form-data`でoriginal fileとmetadataを送る。
6. BackendがBearer Tokenを検証する。
7. Backendがfile sizeを読み込み中に検証し、`104857600 bytes`超過なら拒否する。
8. Backendがtmpへ保存する。
9. Backendがbackend生成パスでoriginalsへ確定保存する。
10. BackendがSHA256を計算する。
11. Backendがasset recordを作成する。
12. Backendがpreview generation jobを登録する。
13. Backendがasset、server SHA256、status、job情報を返す。

### Failure

1. token不正、metadata不正、file size超過、storage失敗、DB失敗のいずれかが発生する。
2. Backendは適切なHTTP errorを返す。
3. tmp fileがある場合は削除する。
4. original確定前に失敗した場合、asset recordは作らない。
5. original確定後のDB失敗が発生した場合、orphan fileを避けるため可能な範囲で確定originalを削除する。ただし既存assetのoriginalは改変しない。

## Functional Requirements

### FR-01 Upload Endpoint

- `POST /assets/upload`を実装する。
- `Authorization: Bearer <token>`を必須にする。
- `multipart/form-data`でfileとmetadataを同じrequest内で受け取る。
- metadataは単一JSON fieldではなく、以下の個別form fieldとして受け取る。
  - `file`: 必須。original file。
  - `type`: 必須。`image` / `video`のみ許可する。
  - `filename`: 必須。metadataとして保存する元ファイル名。保存先pathには使わない。
  - `taken_at`: 任意。ISO 8601文字列。未指定または空文字はnullとして扱う。
  - `latitude`: 任意。数値。未指定または空文字はnullとして扱う。
  - `longitude`: 任意。数値。未指定または空文字はnullとして扱う。
  - `exif_json`: 任意。JSON文字列。未指定または空文字はnullとして扱う。JSON parseできない場合は`422`で拒否する。
  - `is_log`: 任意。boolean。`true` / `false` / `1` / `0`を受け付け、未指定は`false`とする。

### FR-02 File Size Limit

- Phase 1通常uploadの最大サイズは`104857600 bytes`とする。
- backendは読み込み中に累積sizeを確認する。
- 超過した場合は`413 Payload Too Large`を返す。
- 超過時はtmp fileを削除する。

### FR-03 Original Storage

- 保存先rootは`MEDIA_ROOT`から解決する。
- upload中は`${MEDIA_ROOT}/tmp/`を使う。
- 確定originalは`${MEDIA_ROOT}/originals/`へ保存する。
- 保存ファイル名または保存相対pathはbackendが生成する。
- クライアント指定filenameはmetadataとして保存してよいが、保存先pathには使わない。
- path traversalを許可しない。

### FR-04 SHA256 Recording

- 確定保存されたoriginalを読み、server SHA256を計算する。
- `assets.server_sha256`へ記録する。
- `verification_status`は`server_hash_recorded`にする。
- Phase 1では`file_verified`にはしない。

### FR-05 Asset Record

- `assets` tableへrecordを作成する。
- metadataを保存する。
- `original_path`には`MEDIA_ROOT`からの相対pathを保存する。
- `original_path`はbackendが生成し、client filenameやclient pathには依存しない。
- API responseにhost絶対pathを含めない。
- `delete_candidate_status`は常に`not_candidate`にする。

### FR-06 Preview Job Registration

- upload成功後、`jobs` tableへpreview generation jobを登録する。
- `type=image`の場合は`job_type=preview`とする。
- `type=video`かつ`is_log=false`の場合は`job_type=preview`とする。
- `type=video`かつ`is_log=true`の場合は`job_type=lut_preview`とする。
- `jobs.status`は`queued`とする。
- job payloadには後続preview workerに必要なasset id、original path、type、is_logを含める。
- このfeatureではjob処理本体は実装しない。

### FR-07 Worker Claim Safety

- workerはprocessor未実装の`preview` / `lut_preview` jobを即座に`failed`へ落とさない。
- workerは処理可能なjob typeだけをclaimする。
- `claim_next_job`は`supported_job_types`に含まれるqueued jobだけをclaimする。
- `supported_job_types`が空の場合、claim対象はないものとして扱う。
- 不明なjob typeは通常workerではclaimしない。
- 既存の「unsupported jobをfailedにする」処理は、明示的にclaim済みjobを渡した場合の安全処理として維持し、単体テストで確認する。

### FR-08 Response

- upload成功時は`201 Created`を返す。
- Responseには少なくとも以下を含める。
  - `asset`
  - `job`
  - `server_sha256`
  - `transfer_status`
  - `verification_status`
  - `preview_status`
  - `review_status`
  - `delete_candidate_status`
- ResponseにAPI tokenや不要なfilesystem詳細を含めない。
- ResponseはPydantic schemaで定義する。
- `asset.original_path`をresponseに含める場合は`MEDIA_ROOT`からの相対pathのみとし、host絶対pathは返さない。

## Non-Functional / Technical Notes

- Backend languageはPython 3.12。
- Dependency managerは`uv`。
- FastAPIで`UploadFile` / multipartを扱うため、必要に応じて`python-multipart`を追加する。
- large fileを全量memoryへ読み込まず、chunk単位でtmpへ書き込む。
- DB writeとfile operationの順序は、orphan fileやDB-only recordを避けるように設計する。
- SQLiteは既存connection helper、migration方式、WAL mode、busy timeoutを継続する。
- routeはvalidationとservice呼び出しに集中する。
- upload保存、SHA256計算、path生成はserviceへ分離する。
- DB操作はrepositoryへ分離する。
- token、tmp path、不要な個人情報をlog/errorへ含めない。
- original fileの改変、削除自動化、safe delete candidate判定は行わない。

## Acceptance Criteria

- `POST /assets/upload`がBearer Tokenなしで拒否される。
- `POST /assets/upload`が不正tokenで拒否される。
- `POST /assets/upload`が有効tokenで写真/動画metadataとfileを受け取れる。
- `104857600 bytes`を超えるuploadが`413`で拒否される。
- upload成功時、originalが`${MEDIA_ROOT}/originals/`配下に保存される。
- upload失敗時、tmp fileが残らない。
- 保存pathがclient filenameに依存しない。
- server SHA256が計算され、`assets.server_sha256`へ記録される。
- `verification_status`が`server_hash_recorded`になる。
- `preview_status`が`preview_generating`になる。
- `preview_generating`は、このfeatureではpreview作成待ちまたは作成中を表す。
- `review_status`が`not_reviewed`になる。
- `delete_candidate_status`が`not_candidate`になる。
- preview generation jobが`queued`で作成される。
- LOG指定videoでは`lut_preview` jobが作成される。
- preview worker未実装でも登録済み`preview` / `lut_preview` jobが即座に`failed`へ落ちない。
- workerの`supported_job_types`が空の場合、queued jobがclaimされない。
- 不明なjob typeは通常workerにclaimされず、明示的なunsupported job処理ではfailedにできる。
- upload API、storage service、asset/job repositoryにbackend testsがある。
- `uv run pytest`が成功する。
- `docker compose config`が成功する。
- `docker compose build`が成功する。
- upload APIに関係しないMobile画面、preview生成、preview配信、削除処理が実装されていない。

## Open Questions

- なし。

## Deferred Ideas

- Orphan original file cleanup:
  - このfeatureでは、DB失敗時に保存済みoriginalを削除しようとし、削除できなかった場合はログに残す。
  - 本格的なorphan file検出/清掃は別featureとして扱う。
  - Backlog: `docs/ideas/pre/pre-ideas.md`

## Durable Docs Impact

- 更新候補:
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
- 更新要否: 実装設計で以下が確定した場合のみ更新する。
- 理由:
  - worker claim filtering、upload response schema、orphan file cleanup方針は安定したbackend設計判断になり得るため。
