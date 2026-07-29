# MediaVault リポジトリ構造定義書

## 適用方針

- MobileはExpo managed workflow + JavaScriptのfeature-first構成とする。
- BackendはFastAPIのlayered構成とする。
- originalとderived fileはrepository外の`MEDIA_ROOT`へ保存する。
- Docker関連ファイルはMac mini移行時に追加する。

## プロジェクト構造

```text
project-root/
├── App.jsx
├── index.js
├── app.json
├── eslint.config.js
├── package.json
├── assets/
│   └── ...
├── modules/
│   └── streaming-sha256/
├── src/
│   ├── application/
│   │   ├── navigation/
│   │   ├── providers/
│   │   └── theme/
│   ├── features/
│   │   ├── settings/
│   │   ├── asset-picker/
│   │   ├── processed-results/
│   │   ├── managed-renditions/
│   │   ├── original-deletion/
│   │   ├── assets/
│   │   └── preview-review/
│   └── shared/
│       ├── api/
│       ├── components/
│       ├── constants/
│       ├── services/
│       ├── test-support/
│       └── utils/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── workers/
│   ├── assets/
│   │   ├── detectors/apple-log-v1/
│   │   └── lut/presets/
│   ├── scripts/
│   ├── tests/
│   ├── pyproject.toml
│   ├── uv.lock
│   └── .env.example
├── ios/
├── scripts/
│   └── verify-ios-native-config.mjs
├── docker-compose.yml
├── docs/
├── docs/ideas/
├── .agents/
├── .steering/
└── .devcontainer/
```

## Mobile構造

### root `eslint.config.js`

- Expo SDK 54のflat configを基礎に、Mobile production/testとmaintained root JavaScriptのlint policyを所有する。
- Jest globalはtest/setup、Node globalはlint configへ限定し、generated output、Backend、`.agents/`、`.steering/`をglobal ignoreする。
- Backend Python lintの責務は持たず、Backend品質commandは`backend/pyproject.toml`側で管理する。

### `src/application/`

- navigation、provider、themeなどアプリ全体の組み立てを置く。
- feature固有の業務処理を持たない。
- Expo Routerを使わない限り、Expo CLIのrouter root誤認を避けるため`src/app/`は使わない。

### `src/features/[feature]/screens/`

- 画面描画とユーザー操作受付を担当する。
- Expo APIやHTTP APIを直接呼ばず、hook/service経由にする。

### `src/features/[feature]/hooks/`

- 画面状態、非同期処理、状態遷移を調停する。
- API client、platform serviceを呼び出す。
- preview確認後のiPhone側original手動削除は、screenではなくhookで条件判定し、shared serviceへ委譲する。
- `processed-results/hooks/`は処理済みvideoのdownload、verify、photo-library save状態と起動時temporary file cleanupを調停する。source original削除hookから参照しない。

### `src/features/[feature]/components/`

- feature固有UIを置く。
- screenをimportしない。

### `src/shared/api/`

- Backend URL、Authorizationヘッダー、API response処理を集約する。
- 初期リリースの1つのBackend URLに対するAuthorization、`capabilities`、preset一覧、
  preview response処理を集約する。
- `src/shared/services/backendEndpointPolicy.js`をURL classification/normalizationの正本とし、
  rejected URLではheader構築とnetwork adapter呼び出しを行わない。
- Tokenをログ出力しない。
- processed result metadataをsanitizeし、validated asset/result IDからcanonical relative delivery pathを再構築する。response URLをそのまま認証付きrequestへ渡さない。

### `src/shared/services/`

- `expo-media-library`、通常設定保存、`expo-secure-store`など端末依存処理を集約する。
- 1つのURLは通常設定保存領域、1つの固定APIトークンは既存keyで
  `expo-secure-store`へ保存する。server profile/name/IDは将来機能とする。
- Tailscale接続状態そのものはアプリ内認証として扱わず、固定APIトークンを常に送信する。
- 写真ライブラリ選択、metadata取得、local asset identifier保持、iPhone側original手動削除要求は`mediaLibraryService.js`へ閉じ込める。
- `original-deletion/services/originalDeletionEligibility.js`はPhase 1 direct assetと
  Phase 2 session videoの削除eligibilityを副作用なしで判定する。

### `src/features/preview-review/`

- preview再生、内容確認、確認済み更新を担当する。
- 要求・適用preset、色変換状態、Apple Log未変換表示を担当する。
- `preview_status = preview_ready`かつ`review_status = preview_confirmed`のassetだけ、iPhone側original手動削除導線を表示する。
- Backend側original削除APIを呼ばない。

### `src/features/managed-renditions/`

- `services/managedRenditionApi.js`はcapability/catalog/rendition responseをsanitizeし、versioned APIを呼ぶ。
- `services/managedRenditionStore.js`はasset単位のclient request/rendition identityとselection sequenceを独立AsyncStorage namespaceへ保存する。
- `hooks/useManagedRendition.js`はwrite-before-POST、same-ID retry、再起動polling、A/B stale response guard、exact active result再取得を調停する。
- `components/PresetSelector.jsx`はserver-returned preset、明示render command、phase、要求・適用preset、fallback/errorを表示する。Apple Log/Rec.709 labelを推測しない。

## Backend構造

### `backend/app/api/`

- FastAPI routeとrequest/response処理。
- business logic、path生成、ffmpeg呼び出しを直接持たない。
- managed presetは`capabilities.py`、`presets.py`、`renditions.py`へ分け、すべてrouter-level token認証を要求する。

### `backend/app/core/`

- `MEDIA_ROOT`、`USER_LUT_ROOT`、固定APIトークン、管理preset/manifest、preview設定などの環境設定。

### `backend/app/db/`

- SQLite接続、schema初期化、migration関連処理。
- Phase 2B migration SQLはformal/managed provenanceを使うactive-result classifierと
  kind-aware pointer triggerを所有する。steady-state classifierとmanaged pointer transition validatorを
  分離し、legacy preview又は置換された旧managed resultだけをsupersedeする。
- Phase 2B migration CLIはoffline one-shot serviceだけで動作し、`BEGIN IMMEDIATE`取得後に
  schema/markerとdrain条件を再検証してからDDL、backfill、ledgerを同一transactionへ適用する。
- `phase_schema_identity.py`はPhase 2B/2Cのmarker、metadata、column、index、trigger SQL identityを
  closed signalで検証する。`phase2c/009_safe_delete_candidate.sql`はstartup migration外のtrusted
  assets rebuildと11個のauthority保護triggerを所有する。

### `backend/app/models/`, `schemas/`

- 永続化modelとAPI schemaを分離する。

### `backend/app/repositories/`

- assets、derived_files、jobs、processed_results、LUT preset/manifestのDB操作。transaction境界はserviceが所有し、repository helperはcommitしない。

### `backend/scripts/` と process helper

- detector certification host scriptはCompose certifierを`Popen`とbounded stdout/stderr readerで管理し、
  timeout/output超過時のprocess group停止と一意なcontainer名による強制cleanupを所有する。
- `backend/scripts/run_phase2b_formal_preview_migration.py` host wrapperはComposeの旧
  `api`/`worker`非稼働を確認し、`phase2b-migration` profileのone-shot migratorだけを起動する。
- container内`backend/scripts/migrate_phase2b_formal_preview.py`はDB preflight、
  `BEGIN IMMEDIATE`内の再検証、schema/backfill/ledger transactionだけを所有する。
- `run_phase2c_safe_delete_candidate_migration.py`とcontainer内
  `migrate_phase2c_safe_delete_candidate.py`はPhase 2Cのdry-run/applyを分離し、API/worker停止、
  drain、bounded output、failure時の停止維持を担当する。
- `run_safe_delete_candidate_reconciliation.py`とcontainer内
  `reconcile_safe_delete_candidates.py`はnetwork-disabled one-shot serviceでcandidate projectionを
  dry-run/applyする。
- `renditions.py`と`rendition_provenance.py`はrequest/job/result/provenance relationを扱い、finalizerのtransactionを内側でcommitしない。
- formal preview repositoryとrendition repositoryはresult kindを共有flagから推測せず、
  各provenance relationでcurrent authorityを解決する。

### `backend/app/services/`

- upload保存、SHA256計算、Apple Log判定、preset検証、preview生成、path生成、processed result integrity/backfill/finalize/delivery/range stream。
- `processed_result_authority.py`はformal/managed/legacy resultをpersist済みprovenanceで分類し、
  migration、formal finalizer、exact-result deliveryへ同じkind判定を提供する。managed authorityは
  active pointerが指す最新成功ready renditionとし、より新しいfailed/superseded selectionを許容する。
  managed finalizerのpointer切替は別のtransition validatorを使い、current selectionの一意なready targetだけを許可する。
- original非改変ルールを守る。
- managed presetはmanifest/JCS/`.cube`検証、registry分類、no-follow LUT snapshot、rendition作成、専用処理、原子的finalizeをそれぞれ`preset_manifest.py`、`preset_registry.py`、`lut_snapshot.py`、`rendition_creation.py`、`rendition_processing.py`、`rendition_finalizer.py`へ分離する。
- `phase2_rollout.py`はschema、client version、runtimeの評価順と0.2.0/0.3.0 floorを共有する。
  `safe_delete_candidate.py`は4 SQL以下の純粋なrelational evaluator/projection、
  `safe_delete_reconciliation.py`はoperator再評価transactionを所有する。

### `modules/streaming-sha256/`

- Phase 2Aで追加するin-repository Expo Module。iOS/Android native codeでlocal videoのwhole-file/byte-range SHA256をbounded memoryで計算する。
- Expo Goでは使わず、Development Buildでのみ検証・利用する。

### `backend/app/workers/`

- preview、upload finalization、managed rendition jobを明示dispatchして実行する。
- SQLite transactionによるatomic claim、lease、期限切れjob回収を担当する。
- Phase 3+でAI jobを追加する。

### `backend/assets/lut/`

- Backend workerが管理presetとmanifestを読む場所。`presets/{preset-id}/manifest.json`と同directoryの`.cube`として、identity/test LUTなどリポジトリで管理可能な資産だけを置く。
- manifestにはpreset id、source/target profile、version、SHA-256、generatorまたはsource URL、利用条件の参照を記録する。identity LUTをRec.709変換用として扱わない。
- Docker image内では`/app/assets/lut/`として参照する。custom LUTはimageとGitへ含めず、Mac mini側のrepo外`USER_LUT_ROOT`をread-only mountして参照する。任意のLUTをMobileからuploadしない。

### `backend/assets/detectors/`

- `apple-log-v1/detector-rule-input-v1.json`へrepository ownerが人手で作成・承認した判定predicate、根拠、source reference、approval情報を置き、隣接する`.sha256` sidecarへJCS SHA-256を置く。fixture差分又はscriptから判定ruleを生成しない。
- `apple-log-v1/manifest.json`と`certificate-summary.json`へ、rule-input digest、exact ffprobe version/entries、resource limit、fixture SHA-256/期待分類、canonical digestを持つ認証結果を置く。動画本体、local path、raw ffprobe outputは置かない。

### `backend/scripts/`

- `generate_test_luts.py`は17-point identityとred/blue swap test LUT、schema v1 manifestをdeterministicに再生成する。
- generated LUT/manifestはcommitし、generator再実行後の差分とSHA-256をtestで検証する。実user LUTは生成・commitしない。
- `certify_apple_log_detector.py`は人手承認済みrule inputを変更せず、repo外fixtureをpinned Docker ffprobeで検査し、sanitized candidate manifestとpath-free certificate summaryを決定的に生成・再検証する。fixture差分からpredicateを推論せず、認証前のmanifestを有効化せず、media/pathを出力又はcommitしない。
- `validate_image_codecs.py`は`tests/fixtures/image-codecs/`のprovenance/hash/dimensionsを
  strictに検証し、production adapterでHEIC/JPEG/PNGをJPEGへ実decodeする。

### checked-in iOSとroot verifier

- `ios/LatestTemplate/Info.plist`はrelease inputであり、`app.json`と表示名、version、ATSを同期する。
- `scripts/verify-ios-native-config.mjs`は`plutil`でplistをstructured parseし、
  `MediaVault`、Expo/npm/plist/Xcodeの`0.3.0`、ATS `false/true`を固定commandで検証する。

### `backend/pyproject.toml`, `backend/uv.lock`

- BackendのPython依存は`uv`で管理する。
- `pyproject.toml`にruntime/test dependenciesとPython version制約を定義する。
- `uv.lock`は再現性のためcommitする。
- `.venv/`はlocal generated stateとしてcommitしない。
- Backendのtestや起動は原則`uv run ...`で実行する。

## MEDIA_ROOT構造

repository内へ実データを置かない。

```text
${MEDIA_ROOT}/
├── originals/
├── previews/
│   └── renditions/
├── thumbnails/
├── jobs/
└── tmp/
    └── renditions/
```

custom LUTは`${USER_LUT_ROOT}/{preset-id}/manifest.json`とmanifestが指すrelative `.cube`として配置し、`MEDIA_ROOT`のimmutable original・derived fileとは分離する。API/worker containerへ同じread-only mountを渡す。

## 命名規則

| 対象 | 規則 | 例 |
|------|------|-----|
| Mobile screen | `PascalCaseScreen.jsx` | `AssetPickerScreen.jsx` |
| Mobile component | `PascalCase.jsx` | `UploadProgress.jsx` |
| Mobile hook | `useSomething.js` | `useUploadQueue.js` |
| Mobile service | `camelCase.js` | `mediaLibraryService.js` |
| Python module | `snake_case.py` | `preview_service.py` |
| Test | mobileは`*.test.js(x)`、backendは`test_*.py` | `uploadAsset.test.js`, `test_upload.py` |

## 依存関係ルール

```text
mobile screens -> hooks -> shared api / platform services
backend api -> services -> repositories -> db
backend workers -> services -> repositories -> db
```

禁止事項:

- Mobile screenからExpo APIやHTTP clientを直接呼ぶ。
- Backend routeからffmpegを直接呼ぶ。
- クライアント由来pathを保存先に使う。
- Tailscale接続を理由に固定APIトークン送信や認証処理を省略する。
- 公開インターネット向けHTTP endpointをPhase 1の接続先にする。
- repository内へoriginalやpreviewを保存する。
- iPhone側original削除を自動実行する。
- iPhone側original削除とBackend側original削除を同じservice/APIで扱う。

## Docker配置方針

- `docker/`またはrootにDockerfile/Composeを置く。
- Mac miniのSSD host pathは環境変数でcomposeへ渡す。
- container内`MEDIA_ROOT`へvolume mountする。
- optionalなhost `USER_LUT_ROOT`はcontainerへread-only volume mountし、未設定時はcustom LUT capabilityをfalseにする。
- `node_modules`はcontainer内で作成する。
- Backend Python依存はcontainer内で`uv sync --frozen`相当の手順で解決する。

## ドキュメント配置

- `docs/ideas/initial-requirements.md`: bootstrap spec。
- `docs/ideas/YYYYMMDD_N-[feature-name].md`: 個別仕様。`N` は日付ごとに `1` から採番する連番。
- `docs/*.md`: 長期維持する設計文書。
- `.steering/[YYYYMMDD_N]-[feature-name]/`: feature spec と同じ basename を使う実装計画と進捗。
