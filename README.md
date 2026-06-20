# Project Template

This directory is a reusable base template and initialized Expo baseline for new AI-driven React Native projects.

### 日本語説明
このディレクトリは、AI 駆動で開発する React Native プロジェクト向けの再利用テンプレート兼 Expo 初期構成です。

## Template assumptions

- React Native
- Expo managed workflow
- JavaScript
- Node 24
- Expo SDK 54

### 日本語説明
- React Native を使う前提です。
- Expo managed workflow を採用します。
- 実装言語は JavaScript です。
- Node 24 と Expo SDK 54 を前提にします。

## Included files

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `docs/ideas/`
- `.steering/.gitkeep`
- `.agents/`
- `.gitignore`
- `.nvmrc`
- `.devcontainer/` (optional)
- Expo app baseline files after `init-project`
  - `package.json`
  - `package-lock.json`
  - `app.json`
  - `index.js`
  - `App.jsx`

`.agents/` contains reusable commands, skills, settings, and review agents copied from the current project where they are generic enough for new React Native + Expo + JavaScript projects.

`docs/ideas/` is reserved for product and feature specs only. It contains the bootstrap project spec `initial-requirements.md` created by `define-project` and later feature specs created by `define-feature`.

Before `init-project`, this template intentionally does not include application source code or `package-lock.json`. After `init-project`, the Expo baseline and `package-lock.json` are expected to exist. `node_modules` is never committed.

### 日本語説明
- `AGENTS.md` は AI エージェント向けの運用ルールです。
- `PROJECT_CONTEXT.md` は技術前提と環境制約をまとめた文書です。
- `docs/ideas/` は仕様専用ディレクトリです。
- `docs/ideas/initial-requirements.md` は `define-project` で作成・更新し、プロジェクト全体の初期要件に使います。
- 追加機能の仕様は `define-feature` で `docs/ideas/YYYYMMDD-[feature-name].md` として作成・更新します。
- `.steering/.gitkeep` はタスク単位の計画ディレクトリを保持するためのプレースホルダーです。
- `.agents/` には再利用可能な commands、skills、settings、review agents が入っています。
- `.gitignore` と `.nvmrc` は共通開発環境の前提を揃えるために含めています。
- `.devcontainer/` は将来利用できる任意の開発環境設定です。
- `init-project` 後は Expo の最小起動構成として `package.json`、`package-lock.json`、`app.json`、`index.js`、`App.jsx` が存在します。

`init-project` 前の配布テンプレートにはアプリケーションのソースコードや `package-lock.json` は含めません。`init-project` 後は Expo の最小構成と `package-lock.json` を含めます。`node_modules` は含めません。

## How to start a new project from this template

1. Create a new repository for the project.
2. Copy the contents of `project-template/` into the new repository root.
3. Read `AGENTS.md`, `PROJECT_CONTEXT.md`, and `.agents/README.md`.
4. Run `init-project` to create the Expo managed workflow baseline.
5. Keep the environment pinned to Node 24 and Expo SDK 54.
6. Run `define-project` to create or update `docs/ideas/initial-requirements.md`.
7. Run `setup-project` to create the six durable docs from `docs/ideas/initial-requirements.md`.
8. Run `define-feature` to create or update a feature spec in `docs/ideas/YYYYMMDD-[feature-name].md`.
9. Run `plan-feature` with that `docs/ideas/YYYYMMDD-[feature-name].md` file to create `.steering/[YYYYMMDD]-[task]/`.
10. Run `implement-feature` for the target `.steering/...` directory.
11. Run `validate-implementation` for the same `.steering/...` directory.

### 日本語説明
1. 新しいプロジェクト用のリポジトリを作成します。
2. `project-template/` の中身を新しいリポジトリのルートへコピーします。
3. `AGENTS.md`、`PROJECT_CONTEXT.md`、`.agents/README.md` を読みます。
4. `init-project` を実行して Expo managed workflow の土台を作成します。
5. 開発環境は Node 24 と Expo SDK 54 に固定します。
6. `define-project` を実行して、`docs/ideas/initial-requirements.md` を作成または更新します。
7. `setup-project` を実行して `docs/ideas/initial-requirements.md` から 6 つの永続ドキュメントを作成します。
8. 追加機能に着手する前に、`define-feature` を実行して `docs/ideas/YYYYMMDD-[feature-name].md` を作成または更新します。
9. `plan-feature` に対象の `docs/ideas/YYYYMMDD-[feature-name].md` を渡して `.steering/[YYYYMMDD]-[task]/` を作成します。
10. `implement-feature` で対象 `.steering/...` に従って実装します。
11. `validate-implementation` で同じ `.steering/...` を検証します。

## Development commands

- `npx expo start`
- `npx expo start --tunnel`

### 日本語説明
- `npx expo start` は通常の開発サーバー起動に使います。
- `npx expo start --tunnel` はリモート端末から接続したい場合に使います。

## Environment transfer note

Keep Node 24 for this template and use Docker as the canonical runtime when moving to the Mac mini.

This repository may be developed locally and later moved to a Docker-based Mac mini environment. The Mac mini host Node version is not the source of truth when the app runs inside Docker. Keep `.nvmrc`, `.devcontainer/devcontainer.json`, `AGENTS.md`, `PROJECT_CONTEXT.md`, and this README aligned to Node 24.

Do not copy local `node_modules` into Docker. Install dependencies inside the Docker environment so native packages and package metadata are resolved in the same runtime that builds or runs the app.

### 日本語説明
このテンプレートは Node 24 前提で扱い、Mac mini へ移すときは Docker 内の実行環境を正とします。

ローカルで開発して Docker ベースの Mac mini 環境へ移す場合、Mac mini ホスト側の Node バージョンは Docker 内で実行する限り正本ではありません。`.nvmrc`、`.devcontainer/devcontainer.json`、`AGENTS.md`、`PROJECT_CONTEXT.md`、README の Node 前提は Node 24 で揃えます。

ローカルの `node_modules` は Docker へ持ち込まず、Docker 環境内で `npm install` を実行してください。これにより、ビルドや実行に使う環境と同じ Node/npm 系で依存関係を解決できます。

## Workflow note

Run `init-project` before `setup-project`.

`init-project` prepares the Expo app baseline and shared environment files.

After `init-project`, the next documentation step is `define-project` if `docs/ideas/initial-requirements.md` still needs project-specific content, then `setup-project` to create the six durable docs.

`define-project` is the entry point for creating and updating the project-wide bootstrap spec in `docs/ideas/initial-requirements.md`.

`define-feature` creates or updates individual feature specs in `docs/ideas/YYYYMMDD-[feature-name].md`.

`setup-project` prepares the six durable documents from `docs/ideas/initial-requirements.md`.

If the durable docs below do not exist yet, do not run `define-feature` or `plan-feature` first:

- `docs/product-requirements.md`
- `docs/functional-design.md`
- `docs/architecture.md`
- `docs/repository-structure.md`
- `docs/development-guidelines.md`
- `docs/glossary.md`

`plan-feature` only accepts a feature spec under `docs/ideas/`. It must not be run with `docs/ideas/initial-requirements.md`; use `setup-project` first.

`validate-implementation` is the dedicated validation step after `implement-feature`.

`.devcontainer/` is kept in the template as an optional future-ready setup. It does not mean Docker must be used immediately.

### 日本語説明
`init-project` は `setup-project` より先に実行します。  
`init-project` は Expo アプリの土台と共通環境ファイルを整えます。  
`init-project` 後は、必要に応じて `define-project` で `docs/ideas/initial-requirements.md` をプロジェクト固有の内容に整え、その後 `setup-project` で 6 つの永続ドキュメントを作成します。
`define-project` は `docs/ideas/initial-requirements.md` のプロジェクト初期要件を作成・更新する入口です。
`define-feature` は `docs/ideas/YYYYMMDD-[feature-name].md` の個別機能仕様を作成・更新する入口です。
`setup-project` は `docs/ideas/initial-requirements.md` をもとに 6 つの永続ドキュメントを作ります。  
6 つの永続ドキュメントが未作成の場合は、先に `define-feature` や `plan-feature` を実行せず、`setup-project` を実行します。
`plan-feature` は `docs/ideas/` 配下の追加仕様ファイルを入力に使い、`initial-requirements.md` は受け付けません。  
`validate-implementation` は `implement-feature` の後に使う実装検証専用コマンドです。  
`.devcontainer/` は将来用にテンプレートへ残しますが、現時点で Docker 利用は必須ではありません。

## Dependency policy

- Use `npx expo install` for Expo-related dependencies
- Do not upgrade Expo SDK automatically
- Do not change Node version automatically

### 日本語説明
- Expo 関連の依存追加や更新では `npx expo install` を使います。
- Expo SDK は明示依頼がない限り自動アップグレードしません。
- Node のバージョンも自動では変更しません。
