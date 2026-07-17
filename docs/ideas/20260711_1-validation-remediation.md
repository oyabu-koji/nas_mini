# Feature Spec

## Metadata

- Date: 2026-07-11
- Feature name: validation-remediation
- Status: confirmed
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/ideas/20260622_1-backend-upload-api.md`
  - `docs/ideas/20260624_1-backend-preview-worker.md`
  - `docs/ideas/20260628_1-frontend-mvp-upload-preview-confirmation.md`
  - `.steering/20260622_1-backend-upload-api/`
  - `.steering/20260624_1-backend-preview-worker/`
  - `.steering/20260628_1-frontend-mvp-upload-preview-confirmation/`

## Background

`validate-implementation` 相当の実装レビューで、Phase 1 MVP の upload、preview job、LOG preview、metadata 契約に重大な不整合が見つかった。

- 現在の `backend/assets/lut/rec709.cube` は identity LUT であり、LOG 素材を Rec.709 に変換しない。
- Mobile の共通 API timeout が 15 秒のため、上限内の 100MB upload も LAN/Tailscale の実効速度次第で中断される。
- Backend が upload を成功させた後の local mapping 保存失敗を、Mobile が upload 失敗として扱う。その後に再送すると duplicate asset を作り得る。
- `payload_json` が object でない、または `asset_id` が整数でない job は worker の例外処理を抜け、`running` のまま lease recovery まで残る。
- `taken_at` は ISO 8601 契約だが、Mobile は EXIF の非 ISO 日時または offset 単体を送信でき、Backend は任意文字列を保存する。

この feature は、既存の Phase 1 契約を壊さずにこれらの問題を修正する。Apple Log の自動判別、Apple Log to Rec.709 の正式 LUT 選定、profile ごとの色変換は別 feature として扱う。

## Target Users / Use Cases

- iPhone ユーザーとして、100MB 以下の素材を private LAN/Tailscale 経由で、通常の回線速度でも upload したい。
- iPhone ユーザーとして、upload が成功した素材を、端末内の補助データ保存失敗によって失敗扱いにされたくない。
- iPhone ユーザーとして、変換されていない LOG preview を Rec.709 変換済みと誤認したくない。
- 開発者として、不正な preview job が terminal state へ確実に遷移し、`running` job が不必要に残らないようにしたい。
- 開発者として、撮影日時を API 契約どおりの形式で保存し、後続の一覧・削除確認で信頼できる metadata を使いたい。

## Scope

### LOG Preview Safety

- identity LUT を Rec.709 変換用として使用しない。
- `is_log = true` で登録された既存または新規の `lut_preview` job は、Apple Log feature が提供する正式な変換実装が入るまで、成功した色変換 preview を生成しない。
- 暫定期間は worker が `lut_preview` job を sanitized な理由で `failed` にし、対象 asset の `preview_status` を `failed` にする。
- migration は既存の `is_log = 1` かつ `preview_status = preview_ready` の asset を一括で `preview_status = failed`、`review_status = not_reviewed` に更新する。LUT provenance を保存していないため、custom `LUT_PATH` で生成した可能性があるものも安全側で失効する。既存の `derived_files` と `jobs.status = done` は監査用に保持し、derived file を自動削除しない。
- preview stream は `preview_status = failed` の asset を配信しない。これにより、migration 前に identity LUT で作られた preview は確認・削除導線に使えない。
- migration は条件付き update による一回限りかつ冪等な SQL migration とし、対象件数を token、host path、filename を含めずに記録する。
- 通常の `preview` job には LUT を適用せず、既存の画像・通常動画 preview 処理を維持する。
- 暫定失敗の message に host absolute path、API token、ffmpeg stderr、元ファイル URI を含めない。

### Upload Timeout

- API client の通常 request timeout と upload timeout を分離する。
- `POST /assets/upload` は Mobile 側で 10 分の timeout を使用する。通常の JSON API は既存の 15 秒 timeout を維持する。
- upload timeout は backend 保存結果が不明な `result_unknown` として扱う。Mobile は自動再送せず、同じ選択素材の upload 操作を無効にする。
- `result_unknown` は token、URI、filenameを含めずに端末へ永続化する。local asset id がある場合はその値をキーに復元し、同じ素材の再送を抑止する。local asset id がない場合は、確認前の新規upload全体を抑止する安全側の global pending marker を復元する。
- `result_unknown` のユーザーには asset 一覧を更新して結果を確認する導線を出す。ユーザーが「一覧を確認済み」と明示し、選択素材または global pending marker を破棄した後だけ upload を再開できる。
- idempotency key と server-side duplicate detection は API 契約を広げるため今回の対象外とし、timeout 後に同一 request を自動または即時に再送してはならない。
- chunk/resume upload、upload progress 表示、background upload、無制限 timeout は今回実装しない。

### Upload Success and Local Mapping

- `POST /assets/upload` が成功した時点で、Mobile は backend asset を upload 成功として扱う。
- local asset id と backend asset id の mapping 保存は best-effort とする。
- mapping 保存が失敗しても、asset detail への遷移、upload success 表示、後続の preview 確認を妨げない。
- mapping 保存失敗を API upload 失敗として表示しない。
- mapping 保存失敗時に API token、local file URI、host path を画面または log に出さない。
- `getLocalAssetMapping` が null の場合は、永続化しない派生local状態 `mapping_unavailable` として扱う。後続のiPhone側original削除featureは削除操作を表示せず、非ブロッキングな理由を表示する。backend asset id やfilenameからlocal assetを推測しない。

### Preview Job Payload Validation

- `jobs.asset_id` は preview job の target asset に関する唯一の source of truth とする。
- `payload_json` が未指定または空の場合は、`jobs.asset_id` を使って従来どおり処理する。
- `payload_json` が指定された場合は JSON object として検証する。
- `payload_json.asset_id` が存在する場合は JSON number の整数値として検証し、boolean、float、文字列数値を拒否したうえで `jobs.asset_id` と一致することを確認する。
- malformed JSON、JSON object 以外、契約外の `asset_id`、または不一致の `asset_id` は preview job の terminal failure とする。
- terminal failure 時は `jobs.status = failed`、`jobs.error_message` は 200 文字以内の sanitized reason、asset が存在する場合は `assets.preview_status = failed` とする。
- terminal failure の job と asset status は同一SQLite transactionで更新する。asset が存在しない場合も job failure を単一transactionで確定する。
- worker loop の広域例外に任せて lease recovery を待つ状態を作らない。

### `taken_at` Contract

- upload request の `taken_at` は空文字または、秒精度の `YYYY-MM-DDTHH:mm:ss`、`YYYY-MM-DDTHH:mm:ssZ`、`YYYY-MM-DDTHH:mm:ss+HH:mm`、`YYYY-MM-DDTHH:mm:ss-HH:mm` のいずれかのみ許可する。
- Mobile は EXIF `DateTimeOriginal` / `DateTime` が厳密な `YYYY:MM:DD HH:mm:ss` の場合だけ、`YYYY-MM-DDTHH:mm:ss` に変換して送信する。
- EXIF datetime に有効な `OffsetTimeOriginal` (`Z` または `+HH:mm` / `-HH:mm`) がある場合は offset を付ける。offset がない EXIF datetime は撮影地の timezone を推測せず、offset なしの local wall time として保存する。
- offset 単体、date-only、space 区切り、秒未満、文字列 `null`、形式不正な日時、変換不能な値は API で受理しない。Mobile は正規化不能な値を空文字として送る。
- Backend は非空の `taken_at` を暦上も検証し、契約外の値は `422 Unprocessable Content` を返す。
- Backend は秒精度の canonical text を `assets.taken_at` へ保存し、UTC の `Z` は `+00:00` に正規化する。既存 record の一括 migration は今回行わない。

### Automated Tests and Verification

- Backend test に identity LUT を `lut_preview` で成功扱いにしないことを追加する。
- Backend migration test に、既存の identity LUT `preview_ready` asset が preview stream、preview confirmation、削除導線の対象外になることを追加する。
- Backend test に malformed JSON、JSON array、非整数 `payload_json.asset_id`、不一致 `asset_id` の terminal failure を追加する。
- Backend test に terminal failure の job/asset status update が同一transactionでrollbackされることを追加する。
- Backend API test に offset あり/なしの有効な `taken_at`、date-only、space 区切り、offset-only、`null`文字列、暦上不正な日時の受理/`422` を追加する。
- Mobile の軽量 unit test 実行基盤を追加し、upload timeout 選択、再起動後に復元される `result_unknown` と再送抑止・明示解除、EXIF datetime 正規化、mapping 保存失敗後の success 遷移を自動検証する。
- Mobile component test または実機確認で、LOG安全ゲートのassetが `failed` と表示され、preview再生、confirmation、iPhone側original削除導線を表示しないことを確認する。
- `npm test` を package script として提供する。lint 導入は今回の必須範囲に含めない。
- Backend test、Mobile test、Expo dependency check、iOS export を実行する。
- backend と worker を起動し、iPhone 実機または Development Build で通常素材の select -> upload -> preview -> confirmation を手動確認する。

## Out of Scope

- Apple Log の自動判別。
- Apple Log to Rec.709 の正式 LUT 選定、ライセンス確認、bundle、色変換の有効性検証。
- camera/log profile ごとの LUT 選択。
- `is_log` の永続データモデルを Apple Log profile へ拡張すること。
- `lut_preview` の成功再有効化。これは後続の Apple Log feature で扱う。
- 100MB 上限の変更。
- chunk upload、resume upload、upload progress、background upload、cancel API。
- iPhone 側 original 削除、`local_delete_status` の実装。
- `/jobs` API または job 画面の追加。
- 既存 `assets.taken_at` の bulk migration。
- preview retry API、orphan file cleanup、AI job。

## User Flow

### Normal Upload on a Slow Private Network

1. ユーザーが 100MB 以下の通常写真または動画を選択する。
2. Mobile は metadata を正規化し、`taken_at` を取得できない場合は空として送る。
3. ユーザーが upload を開始する。
4. Mobile は upload 専用の 10 分 timeout で `POST /assets/upload` を待機する。
5. Backend が `201 Created` を返したら、Mobile は upload 成功を表示して asset detail へ遷移する。
6. local mapping の保存に失敗しても、asset detail と preview 確認は継続できる。

### Upload Timeout With Unknown Result

1. 10 分を超えても upload response を受け取れない場合、Mobile は request を abort する。
2. Mobile は backend の保存結果を断定せず、選択素材を `result_unknown` として表示する。
3. Mobile は local asset id がある場合はその値をキーに、ない場合はglobal pending markerとして状態を永続化する。
4. アプリ再起動後もMobileは状態を復元し、同じ素材またはglobal pending markerの間はuploadを無効にする。
5. ユーザーは asset 一覧の更新で保存済み asset を確認する。
6. ユーザーが「一覧を確認済み」と明示して状態を破棄した場合だけ、新たな upload 操作を選択できる。

### LOG Upload During the Safety Gate

1. ユーザーが LOG 素材として upload する。
2. Backend は original を保存し、`lut_preview` job を登録する。
3. Worker は正式な Apple Log 変換が未実装であることを sanitized な job error として記録する。
4. Worker は job と asset preview を `failed` にする。
5. Mobile は preview 失敗として表示し、変換済み Rec.709 preview を表示しない。

### Existing Identity LUT Preview Invalidation

1. deployment 時の SQL migration が既存の `is_log = 1` かつ `preview_ready` asset を検出する。
2. migration は asset の `preview_status` を `failed`、`review_status` を `not_reviewed` に更新する。
3. 既存 preview file と derived file record は削除しないが、asset status により preview stream と confirmation は拒否される。
4. Mobile は preview 失敗を表示し、確認済みや削除可能として表示しない。

### Missing Local Mapping

1. backend upload は成功するが、local mapping の保存または取得に失敗する。
2. Mobile は asset detail と preview確認を継続し、upload失敗として表示しない。
3. 将来のiPhone側original削除featureは `mapping_unavailable` を導出し、削除操作を表示しない。
4. Mobile は backend asset id、filename、URIからlocal assetを推測または検索しない。

### Malformed Preview Job

1. Worker が claimed preview job を受け取る。
2. `jobs.asset_id` を target asset として読む。
3. `payload_json` が指定されている場合、object と optional `asset_id` の型・一致を検証する。
4. 検証に失敗した場合、worker は job と対象 asset を `failed` に更新する。
5. worker loop は次の job を処理できる状態を維持する。

## Functional Requirements

### FR-01 LOG Preview Safety

- `lut_preview` は identity LUT を使って成功してはならない。
- 正式な Apple Log feature 導入前の `lut_preview` は、明確かつ sanitized な failure とする。
- 既存の identity LUT output を持つ LOG asset は migration で `preview_status = failed`、`review_status = not_reviewed` にする。
- 既存の derived preview は自動削除しないが、`preview_status = failed` のため配信・confirmation・削除導線に使用できない。
- failure は original を変更、削除、移動しない。
- `preview` job の既存の成功経路を変えない。

### FR-02 Upload Timeout

- `uploadAsset` は 600,000ms の upload 専用 timeout を request client へ渡す。
- JSON API の default timeout は 15,000ms のままとする。
- timeout は `result_unknown` として表示され、同じ選択素材の upload を自動または即時に再送しない。
- `result_unknown` は再起動後も復元する。local asset idがある場合は同じ素材を、ない場合はglobal pending markerにより確認前のuploadを無効にする。
- `result_unknown` では asset 一覧を確認する導線を表示し、ユーザーが「一覧を確認済み」と明示して状態を破棄するまで upload を無効にする。
- upload 実行中は二重送信を防ぐ。

### FR-03 Upload Outcome and Local Mapping

- backend upload response が成功なら、Mobile の upload status は `uploaded` になる。
- mapping 保存失敗は backend upload response の成功を覆さない。
- backend asset id が存在する場合は mapping の成否にかかわらず `onUploaded` を実行する。
- mapping 保存は backend API の再呼び出しを発生させない。
- mapping が取得できない場合は `mapping_unavailable` を派生local状態として扱う。将来の削除導線は安全に無効化するが、upload・preview・confirmationを妨げない。

### FR-04 Preview Job Failure Handling

- `payload_json` の absent/empty は許容する。
- malformed または契約外の payload は `PreviewProcessingError` 相当の内部エラーへ正規化する。
- claimed job は、処理結果として `done` または `failed` のどちらかになる。
- malformed payload によって job が `running` のまま lease expiry を待つことはない。
- terminal failure では jobと存在するassetのstatusを同一SQLite transactionで更新し、部分更新を残さない。
- job error、API response、worker log に host absolute path、API token、full payload を出さない。

### FR-05 ISO 8601 `taken_at`

- Mobile は `taken_at` を定義済みの秒精度 ISO 8601 datetime または空文字として API client に渡す。
- Backend は `YYYY-MM-DDTHH:mm:ss` と、`Z` または `+HH:mm` / `-HH:mm` を含む同形式だけを受理する。UTC の `Z` は `+00:00` として保存する。
- Backend は non-empty かつ invalid な日時、date-only、space 区切り、offset-only、文字列 `null` を `422` で拒否する。
- metadata が存在しない、または正規化不能な場合は null とし、他の upload metadata は受理する。

### FR-06 Testability

- Mobile の純粋 helper は unit test から import できる。
- test は API token、local file URI、host absolute path を assertion failure output に含めない fixture を使う。
- Backend test は worker の terminal state を SQLite row で検証する。

## Non-Functional / Technical Notes

- Mobile は React Native + Expo managed workflow + JavaScript を維持する。TypeScript を導入しない。
- Backend は Python 3.12、FastAPI、SQLite、uv を維持する。
- route、service、repository、worker、ffmpeg adapter の責務分離を維持する。
- original は immutable とし、暫定 LOG failure を含めて original を削除しない。
- Expo API、AsyncStorage、SecureStore は screen から直接呼ばない。
- API token、host absolute path、tmp path、local file URI、full `payload_json` を log/error/UI に出さない。
- Mobile test dependency は Expo SDK 54 と互換なものを `npx expo install` で追加する。
- worker の terminal failure は lease recovery を正常終了しなかった場合の保険として残すが、入力検証失敗を lease recovery に依存させない。

## Acceptance Criteria

- `backend/assets/lut/rec709.cube` の identity transform を成功する `lut_preview` に使わない。
- `is_log = true` の job は正式な Apple Log 実装前に sanitized な `failed` 状態となり、Rec.709 変換済みと表示されない。
- 既に identity LUT で `preview_ready` となった LOG asset は migration 後に preview配信、confirmation、削除導線の対象外となる。
- 100MB 以下の upload は 15 秒の共通 timeout で abort されず、upload 専用 timeout を使う。
- upload timeout 後は同じ選択素材を自動または即時に再送せず、asset 一覧で結果を確認できる。
- `result_unknown` は再起動後も復元され、一覧確認済みの明示操作まで再送を抑止する。
- backend upload 成功後に local mapping 保存を失敗させても、Mobile は upload 成功を表示し asset detail へ遷移する。
- mapping がないassetは、将来のiPhone側original削除導線を表示しない。
- malformed JSON、JSON array、非整数 `asset_id`、不一致 `asset_id` の preview job は `failed` になり、存在する asset の `preview_status` も `failed` になる。
- malformed payload の job が `running` のまま lease expiry を待たない。
- malformed payloadのterminal failureでjobとassetのstatusが部分更新されない。
- 定義済みの offset あり/なし ISO 8601 `taken_at` は保存され、date-only、space 区切り、offset-only、`null`文字列、暦上不正な non-empty `taken_at` は `422` になる。
- EXIF datetime が正規化不能な場合、Mobile は `taken_at` を null として upload を継続できる。
- `cd backend && uv run pytest` が通る。
- `npm test` が通る。
- `npx expo install --check` が通る。
- `npx expo export --platform ios --output-dir <temporary-directory>` が通る。
- iPhone 実機または Development Build で、通常素材の select -> upload -> preview ready -> playback -> confirmation を確認する。
- iPhone 実機または Development Build で、LOG安全ゲートのassetが `failed` と表示され、preview再生、confirmation、iPhone側original削除導線を表示しないことを確認する。

## Open Questions

- Apple Log feature が導入されるまで、LOG 素材の upload UI で failure 予定をどの文言で表示するか。
- Mobile unit test に使う Expo SDK 54 互換の最小 test dependency 構成。

## Durable Docs Impact

- 更新候補: `docs/product-requirements.md`、`docs/functional-design.md`、`docs/architecture.md`、`docs/repository-structure.md`、`docs/development-guidelines.md`、`docs/glossary.md`。
- 更新要否: 必要。
- 理由: LOG preview を安全ゲート中は生成・配信しないこと、既存 identity LUT preview の失効、`taken_at` の厳密な API契約、upload timeout 後の結果不明状態は、Phase 1 のユーザー体験・API契約・運用ルールに影響する安定した判断である。
