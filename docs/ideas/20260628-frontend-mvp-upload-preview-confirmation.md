# Feature Spec

## Metadata

- Date: 2026-06-28
- Feature name: frontend-mvp-upload-preview-confirmation
- Status: draft
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
  - `docs/ideas/initial-requirements.md`
  - `docs/ideas/20260622-backend-upload-api.md`
  - `docs/ideas/20260624-backend-preview-worker.md`
  - `docs/ideas/20260625-backend-asset-read-preview-api.md`

## Background

MediaVault Phase 1 MVPでは、iPhoneからMac miniまたは開発中のMBA backendへ写真・動画のoriginalをuploadし、backend側でpreviewを生成し、iPhoneアプリでpreviewを確認する一連の体験を検証する。

Backend側は、通常upload、preview worker、asset list/detail、preview streaming、preview confirmation APIまで実装済みである。次に、Expo React NativeアプリでSettings、素材選択、upload、asset状態確認、preview再生、確認済み更新を行えるようにする必要がある。

このfeatureでは、Frontend MVPとして1ファイルずつのuploadとpreview確認体験を実装する。chunk/resume upload、`/jobs` API表示、iPhone側original削除実行は後続featureに分離する。

## Target Users / Use Cases

- iPhoneユーザーとして、Backend URLと固定APIトークンを設定し、Tailscale private network経由でbackendへ接続したい。
- iPhoneユーザーとして、写真または動画を1つ選択し、Mac miniまたはMBA backendへuploadしたい。
- iPhoneユーザーとして、100MBを超える素材はPhase 1対象外としてupload前に止めたい。
- iPhoneユーザーとして、LOG素材かどうかを手動で指定してuploadしたい。
- iPhoneユーザーとして、upload後のasset状態を確認し、preview生成完了を待ちたい。
- iPhoneユーザーとして、軽量previewをアプリ内で再生または表示し、Mac mini側に保存された内容を確認したい。
- iPhoneユーザーとして、preview確認後に`review_status = preview_confirmed`へ更新したい。
- 開発者として、既存backend APIとつながる最小Frontend MVPを作り、Tailscale経由の実機動作を検証したい。

## Scope

### Settings

- Backend URLを入力・保存する。
- 固定APIトークンを入力・保存する。
- Backend URLはLAN IP、Tailscale IP、MagicDNS名のHTTP private endpointを許容する。
- Backend URLの例として`http://<tailscale-ip>:8000`をplaceholderまたは補助表示に使う。
- 固定APIトークンは`expo-secure-store`に保存する。
- Backend URLは通常設定保存領域に保存する。
- 接続確認操作を提供し、`GET /health`でbackend到達を確認する。
- 接続確認は固定APIトークンの保存有無も検証するが、`/health`自体が認証不要の場合でも、後続APIに使う設定が揃っていることを表示する。

### Asset Picker / Upload

- `expo-media-library`または適切なExpo media picker/service経由で写真・動画を1つ選択する。
- Phase 1では複数選択を実装しない。
- 写真と動画を対象にする。
- 100MB超の素材はupload開始前に拒否する。
- iCloud-onlyなど端末ローカルに実体がない素材は、選択不可またはupload失敗として扱い、分かる範囲で理由を表示する。
- 取得可能なmetadataをupload requestへ含める。
- 取得できないmetadataはnullとして扱う。
- LOG素材かどうかをユーザーがtoggleで指定できる。
- `POST /assets/upload`へmultipart uploadする。
- upload中、成功、失敗を表示する。
- upload成功後は返却されたassetを使ってAsset Detail / Preview Reviewへ進める。

### Asset List / Detail

- `GET /assets`でasset一覧を取得する。
- `GET /assets/{asset_id}`でasset詳細を取得する。
- `/jobs` APIは使わない。
- preview生成状態はassetの`preview_status`で表示する。
- confirmation状態はassetの`review_status`で表示する。
- `preview_status = preview_generating`の場合、手動更新操作でasset detailを再取得できる。
- Phase 1 MVPでは自動pollingは必須にしない。
- `preview_status = failed`の場合、preview生成失敗として表示するが、retry APIはこのfeatureでは実装しない。
- responseに含まれないbackend filesystem pathを推測・表示しない。

### Preview Review

- `preview_status = preview_ready`のassetだけpreview再生/表示を有効にする。
- 動画previewは`GET /assets/{asset_id}/preview`を使い、アプリ内で再生する。
- 第一候補は`expo-video`による認証ヘッダー付きstreaming再生とする。
- 動画sourceには`Authorization: Bearer <token>` headerを付ける。
- backendが対応済みのRange streamingを利用し、シーク可能なpreview確認を目指す。
- `expo-video`またはiOS/Expo制約で認証ヘッダー付きstreamingが不安定な場合は、一時cache download再生へフォールバックできる設計にする。
- フォールバック時もiPhone写真ライブラリへpreviewを保存しない。
- 写真previewは`GET /assets/{asset_id}/preview`を使ってアプリ内表示する。
- 写真表示でも固定APIトークンを送信し、tokenをURL queryへ入れない。
- ユーザーが内容確認した場合、`POST /assets/{asset_id}/preview-confirmation`を呼ぶ。
- confirmation成功後は`review_status = preview_confirmed`として表示する。

### Local State

- Backend URL、固定APIトークン、最後に選択したassetまたはupload結果のUI状態を端末内で扱う。
- upload時に取得できるiPhone写真ライブラリのlocal asset identifierは、将来のiPhone側original手動削除featureに備えて、backend asset idと紐づけ可能なservice/hook境界にする。
- このfeatureでlocal asset identifierを永続保存する場合でも、削除状態の本格管理は行わない。
- このfeatureではiPhone側original削除は実行しない。
- `local_delete_status`の本格実装は後続featureにする。

## Out of Scope

- chunk upload
- resume upload
- chunk hash verification
- 100MB超の大容量ProRes/LOG本番退避
- 複数ファイル同時upload
- background upload
- `/jobs` API利用
- job一覧/詳細画面
- preview retry API利用
- failed previewの再生成操作
- iPhone側original削除の実行
- `local_delete_status`の永続管理
- Backend側original削除
- original再ダウンロード
- safe delete candidate判定
- AI解析
- FCPXML出力
- App Store公開向けの本番認証
- 公開インターネット向けHTTPS endpoint運用

## User Flow

### Initial Settings

1. ユーザーがSettingsを開く。
2. Backend URLに`http://<tailscale-ip>:8000`または`http://<magicdns-name>:8000`を入力する。
3. 固定APIトークンを入力する。
4. アプリはBackend URLを通常設定保存領域へ保存する。
5. アプリは固定APIトークンを`expo-secure-store`へ保存する。
6. ユーザーが接続確認を押す。
7. アプリは`GET /health`を呼び、backend到達可否を表示する。
8. 到達不可の場合は、Tailscale接続、Backend URL、backend起動状態を確認する案内を表示する。

### Upload One Asset

1. ユーザーがAsset Pickerを開く。
2. アプリが写真ライブラリ権限を確認または要求する。
3. ユーザーが写真または動画を1つ選択する。
4. アプリがfile size、type、filename、取得可能metadataを読み取る。
5. file sizeが`104857600 bytes`を超える場合、Phase 1対象外としてupload開始前に止める。
6. ユーザーがLOG素材かどうかをtoggleで指定する。
7. ユーザーがuploadを開始する。
8. アプリは`POST /assets/upload`へmultipart uploadする。
9. upload成功後、返却されたasset idとstatusを表示する。
10. アプリはAsset Detail / Preview Reviewへ遷移または誘導する。

### Wait For Preview

1. Asset Detailが`GET /assets/{asset_id}`を呼ぶ。
2. `preview_status = preview_generating`の場合、preview生成中として表示する。
3. ユーザーが更新操作を行う。
4. アプリがasset detailを再取得する。
5. `preview_status = preview_ready`になったらpreview再生/表示を有効にする。
6. `preview_status = failed`の場合、preview生成失敗として表示する。

### Preview Playback / Display

1. ユーザーがPreview Reviewを開く。
2. アプリがassetの`preview.url`または`/assets/{asset_id}/preview`をBackend URLと結合する。
3. 動画の場合、`expo-video`へ`uri`と`Authorization` headerを含むsourceを渡す。
4. 動画プレイヤーはbackend preview streaming APIからRange requestで必要な範囲を取得する。
5. ユーザーは再生、停止、シーク、fullscreenを使って内容を確認する。
6. 写真の場合、認証付きrequestでpreviewを取得し、アプリ内で表示する。
7. streamingが失敗する場合、アプリは一時cache download再生/表示へフォールバックできる。

### Confirm Preview

1. ユーザーがpreview内容を確認する。
2. ユーザーが確認ボタンを押す。
3. アプリが`POST /assets/{asset_id}/preview-confirmation`を呼ぶ。
4. 成功時、アプリは`review_status = preview_confirmed`として表示する。
5. 失敗時、preview未準備、認証失敗、接続失敗などの分類に応じて表示する。
6. このfeatureではiPhone側original削除は実行しない。

## Functional Requirements

### FR-01 Settings Storage

- Backend URLを入力できる。
- Backend URLは空白を除去して保存する。
- Backend URLは`http://`から始まるprivate endpointを許容する。
- Backend URLは末尾slashの有無に依存せずAPI pathと安全に結合する。
- 固定APIトークンを入力できる。
- 固定APIトークンは`expo-secure-store`へ保存する。
- 固定APIトークンをログ、画面のdebug表示、error detailへ出さない。
- Settings画面は保存済みBackend URLを再表示できる。
- 固定APIトークンは必要に応じて再入力・上書きできる。

### FR-02 Connection Check

- 接続確認操作を提供する。
- 接続確認は`GET /health`を呼ぶ。
- 成功時はbackend到達可能として表示する。
- 失敗時は到達不可、URL不正、network errorのいずれかを表示する。
- Tailscale private endpoint前提として、失敗時にTailscale接続、Backend URL、backend起動状態の確認を促す。
- 接続確認時に固定APIトークン値を表示しない。

### FR-03 API Client

- API clientはSettingsからBackend URLと固定APIトークンを読む。
- `/assets/upload`、`/assets`、`/assets/{asset_id}`、`/assets/{asset_id}/preview`、`/assets/{asset_id}/preview-confirmation`には`Authorization: Bearer <token>`を付ける。
- `401`は固定APIトークン不正または未設定として扱う。
- `413`はPhase 1 size limit超過として扱う。
- `409`はpreview未準備または確認不可として扱う。
- `422`は入力値またはmetadata validation errorとして扱う。
- `404`はassetまたはpreviewが見つからない状態として扱う。
- `416`はpreview Range request不正として扱い、通常のUI操作では発生しにくい再生失敗として表示する。
- `500`はbackend側保存/preview metadata不整合または内部失敗として扱う。
- network errorはBackend URL、Tailscale、backend起動状態の確認を促す。
- response bodyやerrorを扱う際、固定APIトークンをログ出力しない。

### FR-04 Media Selection

- 写真ライブラリ権限を要求できる。
- 権限拒否時はuploadを開始しない。
- 写真または動画を1つ選択できる。
- 選択した素材のfilename、size、typeを取得する。
- 取得可能なtaken_at、latitude、longitude、exif_jsonを取得する。
- 取得できないmetadataはnullとして扱う。
- 端末ローカルに実体がない素材はupload不可またはupload失敗として扱う。
- `104857600 bytes`を超える素材はupload開始前に拒否する。
- ProRes/LOGの自動判定はしない。
- LOG素材かどうかをユーザーがtoggleで指定する。

### FR-05 Upload

- `POST /assets/upload`へmultipart/form-dataでuploadする。
- Fieldsはbackend仕様に合わせ、`file`, `type`, `filename`, `taken_at`, `latitude`, `longitude`, `exif_json`, `is_log`を送る。
- upload中は操作状態を表示する。
- upload成功時はasset id、filename、transfer_status、verification_status、preview_status、review_statusを表示する。
- upload成功responseに`asset.original_path`が含まれる場合でも、画面やログに表示しない。
- upload失敗時は原因に応じて表示する。
- upload成功後、asset detailを開ける。
- Phase 1では複数upload queueは実装しない。

### FR-06 Asset List

- `GET /assets`でasset一覧を取得する。
- 一覧はfilename、type、preview_status、review_status、created_atを表示する。
- paginationはbackend defaultを使い、MVPでは追加読み込みを必須にしない。
- 手動更新操作で一覧を再取得できる。
- `original_path`やbackend filesystem pathは表示しない。
- 一覧itemからAsset Detailへ移動できる。

### FR-07 Asset Detail

- `GET /assets/{asset_id}`でasset詳細を取得する。
- filename、type、size_bytes、server_sha256、taken_at、metadata、is_log、status fieldsを表示する。
- `preview_status`と`review_status`を分けて表示する。
- `delete_candidate_status`は表示してよいが、Phase 1では削除可能表示として扱わない。
- `preview_status = preview_generating`の場合は生成中として表示する。
- `preview_status = preview_ready`の場合はPreview Reviewへ進める。
- `preview_status = failed`の場合はpreview生成失敗として表示する。
- 手動更新操作でdetailを再取得できる。
- `/jobs` APIは呼ばない。

### FR-08 Preview Video Playback

- 動画previewは`expo-video`を使ってアプリ内再生する。
- `VideoSource` objectに`uri`と`headers.Authorization = Bearer <token>`を設定する。
- `uri`はBackend URLと`/assets/{asset_id}/preview`を結合したものにする。
- native controlsまたは同等の操作で再生、停止、シーク、fullscreenを提供する。
- `preview_status != preview_ready`の場合は再生を開始しない。
- playback error時は再生失敗として表示する。
- header付きstreamingが実機で不安定な場合に備え、一時cache download再生へ切り替えられるservice境界にする。

### FR-09 Preview Image Display

- 写真previewは`GET /assets/{asset_id}/preview`を固定APIトークン付きで取得して表示する。
- 認証情報をURL queryへ入れない。
- 必要であれば一時cache fileとして保存し、Image componentへ渡す。
- iPhone写真ライブラリへpreviewを保存しない。
- 表示失敗時はpreview取得失敗として表示する。

### FR-10 Preview Confirmation

- `preview_status = preview_ready`のassetだけ確認操作を有効にする。
- ユーザー操作で`POST /assets/{asset_id}/preview-confirmation`を呼ぶ。
- 成功時は`review_status = preview_confirmed`を表示する。
- すでに`preview_confirmed`の場合も確認済みとして扱う。
- confirmation失敗時は状態を壊さず、手動更新または再試行を促す。
- confirmationはiPhone側original削除を実行しない。

### FR-11 iPhone-side Original Delete Deferral

- このfeatureではiPhone側original削除を実行しない。
- preview確認済み後も削除ボタンを本実装しない。
- 後続featureで削除導線を実装できるよう、upload時のlocal asset identifierとbackend asset idを紐づけられる設計余地を残す。
- backend asset idはMac mini側保存assetを指し、local asset identifierはiPhone写真ライブラリ内の素材を指すため、Mobile local stateとして分けて扱う。
- 後続のiPhone側original削除featureは、まずFrontend-onlyで実装可能な前提にする。
- 後続featureでもBackend側original、derived file、asset DB recordを削除しない。
- UI文言は、preview確認が「backend保存内容の確認」であり、iPhone側original削除とは別操作であることを混同させない。

## Non-Functional / Technical Notes

### React Native / Expo

- Expo managed workflow + JavaScriptで実装する。
- TypeScriptは導入しない。
- Expo関連依存は`npx expo install`で追加する。
- 想定依存:
  - `expo-secure-store`: 固定APIトークン保存。
  - `expo-media-library`または適切なExpo media picker/service: 写真・動画選択とmetadata取得。
  - `expo-video`: preview動画再生。
  - 必要に応じてcache file管理用のExpo FileSystem系API。
- screenからExpo APIやHTTP clientを直接呼ばず、hook/serviceへ分離する。
- React hooksのcleanupを行い、VideoPlayerを不要に保持しない。
- 一覧は`FlatList`を使う。

### Tailscale / HTTP Private Endpoint

- Phase 1はTailscale private network上のHTTP endpointを許容する。
- Backend URL例は`http://<tailscale-ip>:8000`とする。
- `127.0.0.1`はiPhone自身を指すため、iPhone実機からbackendへ接続するURLとして使わない。
- iPhoneからMBA backendへ接続する場合、backendは`--host 0.0.0.0`で待ち受ける必要がある。
- Tailscaleは通信経路であり、固定APIトークン認証の代替ではない。
- 公開インターネット上のHTTP endpointはPhase 1対象外とする。
- iOS/ExpoでHTTP private endpoint接続にapp configが必要な場合は、plan/implement時に確認して反映する。

### Preview Playback Strategy

- 第一候補は認証ヘッダー付きstreaming再生。
- backend preview streaming APIはRange request対応済みであり、動画のシークを可能にする。
- `expo-video`のsource headersを使って`Authorization`を送る。
- `expo-video`のcache機能は再生補助のLRU cacheとして扱い、明示的に管理する一時download fileとは分ける。
- 認証ヘッダー付きstreamingが実機制約で成立しない場合、Expo FileSystem系APIで一時cache directoryへdownloadして再生する方式へフォールバックする。
- 一時cache download fallbackは削除またはTTL方針を持たせ、永続保存として扱わない。
- fallbackはユーザーに軽量previewを写真ライブラリへ保存したと誤解させない。

### Security

- 固定APIトークンをソースコードへハードコードしない。
- 固定APIトークンをログへ出さない。
- 固定APIトークンをURL query parameterへ入れない。
- Backend URLと固定APIトークンはユーザーがSettingsで入力する。
- responseにbackend filesystem pathが含まれなくても、Mobile側で推測表示しない。

### Performance / UX

- Phase 1 upload size limitは`104857600 bytes`。
- MVPは1ファイルuploadなので、複雑なqueue管理を入れない。
- preview生成中は手動更新を基本にする。
- 動画再生はiPhoneで確認しやすいよう、fullscreenとシークを使える状態にする。
- network error時は、Tailscale接続、Backend URL、backend起動状態を確認する導線を出す。

## Acceptance Criteria

- SettingsでBackend URLを保存できる。
- Settingsで固定APIトークンを`expo-secure-store`へ保存できる。
- `GET /health`で接続確認できる。
- Tailscale IP形式のBackend URLを入力して保存できる。
- 写真ライブラリ権限を要求できる。
- 写真または動画を1つ選択できる。
- 100MB超の素材はupload前に拒否される。
- LOG toggleを指定してuploadできる。
- 100MB以下の動画を`POST /assets/upload`でuploadできる。
- upload requestに`Authorization: Bearer <token>`が付く。
- upload成功後、asset idとstatusを表示できる。
- `GET /assets`でasset一覧を表示できる。
- `GET /assets/{asset_id}`でasset detailを表示できる。
- `/jobs` APIを使わずに`preview_status`でpreview生成状態を表示できる。
- 手動更新でasset detailを再取得できる。
- `preview_status = preview_ready`の動画previewをアプリ内で再生できる。
- 動画preview requestに固定APIトークンが付く。
- 動画previewで再生、停止、シークができる。
- 写真previewをアプリ内で表示できる。
- `POST /assets/{asset_id}/preview-confirmation`で`review_status = preview_confirmed`へ更新できる。
- confirmationはiPhone側original削除を実行しない。
- 固定APIトークンが画面の通常表示、ログ、URL queryに出ない。
- iPhone実機からTailscale private endpointのbackendへ接続確認できる。

## Open Questions

- iOS/Expo SDK 54でHTTP private endpoint接続に必要なapp configの具体設定。
- `expo-media-library`と`expo-image-picker`のどちらを主選択UIにするか。
- 動画previewの認証ヘッダー付きstreamingがiPhone実機で安定するか。
- streaming不安定時の一時cache download再生をMVP実装に含めるか、設計余地だけ残すか。
- 写真preview表示を直接Image URIで扱えるか、一時cache fileを必須にするか。
- upload進捗率をMVPで表示するか、upload中のbusy表示に留めるか。
- local asset identifierをこのfeatureで永続保存するか、後続のiPhone側original削除featureで導入するか。
- Settings接続確認で認証必須APIまで確認するか、`GET /health`到達確認に留めるか。

## Durable Docs Impact

- 更新候補:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`
- 更新要否:
  - 現時点では必須更新なし。ただしiPhone側original削除をPhase 1全体から外したわけではない。
- 理由:
  - このfeatureは既存Phase 1方針のうち、Frontend MVPとしてSettings、1ファイルupload、asset確認、preview再生/表示、preview confirmationまでを具体化する。
  - iPhone側original削除はPhase 1全体の後続featureとして残す。このfeatureで除外するのは「Frontend MVPの実装範囲」であり、durable docsのPhase 1方針変更ではない。
  - Tailscale private endpoint、固定APIトークン、upload、preview確認の方針はすでにdurable docsへ反映済み。
  - 実装時にExpo依存、HTTP app config、preview playback strategyの安定方針が確定した場合は、関連durable docsを更新する。
