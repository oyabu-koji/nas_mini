# Feature Spec

## Metadata

- Date: 2026-06-25
- Feature name: backend-asset-read-preview-api
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
  - `docs/ideas/20260622-backend-upload-api.md`
  - `docs/ideas/20260624-backend-preview-worker.md`

## Background

MediaVault Phase 1 MVPでは、iPhoneからMac miniへoriginalをuploadし、Mac mini側でpreviewを生成し、iPhoneアプリでpreviewを確認する。

`backend-upload-api`でoriginal保存、asset作成、preview job登録まで実装済みであり、`backend-preview-worker`で`derived_files`へのpreview記録と`assets.preview_status = preview_ready`更新まで実装済みになった。次に、Mobile Appが保存済みassetを読み取り、生成済みpreviewを再生し、ユーザー確認済みとして`review_status = preview_confirmed`へ更新できるbackend APIが必要である。

このfeatureでは、asset read API、preview streaming API、preview confirmation APIを実装する。Mobile App画面、iPhone側original手動削除、`safe_to_delete_candidate`判定は別featureへ分離する。

## Target Users / Use Cases

- iPhoneユーザーとして、Mac miniへ保存した写真・動画の一覧を見たい。
- iPhoneユーザーとして、退避したassetの詳細、preview状態、確認状態を確認したい。
- iPhoneユーザーとして、軽量previewをアプリ内で再生し、Mac mini側に保存された内容が意図した素材か確認したい。
- iPhoneユーザーとして、preview確認後に「確認済み」にし、後続のiPhone側original手動削除判断へ進めたい。
- 開発者として、Mobile AppのAsset Detail / Preview Review画面を接続できるbackend APIが欲しい。

## Scope

### Asset Read API

- `GET /assets`を追加する。
- `GET /assets/{asset_id}`を追加する。
- すべてBearer Token認証を必須にする。
- Responseにはhost絶対path、API token、不要なfilesystem詳細を含めない。
- `GET /assets`はlimit/offset paginationを持つ。
- 一覧は新しいassetから表示しやすいよう、`created_at DESC, id DESC`で返す。
- Responseにはasset statusとpreview metadataを含める。
- Read API responseでは`original_path`を返さない。

### Preview Streaming API

- `GET /assets/{asset_id}/preview`を追加する。
- すべてBearer Token認証を必須にする。
- `assets.preview_status = preview_ready`のassetだけpreview配信対象にする。
- `derived_files.kind = preview`のrecordを正本として配信対象を決める。
- preview file pathは`MEDIA_ROOT`からの相対pathだけをDBから読み、backend側で安全に解決する。
- path traversal、host絶対path、`MEDIA_ROOT`外への参照を拒否する。
- 動画previewはiPhoneアプリ内再生に必要なRange requestに対応する。
- Range requestがある場合は`206 Partial Content`、`Content-Range`、`Accept-Ranges: bytes`を返す。
- Range requestがない場合もpreview fileを返す。
- 単一rangeのみ対応し、multi-range requestはPhase 1では非対応とする。
- `Content-Type`は`derived_files.mime_type`を使う。
- `derived_files.mime_type`がnullまたは空の場合はmetadata不整合として配信せず、`500 Internal Server Error`相当の安全なerrorにする。
- Responseやerrorにhost絶対pathを含めない。

### Preview Confirmation API

- `POST /assets/{asset_id}/preview-confirmation`を追加する。
- すべてBearer Token認証を必須にする。
- `assets.preview_status = preview_ready`のassetだけ確認済みにできる。
- preview recordが存在するassetだけ確認済みにできる。
- preview recordのpathが安全で、preview fileが存在するassetだけ確認済みにできる。
- 成功時は`assets.review_status = preview_confirmed`にする。
- すでに`preview_confirmed`の場合も成功として扱う。
- このAPIは`review_status`だけを更新する。
- `transfer_status`、`verification_status`、`preview_status`、`delete_candidate_status`、`derived_files`、`jobs`は変更しない。
- Phase 1では`safe_to_delete_candidate`判定を行わない。

## Out of Scope

- `POST /assets/upload`
- preview generation worker
- `GET /jobs`
- `GET /jobs/{job_id}`
- Mobile App画面実装
- preview再生UI
- iPhone側original手動削除
- `local_delete_status`のMobile実装
- `safe_to_delete_candidate`判定
- Backend側original削除
- original再ダウンロード
- thumbnail/proxy配信
- 複数解像度preview選択
- chunk/resume upload
- end-to-end hash verification
- AI解析

## User Flow

### Asset List

1. Mobile AppがSettingsのBackend URLと固定API tokenを使って`GET /assets`を呼ぶ。
2. BackendがBearer Tokenを検証する。
3. Backendがassetを`created_at DESC, id DESC`で取得する。
4. Backendが各assetのpreview metadataを可能な範囲で付与する。
5. Mobile Appが一覧にfilename、type、preview status、review statusを表示する。

### Asset Detail

1. Mobile Appが`GET /assets/{asset_id}`を呼ぶ。
2. BackendがBearer Tokenを検証する。
3. Backendがassetを取得する。
4. assetが存在しない場合は`404`を返す。
5. assetが存在する場合は、asset詳細とpreview metadataを返す。

### Preview Streaming

1. Mobile Appがasset詳細で`preview_status = preview_ready`を確認する。
2. Mobile Appが`GET /assets/{asset_id}/preview`を呼ぶ。
3. BackendがBearer Tokenを検証する。
4. Backendがassetと`derived_files.kind = preview` recordを取得する。
5. Backendがpreview相対pathを`MEDIA_ROOT`内の実pathへ安全に解決する。
6. Mobile Appの動画再生がRange requestを送る場合、Backendは該当byte rangeだけ返す。
7. Mobile Appは軽量previewを再生する。

### Preview Confirmation

1. ユーザーがpreviewを再生し、内容が意図した素材だと判断する。
2. Mobile Appが`POST /assets/{asset_id}/preview-confirmation`を呼ぶ。
3. BackendがBearer Tokenを検証する。
4. Backendが`preview_status = preview_ready`を確認する。
5. Backendが`review_status = preview_confirmed`へ更新する。
6. Mobile Appは確認済み状態を表示する。
7. 後続featureで、確認済みassetにだけiPhone側original手動削除導線を表示する。

## Functional Requirements

### FR-01 Authentication

- `GET /assets`、`GET /assets/{asset_id}`、`GET /assets/{asset_id}/preview`、`POST /assets/{asset_id}/preview-confirmation`はBearer Token認証を必須にする。
- tokenなし、形式不正、値不一致のrequestを拒否する。
- token値をresponse、error、logへ出力しない。

### FR-02 Asset List Endpoint

- `GET /assets`を実装する。
- Query parameter:
  - `limit`: 任意。default `50`、最小`1`、最大`100`。
  - `offset`: 任意。default `0`、最小`0`。
- Responseは`items`, `limit`, `offset`, `total`を含む。
- `items`は`created_at DESC, id DESC`で返す。
- 各itemはasset情報とpreview metadataを含む。
- 各itemのasset情報は以下を含める。
  - `id`
  - `type`
  - `filename`
  - `size_bytes`
  - `server_sha256`
  - `taken_at`
  - `latitude`
  - `longitude`
  - `exif_json`
  - `is_log`
  - `transfer_status`
  - `verification_status`
  - `preview_status`
  - `review_status`
  - `delete_candidate_status`
  - `created_at`
  - `updated_at`
  - `preview`
- `original_path`はresponseに含めない。

### FR-03 Asset Detail Endpoint

- `GET /assets/{asset_id}`を実装する。
- `asset_id`が存在しない場合は`404 Not Found`を返す。
- Responseはasset情報とpreview metadataを含む。
- Responseのasset情報は`GET /assets` itemと同じfieldを含める。
- `preview_status`、`review_status`、`delete_candidate_status`を分離したまま返す。
- `local_delete_status`はMobile側local stateなのでbackend responseには含めない。
- `original_path`はresponseに含めない。

### FR-04 Preview Metadata

- List/Detail responseにpreview metadataを含める。
- preview metadataはnullableとする。
- preview metadataには少なくとも以下を含める。
  - `id`
  - `kind`
  - `mime_type`
  - `size_bytes`
  - `url`
  - `created_at`
- `url`は`/assets/{asset_id}/preview`とする。
- preview metadataに`derived_files.path`やhost絶対pathを含めない。
- preview recordが存在しない場合、preview metadataは`null`にする。
- 複数の`derived_files.kind = preview` recordが存在する場合は、Phase 1では`created_at DESC, id DESC`の最新1件を正本として扱う。
- 最新1件以外のpreview recordの整理、重複検出、repairは別featureで扱う。

### FR-05 Preview Streaming Preconditions

- `GET /assets/{asset_id}/preview`はasset存在確認を行う。
- assetが存在しない場合は`404 Not Found`を返す。
- `assets.preview_status != preview_ready`の場合は`409 Conflict`を返す。
- `derived_files.kind = preview` recordが存在しない場合は`404 Not Found`を返す。
- `derived_files.path`が空、host絶対path、path traversal、`MEDIA_ROOT`外を指す場合は配信せず、`500 Internal Server Error`相当の安全なerrorにする。
- preview recordが存在するが実fileが存在しない場合は配信せず、`500 Internal Server Error`相当の安全なerrorにする。
- `derived_files.mime_type`がnullまたは空の場合は配信せず、`500 Internal Server Error`相当の安全なerrorにする。
- error responseにhost絶対pathを含めない。

### FR-06 Preview Streaming Response

- `Content-Type`は`derived_files.mime_type`を使う。
- `Content-Length`を返す。
- `Accept-Ranges: bytes`を返す。
- Range requestなしの場合は`200 OK`で全体を返す。
- `Range: bytes=start-end`形式を受け付ける。
- `Range: bytes=start-`形式を受け付ける。
- `Range: bytes=-suffix_length`形式を受け付ける。
- 有効なRange requestの場合は`206 Partial Content`を返す。
- `206`では`Content-Range: bytes start-end/total`を返す。
- 不正または範囲外のRange requestは`416 Range Not Satisfiable`を返し、`Content-Range: bytes */total`を返す。
- 複数rangeを含むmulti-range requestはPhase 1では非対応とし、`416 Range Not Satisfiable`を返す。
- HEAD request対応はこのfeatureでは必須にしない。
- 配信時にpreview fileをmemoryへ全量読み込みしない。

### FR-07 Preview Confirmation Endpoint

- `POST /assets/{asset_id}/preview-confirmation`を実装する。
- assetが存在しない場合は`404 Not Found`を返す。
- `assets.preview_status != preview_ready`の場合は`409 Conflict`を返す。
- `derived_files.kind = preview` recordが存在しない場合は`409 Conflict`を返す。
- preview recordのpathが不正、preview fileが存在しない、または`mime_type`がnull/空の場合は`409 Conflict`を返す。
- `review_status`を`preview_confirmed`へ更新する。
- すでに`preview_confirmed`の場合も`200 OK`で現在状態を返す。
- Responseは更新後のasset情報とpreview metadataを返す。
- Responseのasset情報は`GET /assets` itemと同じfieldを含める。
- このAPIは`review_status`だけを更新する。
- `preview_status`、`verification_status`、`delete_candidate_status`を変更しない。
- Phase 1ではこのAPIで`safe_to_delete_candidate`へ変更しない。

### FR-08 Repository / Service Boundaries

- routeは認証、request validation、service呼び出しに集中する。
- asset取得、一覧、件数取得、review status更新はrepositoryへ分離する。
- preview file解決とRange streamingはserviceへ分離する。
- `MEDIA_ROOT` path解決は既存storage helperを使い、クライアント由来pathを信用しない。
- `derived_files.path`はDB由来であっても安全検証する。

### FR-09 Error Response Policy

- `404`: assetが存在しない。またはpreview streamingでpreview recordが存在しない。
- `409`: assetは存在するがpreviewがまだ利用可能でない、失敗状態である、またはpreview confirmationでpreview record/path/file/mime_type条件を満たさない。
- `416`: Range requestが不正または範囲外である。
- `500`: preview recordとfilesystemの不整合、storage path不正、I/O失敗。
- error detailは短くsanitizeする。
- host絶対path、API token、不要な個人情報をerror/logへ含めない。

### FR-10 Response Shape

List item、Detail response、Confirmation responseのasset情報は同じfield setを使う。

```json
{
  "id": 1,
  "type": "video",
  "filename": "IMG_0001.MOV",
  "size_bytes": 12345678,
  "server_sha256": "hex encoded sha256",
  "taken_at": "2026-06-25T10:00:00Z",
  "latitude": 35.0,
  "longitude": 139.0,
  "exif_json": null,
  "is_log": false,
  "transfer_status": "uploaded",
  "verification_status": "server_hash_recorded",
  "preview_status": "preview_ready",
  "review_status": "not_reviewed",
  "delete_candidate_status": "not_candidate",
  "created_at": "2026-06-25 10:00:00",
  "updated_at": "2026-06-25 10:00:00",
  "preview": {
    "id": 1,
    "kind": "preview",
    "mime_type": "video/mp4",
    "size_bytes": 123456,
    "url": "/assets/1/preview",
    "created_at": "2026-06-25 10:01:00"
  }
}
```

- `preview`はpreview recordがない場合`null`にする。
- `original_path`と`derived_files.path`はresponseに含めない。
- `local_delete_status`はMobile側local stateなのでresponseに含めない。

## Non-Functional / Technical Notes

- Backend languageはPython 3.12。
- Dependency managerは`uv`。
- FastAPI / Starletteのresponse機能を使う。
- Range streamingはiPhone動画再生で重要なので、unit/API testで確認する。
- preview file配信は大きなMP4でもmemoryへ全量読み込みしない。
- SQLiteは既存connection helper、WAL mode、busy timeoutを継続する。
- API response schemaはPydanticで定義する。
- `created_at` / `updated_at`は既存DB schemaに合わせ、文字列として返してよい。
- Docker on Mac miniを正規実行環境とし、`MEDIA_ROOT` volume mount配下のpreview fileを配信する。

## Acceptance Criteria

- `GET /assets`がBearer Token必須で、`items`, `limit`, `offset`, `total`を返す。
- `GET /assets`が`limit`と`offset`をvalidationする。
- `GET /assets`がassetを`created_at DESC, id DESC`で返す。
- `GET /assets/{asset_id}`が存在するasset詳細を返す。
- 存在しないasset detailは`404`になる。
- List/Detail responseにpreview metadataがnullableで含まれる。
- preview metadataにhost絶対pathが含まれない。
- `GET /assets/{asset_id}/preview`がBearer Token必須である。
- `preview_status != preview_ready`のpreview requestは`409`になる。
- preview recordなしのpreview requestは`404`になる。
- preview file missingや不正pathはsanitizeされた`500`になり、host絶対pathを返さない。
- Rangeなしpreview requestは`200`でpreview fileを返す。
- Rangeなしpreview requestは`Content-Type`、`Content-Length`、`Accept-Ranges: bytes`を返す。
- 有効な`bytes=start-end`、`bytes=start-`、`bytes=-suffix_length` Range requestは`206`、`Content-Range`、`Accept-Ranges: bytes`を返す。
- multi-range requestは`416`になる。
- 不正または範囲外Range requestは`416`になり、`Content-Range: bytes */total`を返す。
- `POST /assets/{asset_id}/preview-confirmation`がBearer Token必須である。
- tokenなし、形式不正、値不一致のrequestが拒否される。
- `preview_status = preview_ready`のassetだけ`review_status = preview_confirmed`にできる。
- preview recordが存在するassetだけ`review_status = preview_confirmed`にできる。
- preview recordのpathが安全で、preview fileが存在し、`mime_type`があるassetだけ`review_status = preview_confirmed`にできる。
- confirmationは冪等で、2回呼んでも成功する。
- confirmationは`review_status`だけを更新し、`preview_status`、`verification_status`、`delete_candidate_status`を変更しない。
- Phase 1ではconfirmation後も`delete_candidate_status = not_candidate`のままである。
- `cd backend && uv run pytest`相当が成功する。
- `env API_TOKEN=test-token docker compose config`が成功する。
- Docker Desktopが利用可能な場合のみ、任意検証として`env API_TOKEN=test-token docker compose build`が成功する。

## Open Questions

- `GET /assets`の将来filter条件をどのfeatureで追加するか。
- `preview_status = preview_ready`だがpreview record/fileが壊れている場合、将来repair jobを作るか。
- `GET /assets/{asset_id}/preview`のHEAD対応をどのfeatureで追加するか。

## Durable Docs Impact

- 更新候補:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
- 更新要否:
  - 現時点では追加更新不要。
- 理由:
  - 6つの永続ドキュメントには、asset一覧/詳細、preview取得、preview確認API、Token必須、preview確認後のiPhone側original手動削除方針がすでに反映済みである。
  - このfeatureは既存Phase 1方針を具体化するbackend API実装仕様であり、安定方針の追加変更はない。
