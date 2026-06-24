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
├── src/
│   ├── app/
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

### `src/app/`

- navigation、provider、themeなどアプリ全体の組み立てを置く。
- feature固有の業務処理を持たない。

### `src/features/[feature]/screens/`

- 画面描画とユーザー操作受付を担当する。
- Expo APIやHTTP APIを直接呼ばず、hook/service経由にする。

### `src/features/[feature]/hooks/`

- 画面状態、非同期処理、状態遷移を調停する。
- API client、platform serviceを呼び出す。

### `src/features/[feature]/components/`

- feature固有UIを置く。
- screenをimportしない。

### `src/shared/api/`

- Backend URL、Authorizationヘッダー、API response処理を集約する。
- Tokenをログ出力しない。

### `src/shared/services/`

- `expo-media-library`、通常設定保存、`expo-secure-store`など端末依存処理を集約する。
- Backend URLは通常設定保存領域、固定APIトークンはPhase 1から`expo-secure-store`へ保存する。

## Backend構造

### `backend/app/api/`

- FastAPI routeとrequest/response処理。
- business logic、path生成、ffmpeg呼び出しを直接持たない。

### `backend/app/core/`

- `MEDIA_ROOT`、API token、LUT path、preview設定などの環境設定。

### `backend/app/db/`

- SQLite接続、schema初期化、migration関連処理。

### `backend/app/models/`, `schemas/`

- 永続化modelとAPI schemaを分離する。

### `backend/app/repositories/`

- assets、derived_files、jobsのDB操作。

### `backend/app/services/`

- upload保存、SHA256計算、preview生成、path生成。
- original非改変ルールを守る。

### `backend/app/workers/`

- preview job実行。
- SQLite transactionによるatomic claim、lease、期限切れjob回収を担当する。
- Phase 3+でAI jobを追加する。

### `backend/assets/lut/`

- Backend workerがLOG preview生成時に使うLUT fileを置く。
- Phase 1の既定LUTは`backend/assets/lut/rec709.cube`とする。
- Docker image内では`/app/assets/lut/rec709.cube`として参照する。

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
- repository内へoriginalやpreviewを保存する。

## Docker配置方針

- `docker/`またはrootにDockerfile/Composeを置く。
- Mac miniのSSD host pathは環境変数でcomposeへ渡す。
- container内`MEDIA_ROOT`へvolume mountする。
- `node_modules`はcontainer内で作成する。
- Backend Python依存はcontainer内で`uv sync --frozen`相当の手順で解決する。

## ドキュメント配置

- `docs/ideas/initial-requirements.md`: bootstrap spec。
- `docs/ideas/YYYYMMDD-[feature].md`: 個別仕様。
- `docs/*.md`: 長期維持する設計文書。
- `.steering/[YYYYMMDD]-[task]/`: 実装計画と進捗。
