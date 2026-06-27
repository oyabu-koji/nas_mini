# MediaVault 開発ガイドライン

## 基本原則

- originalを改変しない。
- 自動削除を実装しない。
- iPhone側original削除は、Mac mini側preview確認後のユーザー明示操作に限る。
- Backend側original削除とiPhone側original削除を混同しない。
- Phase 1 SHA256記録、Phase 2 hash検証、preview内容確認を混同しない。
- 保存先ルートは`MEDIA_ROOT`から解決する。
- Mobile、Backend、workerの責務を分離する。
- 安定した要件変更は`docs/`、個別仕様は`docs/ideas/`、実装計画は`.steering/`へ反映する。

## 開発環境

- Node.js: 24.x
- Expo SDK: 54
- Mobile: React Native + Expo managed workflow + JavaScript
- Backend: Python + FastAPI
- DB: SQLite
- Preview: ffmpeg
- Mac mini運用: Dockerを正規実行環境とする

## 依存追加

- Expo関連依存は`npx expo install`を使う。
- Expo SDKやNode versionは明示依頼なしに変更しない。
- Python依存はbackendの`pyproject.toml`と`uv.lock`で管理し、`uv`で解決する。
- `uv.lock`は再現性のためcommitし、`.venv/`はcommitしない。
- ローカル`node_modules`をDockerへコピーしない。

## コーディング規約

### JavaScript

- JavaScriptを使い、TypeScriptは明示依頼なしに導入しない。
- 公開hook、service、複雑なデータ構造はJSDocで契約を残す。
- 変数/関数は`camelCase`、componentは`PascalCase`、hookは`use`で始める。
- 真偽値は`is`, `has`, `can`, `should`で始める。

### Python

- module、function、variableは`snake_case`、classは`PascalCase`にする。
- route、service、repository、workerの責務を分離する。
- 外部コマンド実行は専用adapter/serviceへ閉じ込める。
- Backendのtest、worker、local server起動は原則`uv run ...`で実行する。

## Mobile実装ルール

- screenからExpo APIやHTTP clientを直接呼ばない。
- `expo-media-library`など端末APIはserviceに閉じ込める。
- API tokenをログ出力しない。
- metadata欠落をエラーにせずnullableとして扱う。
- Phase 1では`104857600 bytes`超過をupload開始前に案内する。
- Backend URLは通常設定保存領域、固定APIトークンは`expo-secure-store`へ保存する。
- iPhone側original削除は自動実行しない。
- iPhone側original削除操作は、`preview_status = preview_ready`かつ`review_status = preview_confirmed`のassetにだけ表示する。
- 削除前に対象asset、filename、撮影日時などを表示し、ユーザーの明示確認を必須にする。
- iPhone側original削除は`expo-media-library` service経由で実行し、screenから端末APIを直接呼ばない。
- Backend側originalやderived fileをMobileの削除操作で削除しない。

## Backend実装ルール

- routeはvalidationとservice呼び出しに集中する。
- クライアント指定pathを保存先として使わない。
- path traversalを防止する。
- upload中ファイルは`tmp/`、確定originalは`originals/`に置く。
- ffmpegはoriginalを読み取り入力とし、derived fileを別パスへ生成する。
- 外部SSD未接続、容量不足、I/O失敗を明示的に扱う。
- `/assets/upload`, `/assets`, `/assets/{asset_id}`, `/assets/{asset_id}/preview`, `/assets/{asset_id}/preview-confirmation`, `/jobs`, `/jobs/{job_id}`は固定APIトークンを要求する。
- API要求は`Authorization: Bearer <token>`形式とする。

## Statusルール

- `transfer_status`: 転送状態のみ。
- `verification_status`: SHA256記録、Phase 2のhash検証状態のみ。
- `preview_status`: preview生成状態のみ。
- `review_status`: ユーザー確認状態のみ。
- `delete_candidate_status`: 安全削除候補状態のみ。
- `local_delete_status`: Mobile側local stateとして、iPhone側original手動削除状態のみ。Backend asset statusではない。
- 単一status列へ再統合しない。

## Jobルール

- jobはSQLiteへ永続化する。
- Phase 1は単一worker、SQLite WAL mode、`busy_timeout = 5000ms`を使う。
- workerはSQLite transactionでjobをatomic claimする。
- workerは処理可能なjob typeだけをclaimし、processor未実装のjobを通常処理でfailedへ落とさない。
- `claimed_at`と`lease_expires_at`で異常終了後のjobを回収する。
- Docker worker serviceは`restart: unless-stopped`で再起動する。
- job種別は`preview`, `lut_preview`から始め、将来AI jobを追加する。
- job失敗時は`error_message`へ運用に必要な情報を保存する。
- API tokenや不要な個人情報をerror/logへ含めない。

## テスト戦略

### Mobile

- unit test: status表示変換、`104857600 bytes`制限、metadata nullable処理。
- component test: Settings、Asset Picker、Upload Queue、Preview Review。
- unit/component test: iPhone側original削除導線がpreview確認後だけ表示されること。
- 実機確認: Development Buildで権限許可/拒否、iCloud-only素材、metadata欠落、ライブラリアクセス、LAN通信、preview再生、削除キャンセルを確認する。

### Backend

- unit test: path生成、SHA256計算、status遷移、token validation。
- API test: upload、一覧、詳細、preview、確認。
- integration test: tmp保存、original確定保存、ffmpeg成功/失敗、SSD未接続、容量不足。

## 品質ゲート

Mobile scriptが定義済みの場合:

```bash
npm run lint
npm test
npx expo install --check
npx expo start
```

Backendのlint/test commandは実装時に確定する。

Backend test commandの標準形:

```bash
cd backend
uv run pytest
```

### Backend ローカル疎通確認

DockerなしでMBA上のbackendを確認する場合は、API serverとworkerを別Terminalで起動する。

API server:

```bash
cd /Users/oyabu/dev/rep/latest_template/backend

MEDIA_ROOT=/private/tmp/mediavault-local-media \
API_TOKEN=test-token \
DATABASE_PATH=/private/tmp/mediavault-local.sqlite3 \
LUT_PATH=/Users/oyabu/dev/rep/latest_template/backend/assets/lut/rec709.cube \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

worker:

```bash
cd /Users/oyabu/dev/rep/latest_template/backend

PATH="/opt/homebrew/bin:$PATH" \
MEDIA_ROOT=/private/tmp/mediavault-local-media \
API_TOKEN=test-token \
DATABASE_PATH=/private/tmp/mediavault-local.sqlite3 \
LUT_PATH=/Users/oyabu/dev/rep/latest_template/backend/assets/lut/rec709.cube \
uv run python -m app.workers.worker
```

Homebrewで入れたffmpegをworkerから見えるようにするため、worker起動時は`PATH="/opt/homebrew/bin:$PATH"`を明示する。

疎通確認:

```bash
curl -H "Authorization: Bearer test-token" http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/assets/upload \
  -H "Authorization: Bearer test-token" \
  -F "file=@/path/to/sample.mp4" \
  -F "type=video" \
  -F "filename=sample.mp4" \
  -F "is_log=false"

curl -H "Authorization: Bearer test-token" http://127.0.0.1:8000/assets/{asset_id}

curl -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8000/assets/{asset_id}/preview \
  -o /private/tmp/preview-check.mp4

curl -X POST \
  -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8000/assets/{asset_id}/preview-confirmation
```

確認観点:

- `/health`が`{"status":"ok"}`を返す。
- upload responseで`preview_status = preview_generating`、jobが`queued`になる。
- worker処理後にasset detailで`preview_status = preview_ready`になる。
- preview取得で`/private/tmp/preview-check.mp4`が作成される。
- confirmation後に`review_status = preview_confirmed`になる。

実行できないcommandがある場合は、理由を`.steering/[task]/tasklist.md`へ残す。

## Git運用

- commitはConventional Commitsを基本とする。
- `.env`、token、実メディア、SQLite実データをcommitしない。
- `docs/ideas/`には仕様だけを置く。
- 一時メモは`.agents/workspaces/`へ置く。

## Definition of Done

- 受け入れ条件を満たす。
- Backend側original非改変、自動削除禁止、手動削除はpreview確認後のみ、Token必須を確認する。
- lint/test/起動確認または未実行理由を記録する。
- `docs/`と`.steering/`を必要に応じて更新する。
- 実装後に`validate-implementation`を実行する。
