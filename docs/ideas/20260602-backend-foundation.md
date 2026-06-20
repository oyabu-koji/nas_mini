# Feature Spec

## Metadata

- Date: 2026-06-02
- Feature name: backend-foundation
- Status: confirmed
- Related files:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - `docs/glossary.md`

## Background

MediaVaultのPhase 1 upload、SHA256記録、preview生成を実装する前に、Mac mini上で安全に動かすbackend基盤が必要である。

このfeatureではFastAPI、SQLite、外部SSD mount、Bearer Token認証、単一worker、job lease recoveryの土台を作る。preview変換処理やMobile App画面は後続featureへ分離する。

## Target Users / Use Cases

- 開発者として、uploadやpreview機能を追加できるbackend土台が欲しい。
- 運用者として、Mac miniの外部SSDを`MEDIA_ROOT`として安全にmountしたい。
- 運用者として、LAN内で認証なしのAPIアクセスを許可したくない。
- 開発者として、ffmpegや将来AI解析jobを安全に処理できるworker基盤が欲しい。

## Scope

### FastAPI baseline

- `backend/`配下にFastAPI app baselineを作る。
- health check APIを用意する。
- Python依存管理方法を定義する。
- backend test baselineを作る。

### Settings

- 環境変数から設定を読む。
- 必須設定:
  - `MEDIA_ROOT`
  - `API_TOKEN`
  - `DATABASE_PATH`
- 初期設定:
  - `SQLITE_BUSY_TIMEOUT_MS=5000`
  - `JOB_LEASE_SECONDS`
- `.env.example`を用意する。
- `.env`と実tokenはcommitしない。

### Storage baseline

- `MEDIA_ROOT`配下に必要なディレクトリを準備する。
  - `originals/`
  - `previews/`
  - `thumbnails/`
  - `jobs/`
  - `tmp/`
- `MEDIA_ROOT`未指定、外部SSD未接続、書き込み不可を明示的なエラーにする。
- クライアント由来pathを保存先に使わない。
- original非改変を後続featureでも守れるservice境界を定義する。

### SQLite baseline

- SQLite DBを初期化する。
- WAL modeを有効にする。
- `busy_timeout = 5000ms`を設定する。
- migration baselineを用意する。
- Phase 1で必要なtableを用意する。
  - `assets`
  - `derived_files`
  - `jobs`
- `jobs`はworker leaseに必要なfieldを持つ。
  - `claimed_at`
  - `lease_expires_at`

### Authentication

- Phase 1 API共通のBearer Token認証dependencyを作る。
- Header形式:
  - `Authorization: Bearer <token>`
- token未指定、不正token、形式不正を拒否する。
- tokenをログへ出力しない。
- health checkを認証対象に含めるかはOpen Questionとする。

### Worker baseline

- APIとは別に単一workerプロセスを起動する。
- workerはSQLite transaction内で`queued` jobを1件だけatomic claimし、`running`へ更新する。
- lease期限切れの`running` jobを`queued`へ回収できる。
- workerは未対応jobを安全に失敗扱いまたはskipできる。
- workerはpreview処理の具体実装をまだ持たない。

### Docker Compose baseline

- Docker Composeで以下のserviceを用意する。
  - `api`
  - `worker`
- `api`と`worker`は同じbackend imageを使う。
- `worker`は`restart: unless-stopped`を使う。
- SQLite DBは永続volumeへ保存する。
- Mac mini外部SSD host pathをcontainer内`MEDIA_ROOT`へbind mountする。
- host pathはハードコードせず、compose環境変数で指定する。
- ローカル`node_modules`をcontainerへ持ち込まない。
- Mobile開発環境のcontainer化はこのfeature対象外とする。

## Out of Scope

- Mobile App画面、Settings画面、`expo-secure-store`実装。
- `expo-media-library`連携。
- original upload API。
- SHA256計算処理。
- ffmpeg preview生成。
- 写真preview生成。
- LUTファイル選定、preview bitrate、ffmpeg retry回数。
- asset一覧、詳細、preview配信、preview確認API。
- chunk upload、resume upload、end-to-end hash verification。
- `safe_to_delete_candidate`判定。
- iPhone内素材の削除。

## User Flow

### Developer setup

1. 開発者が`.env.example`をもとに環境変数を設定する。
2. 開発者が外部SSDまたはローカル検証用directoryのhost pathを指定する。
3. 開発者がDocker Composeを起動する。
4. `api` serviceがSQLiteと`MEDIA_ROOT`を初期化する。
5. `worker` serviceが起動し、job pollを開始する。
6. health checkでbackend基盤が起動していることを確認する。

### Worker claim

1. workerが期限切れ`running` jobを回収する。
2. workerがSQLite transactionを開始する。
3. workerが`queued` jobを1件だけatomic claimする。
4. workerがjobを`running`へ更新し、leaseを設定する。
5. 対応processorがないjobは明示的に処理結果を記録する。

## Functional Requirements

### FR-01 FastAPI起動

- Docker Composeで`api` serviceが起動する。
- health checkで起動状態を確認できる。

### FR-02 設定validation

- `MEDIA_ROOT`, `API_TOKEN`, `DATABASE_PATH`不足時は起動時または初回利用時に明示的に失敗する。
- tokenはログへ出力しない。

### FR-03 MEDIA_ROOT初期化

- 必須directoryを作成または検証する。
- 書き込み不可時は明示的に失敗する。

### FR-04 SQLite初期化

- WAL modeと`busy_timeout = 5000ms`を設定する。
- migration baselineを適用できる。
- `assets`, `derived_files`, `jobs`を作成できる。

### FR-05 Bearer Token認証

- 共通dependencyで`Authorization: Bearer <token>`を検証できる。
- 後続のPhase 1 APIへ再利用できる。

### FR-06 Worker atomic claim

- 単一workerがqueued jobを1件claimできる。
- claim時に`claimed_at`, `lease_expires_at`を更新する。
- 期限切れrunning jobをqueuedへ戻せる。

### FR-07 Docker Compose

- `api`, `worker`が同じbackend imageで起動する。
- DB volumeと`MEDIA_ROOT` bind mountを設定できる。
- host SSD pathを環境変数で切り替えられる。

## Non-Functional / Technical Notes

- Backend language: Python
- API framework: FastAPI
- DB: SQLite
- SQLite:
  - WAL mode
  - `busy_timeout = 5000ms`
  - Phase 1は単一worker
- Docker:
  - Mac mini移行時の正規実行環境
  - host SSD pathは環境変数
  - workerは`restart: unless-stopped`
- Security:
  - Bearer Token必須
  - tokenをログに含めない
  - path traversal防止
- Media:
  - originalは改変しない
  - 自動削除は実装しない

## Acceptance Criteria

- `api`と`worker`をDocker Composeで起動できる。
- health checkが成功する。
- 必須環境変数不足時に明示的なエラーになる。
- `MEDIA_ROOT`配下の必須directoryを準備できる。
- 書き込み不可の`MEDIA_ROOT`を拒否できる。
- SQLiteでWAL modeと`busy_timeout = 5000ms`を確認できる。
- `assets`, `derived_files`, `jobs` tableが存在する。
- Bearer Token認証dependencyをtestできる。
- tokenがログへ出力されない。
- queued jobをatomic claimできる。
- lease期限切れrunning jobをqueuedへ回収できる。
- worker serviceが`restart: unless-stopped`で構成される。
- SSD host pathをcompose環境変数で差し替えられる。
- preview処理、upload処理、Mobile App実装をこのfeatureへ混ぜない。

## Open Questions

- Backend Python versionをどのminorに固定するか。
- ffmpegをbackend imageへ含めるか、preview-generation featureで追加するか。
- health check APIをBearer Token対象外としてよいか。
- `JOB_LEASE_SECONDS`の初期値を何秒にするか。
- migration toolはAlembicを使うか、Phase 1では軽量schema migrationを使うか。
- Mac miniの外部SSD host pathを`.env`でどの変数名にするか。

## Durable Docs Impact

- 更新候補:
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
- 更新要否: 実装設計でOpen Questionsが確定した時点で更新する。
- 理由:
  - Docker Compose構成、Python version、migration方式、lease初期値、SSD host path変数名は安定した運用判断になるため。

