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
│   │   ├── upload-queue/
│   │   ├── asset-detail/
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
│   │   └── lut/
│   ├── tests/
│   ├── pyproject.toml
│   ├── uv.lock
│   └── .env.example
├── docker/
├── docs/
├── docs/ideas/
├── .agents/
├── .steering/
└── .devcontainer/
```

## Mobile構造

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

### `src/features/[feature]/components/`

- feature固有UIを置く。
- screenをimportしない。

### `src/shared/api/`

- Backend URL、Authorizationヘッダー、API response処理を集約する。
- 選択中のserver profileに対応するBackend URL、Authorization、`capabilities`、preset一覧、preview/job response処理を集約する。
- 自宅用のBackend URLはLAN IP、Tailscale IP、MagicDNS名のprivate endpointを扱う。将来のApp Review用profileはHTTPS endpointだけを扱う。
- Tokenをログ出力しない。

### `src/shared/services/`

- `expo-media-library`、通常設定保存、`expo-secure-store`など端末依存処理を集約する。
- server profileのURL・名称は通常設定保存領域、固定APIトークンはserver IDごとに`expo-secure-store`へ保存する。初期リリースでQRコードからtokenをimportしない。
- Tailscale接続状態そのものはアプリ内認証として扱わず、固定APIトークンを常に送信する。
- 写真ライブラリ選択、metadata取得、local asset identifier保持、iPhone側original手動削除要求は`mediaLibraryService.js`へ閉じ込める。

### `src/features/preview-review/`

- preview再生、内容確認、確認済み更新を担当する。
- 要求・適用preset、色変換状態、Apple Log未変換表示を担当する。
- `preview_status = preview_ready`かつ`review_status = preview_confirmed`のassetだけ、iPhone側original手動削除導線を表示する。
- Backend側original削除APIを呼ばない。

## Backend構造

### `backend/app/api/`

- FastAPI routeとrequest/response処理。
- business logic、path生成、ffmpeg呼び出しを直接持たない。

### `backend/app/core/`

- `MEDIA_ROOT`、`USER_LUT_ROOT`、固定APIトークン、管理preset/manifest、preview設定などの環境設定。

### `backend/app/db/`

- SQLite接続、schema初期化、migration関連処理。

### `backend/app/models/`, `schemas/`

- 永続化modelとAPI schemaを分離する。

### `backend/app/repositories/`

- assets、derived_files、jobs、LUT preset/manifestのDB操作。

### `backend/app/services/`

- upload保存、SHA256計算、Apple Log判定、preset検証、preview生成、path生成。
- original非改変ルールを守る。

### `modules/streaming-sha256/`

- Phase 2Aで追加するin-repository Expo Module。iOS/Android native codeでlocal videoのwhole-file/byte-range SHA256をbounded memoryで計算する。
- Expo Goでは使わず、Development Buildでのみ検証・利用する。

### `backend/app/workers/`

- preview job実行。
- SQLite transactionによるatomic claim、lease、期限切れjob回収を担当する。
- Phase 3+でAI jobを追加する。

### `backend/assets/lut/`

- Backend workerが管理presetとmanifestを読む場所。自前生成のApple Log to Rec.709 preset、identity/test LUTなど、リポジトリで管理可能な資産だけを置く。
- manifestにはpreset id、source/target profile、version、SHA-256、generatorまたはsource URL、利用条件の参照を記録する。identity LUTをRec.709変換用として扱わない。
- Docker image内では`/app/assets/lut/`として参照する。custom LUTはimageとGitへ含めず、Mac mini側のrepo外`USER_LUT_ROOT`をread-only mountして参照する。任意のLUTをMobileからuploadしない。

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
├── thumbnails/
├── jobs/
└── tmp/
```

custom LUTは`${USER_LUT_ROOT}/`配下にmanifestとともに配置し、`MEDIA_ROOT`のimmutable original・derived fileとは分離する。

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
- `node_modules`はcontainer内で作成する。
- Backend Python依存はcontainer内で`uv sync --frozen`相当の手順で解決する。

## ドキュメント配置

- `docs/ideas/initial-requirements.md`: bootstrap spec。
- `docs/ideas/YYYYMMDD_N-[feature-name].md`: 個別仕様。`N` は日付ごとに `1` から採番する連番。
- `docs/*.md`: 長期維持する設計文書。
- `.steering/[YYYYMMDD_N]-[feature-name]/`: feature spec と同じ basename を使う実装計画と進捗。
