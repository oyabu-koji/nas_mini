# Feature Spec

## Metadata

- Date: 2026-06-24
- Feature name: backend-preview-worker
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

## Background

MediaVault Phase 1 MVPでは、iPhoneからMac mini backendへ写真・動画のoriginalをuploadし、Mac mini側で軽量previewを生成して、iPhone側で内容確認できる状態にする。

`backend-upload-api`では、original保存、server-side SHA256記録、asset record作成、`preview` / `lut_preview` job登録まで完了した。次に、workerがqueued jobをclaimし、ffmpegでpreviewを生成し、`derived_files`、`jobs`、`assets.preview_status`を更新する必要がある。

このfeatureでは、preview worker本体を実装する。preview取得API、asset一覧/詳細API、mobile画面、preview確認APIは別featureへ分離する。

## Target Users / Use Cases

- iPhoneユーザーとして、Mac miniへ保存した写真・動画の内容を軽いpreviewで確認したい。
- ProRes / LOG動画を扱うユーザーとして、重いoriginalをiPhoneで直接開かず、見やすいRec.709 previewで確認したい。
- ProRes以外の通常動画を扱うユーザーとしても、同じ仕組みで軽いMP4 previewを確認したい。
- 開発者として、upload APIが登録した`preview` / `lut_preview` jobをworkerで処理できる状態にしたい。

## Scope

### Worker

- Workerが`preview` / `lut_preview` jobをclaimできるようにする。
- `queued` jobを`running`へ更新し、処理完了後に`done`または`failed`へ更新する。
- 既存のlease recovery、atomic claim、単一worker前提を維持する。
- 未対応job typeは通常workerではclaimしない。

### Input Assets

- `assets.type = image`のassetをpreview対象にする。
- `assets.type = video`のassetをpreview対象にする。
- ProResに限定しない。H.264、HEVC、ProResなど、ffmpegが読み込める動画は同じpreview生成対象にする。
- ffmpegが読み込めない形式、破損ファイル、存在しないoriginalはjob失敗として扱う。

### Video Preview

- 動画previewはMP4で生成する。
- Video codecはH.264とする。
- Audioがある場合はAACにする。
- 解像度は1080p上限とし、縦横比を維持する。
- originalは読み取り入力としてのみ使い、上書き・改変しない。

### Image Preview

- 画像previewはJPEGで生成する。
- 長辺2048px上限とし、縦横比を維持する。
- EXIF orientationを反映する。
- originalは読み取り入力としてのみ使い、上書き・改変しない。

### LOG / LUT Preview

- `job_type = lut_preview`の場合、Rec.709変換用LUTを適用して動画previewを生成する。
- LUT fileはbackend Docker imageから読めるrepo管理ファイルとして扱う。
- 正規配置は`backend/assets/lut/rec709.cube`とする。
- Docker image内の既定pathは`/app/assets/lut/rec709.cube`とする。
- `LUT_PATH`環境変数で上書き可能にする。未指定時は`/app/assets/lut/rec709.cube`を使う。
- Dockerfileは`backend/assets/`をimage内`/app/assets/`へcopyする。
- `backend/assets/lut/rec709.cube`はffmpeg `lut3d`で読める有効な`.cube` fileとし、無効なplaceholderは置かない。
- LUT fileが存在しない場合、`lut_preview` jobは`failed`にし、`assets.preview_status = failed`にする。
- `job_type = preview`ではLUTを適用しない。

### Storage

- 生成中のpreviewは`${MEDIA_ROOT}/tmp/`へ一時出力する。
- 確定previewはbackend生成pathで`${MEDIA_ROOT}/previews/`へ移動する。
- `derived_files.path`には`MEDIA_ROOT`からの相対pathを保存する。
- API token、tmp path、host絶対pathをerror/logへ含めない。

### Database

- 成功時に`derived_files`へpreview recordを作成する。
- `derived_files.kind`は`preview`とする。
- 動画previewの`mime_type`は`video/mp4`とする。
- 画像previewの`mime_type`は`image/jpeg`とする。
- 成功時に`jobs.status = done`へ更新する。
- 成功時に`assets.preview_status = preview_ready`へ更新する。
- 失敗時に`jobs.status = failed`へ更新し、`error_message`を保存する。
- 失敗時に`assets.preview_status = failed`へ更新する。

### Docker / Runtime

- backend Docker imageにffmpegを追加する。
- Mac mini運用ではDocker内ffmpegを正とする。
- local testではffmpegがない環境でもunit testが動くよう、ffmpeg呼び出しはadapter/serviceへ分離してmock可能にする。

## Out of Scope

- `GET /assets`
- `GET /assets/{asset_id}`
- `GET /assets/{asset_id}/preview`
- `POST /assets/{asset_id}/preview-confirmation`
- `GET /jobs`
- `GET /jobs/{job_id}`
- Mobile App画面実装。
- preview再生UI。
- preview確認済み更新。
- thumbnail生成。
- 複数解像度preview生成。
- chunk/resume upload。
- end-to-end hash verification。
- `safe_to_delete_candidate`判定。
- original削除。
- 自動削除。
- AI解析job。

## User Flow

### Preview Generation Success

1. Upload APIがoriginalを保存し、asset recordを作成する。
2. Upload APIが`preview`または`lut_preview` jobを`queued`で登録する。
3. Workerが自分の対応job typeとして`preview` / `lut_preview`を持つ。
4. Workerがqueued jobを1件claimし、`running`にする。
5. Workerは`jobs.asset_id`を対象asset idの正本として扱い、`original_path`や`type`は`assets` recordから取得する。
6. Workerが`${MEDIA_ROOT}/originals/...`のoriginalを読み取り入力として扱う。
7. Workerがffmpegで`${MEDIA_ROOT}/tmp/`へpreviewを生成する。
8. Workerがbackend生成pathで`${MEDIA_ROOT}/previews/`へ確定保存する。
9. Workerが`derived_files`へpreview recordを作成する。
10. Workerが`assets.preview_status = preview_ready`にする。
11. Workerが`jobs.status = done`にする。

### Preview Generation Failure

1. Workerがqueued jobをclaimし、`running`にする。
2. original file missing、ffmpeg失敗、LUT missing、storage失敗、DB失敗のいずれかが起きる。
3. Workerは生成途中のtmp previewを削除する。
4. 確定preview保存前に失敗した場合、`derived_files`は作らない。
5. 確定preview保存後にDB失敗した場合、新規preview fileの削除を試みる。
6. 削除できなかった場合は、host絶対pathを含めずに相対pathと安全なerror情報をlogに残す。
7. Workerが`jobs.status = failed`、`assets.preview_status = failed`へ更新する。
8. `jobs.error_message`には運用に必要な短い失敗理由を保存するが、API token、host絶対path、不要な個人情報は含めない。

### Preview Job Re-run

1. Worker異常終了やlease recoveryにより、同じjobが再実行される場合がある。
2. Workerは処理開始時に、同じ`asset_id`かつ`derived_files.kind = preview`のrecordを確認する。
3. 既存preview recordがあり、相対pathが安全で、実fileも存在する場合、新しいpreviewを生成せずに`jobs.status = done`、`assets.preview_status = preview_ready`へ更新する。
4. 既存preview recordがあるが実fileが存在しない場合、重複生成や自動削除はせず、jobを`failed`、`assets.preview_status = failed`へ更新する。
5. Phase 1では既存previewの上書き、複数preview recordの作成、古いpreviewの自動削除を行わない。
6. 確定preview fileが存在するが`derived_files` recordがないorphan previewの検出/清掃は別featureで扱う。

## Functional Requirements

### FR-01 Worker Supported Job Types

- Workerの`supported_job_types`に`preview`と`lut_preview`を含める。
- Workerは`preview` / `lut_preview`だけを通常claimする。
- 未対応job typeは通常workerでclaimしない。
- 既存の明示的なunsupported job failure helperは維持する。

### FR-02 Job Loading

- Workerはclaimしたjobから`asset_id`を取得する。
- `jobs.asset_id`を対象asset idの正本として扱う。
- `assets` recordを`type`、`original_path`、preview生成に必要なmetadataの正本として扱う。
- `payload_json`に`jobs.asset_id`と矛盾する`asset_id`が含まれる場合はjobを`failed`にする。
- Workerは`assets` tableから対象assetを取得する。
- 対象assetが存在しない場合、jobを`failed`にする。
- `original_path`は`MEDIA_ROOT`からの相対pathとして扱う。
- `original_path`がhost絶対pathまたはpath traversalを含む場合は拒否する。

### FR-03 Video Preview Generation

- `assets.type = video`の通常previewをMP4 / H.264 / AACで生成する。
- 最大解像度は1080pとする。
- 縦横比を維持する。
- 出力映像の幅・高さは偶数にする。
- H.264のpixel formatは`yuv420p`にする。
- MP4は`faststart`相当で生成する。
- 動画品質はPhase 1ではCRF 23相当、presetは`veryfast`相当を既定にする。
- 音声がある場合はAAC 128kbps相当を既定にする。
- 音声がない動画でもpreview生成を成功させる。
- ffmpeg adapterは音声streamをoptional mapとして扱う。
- ProRes、HEVC、H.264など入力codecに依存せず、ffmpegが読める動画を対象にする。
- ffmpegが読めない入力はjobを`failed`にする。

### FR-04 Image Preview Generation

- `assets.type = image`のpreviewをJPEGで生成する。
- 長辺2048px上限とする。
- 縦横比を維持する。
- EXIF orientationを反映する。
- JPEG品質はPhase 1では`q:v 3`相当を既定にする。
- ffmpegが読めない入力はjobを`failed`にする。

### FR-05 LUT Preview Generation

- `job_type = lut_preview`では動画previewにRec.709 LUTを適用する。
- LUT fileのrepo内正規配置は`backend/assets/lut/rec709.cube`とする。
- `backend/assets/lut/rec709.cube`はffmpeg `lut3d`で読める有効な`.cube` fileとする。
- Docker image内の既定pathは`/app/assets/lut/rec709.cube`とする。
- `LUT_PATH`環境変数が指定された場合はそのpathを使う。
- LUT fileが存在しない場合はjobを`failed`にする。
- `assets.type != video`のassetに`lut_preview` jobが来た場合はjobを`failed`にする。
- LUT filterはscale/formatと同じffmpeg filter chain内で扱い、最終出力はH.264 MP4 / `yuv420p`にする。

### FR-06 Derived File Record

- preview生成成功時、`derived_files`へrecordを作成する。
- `asset_id`には対象asset idを入れる。
- `kind`は`preview`にする。
- `path`は`MEDIA_ROOT`からの相対pathにする。
- `mime_type`は動画なら`video/mp4`、画像なら`image/jpeg`にする。
- `size_bytes`には生成後preview file sizeを記録する。
- response APIはこのfeatureでは実装しない。

### FR-07 Status Updates

- preview生成開始時は、jobを`running`にする既存claim動作を使う。
- 成功時は`jobs.status = done`にする。
- 成功時は`jobs.error_message = NULL`にする。
- 成功時は`assets.preview_status = preview_ready`にする。
- 失敗時は`jobs.status = failed`にする。
- 失敗時は`jobs.error_message`へ短い失敗理由を保存する。
- 失敗時は`assets.preview_status = failed`にする。

### FR-08 File Safety

- originalを改変しない。
- originalを削除しない。
- 生成中は`${MEDIA_ROOT}/tmp/`を使う。
- 成功時のみ`${MEDIA_ROOT}/previews/`へ確定保存する。
- 失敗時はtmp previewを削除する。
- DB失敗時に確定previewができていた場合は、新規preview fileの削除を試みる。
- preview保存pathはbackendが生成し、client filenameやoriginal filenameには依存しない。
- host絶対pathをDB、job payload、error response、logへ含めない。

### FR-09 ffmpeg Adapter

- ffmpeg呼び出しはworker本体から直接書かず、adapterまたはserviceへ分離する。
- subprocess実行時はargument listを使い、shell文字列を使わない。
- stdout/stderrをそのまま全量DBやlogへ保存しない。
- errorには短くsanitizedされた理由を保存する。
- 保存するerrorは200文字以内にする。
- ffmpeg timeoutはPhase 1では300秒を既定にする。
- Phase 1では自動retryを実装しない。
- timeout時はjobを`failed`にする。
- unit testではffmpeg呼び出しをmockできる。

### FR-10 Docker Image

- backend Docker imageにffmpegをinstallする。
- backend Docker imageに`backend/assets/`をcopyし、`/app/assets/lut/rec709.cube`を読めるようにする。
- `.env.example`に任意設定として`LUT_PATH=/app/assets/lut/rec709.cube`を示す。
- `docker compose build`でapi/worker imageをbuildできる。
- worker serviceは既存通り`restart: unless-stopped`を維持する。

### FR-11 Transaction and Idempotency

- preview file operationとSQLite writeは完全にはatomicにできないため、順序を明確にする。
- 推奨順序は、tmp preview生成、previewsへの確定move、DB transactionで`derived_files`作成・`assets`更新・`jobs`更新とする。
- Workerのclaim transactionはpreview生成開始前に完了させ、ffmpeg実行中にSQLite write transactionを保持しない。
- DB transaction失敗時は、新規確定preview fileの削除を試みる。
- 削除失敗時は、host絶対pathを含めず、相対pathとsafe error情報だけをlogに残す。
- 同じassetに既存`derived_files.kind = preview`があり実fileも存在する場合、新しいpreviewは作らない。
- 同じassetに既存`derived_files.kind = preview`があるが実fileがない場合、jobを`failed`にする。
- orphan preview file検出/清掃は別featureで扱う。

## Non-Functional / Technical Notes

- Backend languageはPython 3.12。
- Dependency managerは`uv`。
- ffmpegはOS packageとしてDocker imageへ入れる。
- Python依存追加は必要最小限にする。
- SQLiteは既存connection helper、migration方式、WAL mode、busy timeoutを継続する。
- Worker、preview service、ffmpeg adapter、repositoryの責務を分離する。
- preview生成はAPI request lifecycleから分離し、workerで実行する。
- original fileはimmutableとして扱う。
- token、host絶対path、tmp path、不要な個人情報をerror/logへ含めない。
- Phase 1は単一worker前提とし、複数workerやqueue system導入は行わない。
- Phase 1は自動retryなしとし、失敗jobの再投入や手動retry APIは別featureで扱う。
- 大容量素材向けのchunk/resumeやexpected hash verificationはPhase 2で扱う。

## Acceptance Criteria

- Workerが`preview` jobをclaimできる。
- Workerが`lut_preview` jobをclaimできる。
- Workerが未対応job typeを通常claimしない。
- 動画assetからMP4 / H.264 / AAC previewが生成される。
- ProRes以外のffmpeg-readable動画もpreview生成対象になる。
- 動画previewは1080p上限、縦横比維持になる。
- 音声なし動画でもpreview生成が成功する。
- 画像assetからJPEG previewが生成される。
- 画像previewは長辺2048px上限、縦横比維持になる。
- LOG動画ではRec.709 LUTが適用される。
- LUT file missing時、`lut_preview` jobが`failed`になる。
- original fileは変更されない。
- preview生成成功時、`derived_files.kind = preview`でrecordが作成される。
- preview生成成功時、`assets.preview_status = preview_ready`になる。
- preview生成成功時、`jobs.status = done`になる。
- preview生成失敗時、`assets.preview_status = failed`になる。
- preview生成失敗時、`jobs.status = failed`になり、短い`error_message`が保存される。
- missing original時、jobが`failed`になり、`assets.preview_status = failed`になる。
- unsafe `original_path`時、jobが`failed`になり、host絶対pathをlog/errorへ含めない。
- 対象assetが存在しない時、jobが`failed`になる。
- image assetに`lut_preview` jobが来た時、jobが`failed`になる。
- ffmpeg timeout時、jobが`failed`になる。
- ffmpeg stderrが長い場合でも、DB/logにはsanitizedされた短いerrorだけを保存する。
- `jobs.error_message`は200文字以内にする。
- 失敗時、tmp preview fileが残らない。
- DB失敗時、新規確定preview fileの削除を試みる。
- 既存preview recordと実fileがあるjob再実行時、新しいpreviewを作らず`done`にできる。
- 既存preview recordがあるが実fileがないjob再実行時、jobが`failed`になる。
- DB、job payload、log、errorにhost絶対pathが含まれない。
- backend testsでworker成功/失敗、repository更新、path safety、ffmpeg adapter command生成、LUT missing、再実行、cleanup failureを確認できる。
- ffmpeg adapter unit testで、動画commandがH.264、AAC optional map、1080p上限、偶数解像度、`yuv420p`、faststart、timeoutを扱うことを確認できる。
- ffmpeg adapter unit testで、画像commandがJPEG、長辺2048px上限、EXIF orientation反映方針を扱うことを確認できる。
- `uv run pytest`が成功する。
- `env API_TOKEN=test-token docker compose config`が成功する。
- Docker Desktopが利用可能な環境で`env API_TOKEN=test-token docker compose build`が成功する。
- Mobile画面、preview取得API、preview確認API、削除処理がこのfeatureに混ざっていない。

## Open Questions

- なし。

## Durable Docs Impact

- 更新候補:
  - `docs/architecture.md`
  - `docs/functional-design.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
- 更新要否:
  - preview失敗時の`assets.preview_status = failed`とbackend内LUT配置は関連docsへ反映済み。
  - ffmpeg adapter構成の詳細は、このfeatureの`plan-feature`または実装時に必要に応じて関連docsへ反映する。
- 理由:
  - preview workerはPhase 1の中心処理であり、status名、LUT配置、Docker ffmpeg、worker責務は長期的な設計判断になるため。
