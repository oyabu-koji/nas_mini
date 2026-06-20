# Codex Project Memory

### 日本語説明
このファイルは、AI エージェントが新規プロジェクトで一貫した進め方を取るための運用メモです。

## Technology Stack

- App type: React Native mobile application
- Framework: Expo managed workflow
- Language: JavaScript
- Package manager: npm
- Environment: Node 24, Expo SDK 54
- Start command: `npx expo start`
- Remote device testing: `npx expo start --tunnel`

### 日本語説明
- アプリ種別は React Native のモバイルアプリです。
- 実行基盤は Expo managed workflow を前提にします。
- 実装言語は JavaScript を使います。
- パッケージ管理は npm を想定しています。
- 開発環境は Node 24 と Expo SDK 54 に固定します。
- 通常の起動は `npx expo start`、リモート端末確認は `npx expo start --tunnel` を使います。

## Purpose

This template is the starting point for an AI-driven React Native + Expo + JavaScript project.

### 日本語説明
このテンプレートは、AI 駆動で React Native + Expo + JavaScript の新規プロジェクトを始めるための出発点です。

## Core Workflow

1. Read `PROJECT_CONTEXT.md`
2. Run `init-project` to create the Expo managed workflow baseline
3. Use `define-project` to create or update `docs/ideas/initial-requirements.md`
4. Run `setup-project` to create the six durable docs
5. Use `define-feature` to create or update `docs/ideas/YYYYMMDD-[feature-name].md`
6. Use `plan-feature` with the target `docs/ideas/YYYYMMDD-[feature-name].md` file to create `.steering/[YYYYMMDD]-[task]/`
7. Use `implement-feature` with the target `.steering/...` directory to make changes and update `tasklist.md`
8. Use `validate-implementation` with the same `.steering/...` directory to review the implementation strictly
9. Start the app with `npx expo start`
10. Use `npx expo start --tunnel` when remote device testing is needed

### 日本語説明
基本フローは、まず `PROJECT_CONTEXT.md` を読み、`define-project` でプロジェクト初期要件を整えてから進める形です。
最初に `init-project` で Expo managed workflow の土台を整え、その後 `define-project` と `setup-project` で初期要件と永続ドキュメントを作成します。
追加仕様は `define-feature` で `docs/ideas/YYYYMMDD-[feature-name].md` として管理し、設計は `plan-feature`、実装は `implement-feature`、厳しめの検証は `validate-implementation` を使います。
起動確認は `npx expo start`、リモート端末確認は `npx expo start --tunnel` を使います。

## Working Rules

- Use React Native + Expo managed workflow + JavaScript as the default project assumption
- Do not introduce TypeScript unless explicitly requested
- Do not upgrade Expo SDK automatically
- Do not change the Node version automatically
- Use `npx expo install` for Expo-related dependencies
- Reuse the commands, skills, and review agents provided under `.agents/`
- Keep specs only in `docs/ideas/`
- Keep workflow notes and temporary operational notes out of `docs/ideas/`; use `.agents/workspaces/` for those
- Treat `docs/ideas/initial-requirements.md` as the bootstrap input for `setup-project`
- Treat `docs/ideas/YYYYMMDD-[feature-name].md` as the standard input for `plan-feature`
- Use `define-project` for project-wide bootstrap requirements
- Use `define-feature` only for individual feature specs
- Keep short-term task planning in `.steering/`
- Keep durable product and engineering documentation in `docs/`
- Update `docs/` when stable requirements or architecture decisions change
- Keep `.devcontainer/` in the repository as an optional future-facing setup, even if Docker is not used now

### 日本語説明
- デフォルト前提は React Native + Expo managed workflow + JavaScript です。
- 明示依頼がない限り TypeScript は導入しません。
- Expo SDK や Node のバージョンは自動で変更しません。
- Expo 関連の依存追加や更新では `npx expo install` を使います。
- `.agents/` 配下の command、skill、review agent を再利用します。
- 仕様は `docs/ideas/` にのみ置きます。
- ワークフロー作業メモや一時的な運用メモは `docs/ideas/` に置かず、`.agents/workspaces/` に置きます。
- `docs/ideas/initial-requirements.md` は `setup-project` の入力として扱います。
- `docs/ideas/YYYYMMDD-[feature-name].md` は `plan-feature` の標準入力として扱います。
- プロジェクト全体の初期要件は `define-project` で扱います。
- 個別機能仕様は `define-feature` で扱います。
- 短期タスク管理は `.steering/`、長期的に残す設計文書は `docs/` に置きます。
- 安定した要件や設計判断が変わったら `docs/` を更新します。

## Expected Directories

### 日本語説明
新規プロジェクトでは、以下のディレクトリやファイル群を標準構成として想定します。

### Specs

- `docs/ideas/initial-requirements.md`
- `docs/ideas/YYYYMMDD-[feature-name].md`

#### 日本語説明
`docs/ideas/` は仕様専用ディレクトリです。  
`initial-requirements.md` はプロジェクト全体の初期要件、`YYYYMMDD-[feature-name].md` は追加機能の仕様を表します。
ワークフロー改造メモや一時メモは `.agents/workspaces/` に置きます。

### Durable docs

- `docs/product-requirements.md`
- `docs/functional-design.md`
- `docs/architecture.md`
- `docs/repository-structure.md`
- `docs/development-guidelines.md`
- `docs/glossary.md`

#### 日本語説明
ここにある 6 つの文書は、要件・設計・構成・用語を長期的に管理するための恒久ドキュメントです。

### Task-level planning

- `.steering/[YYYYMMDD]-[task]/requirements.md`
- `.steering/[YYYYMMDD]-[task]/design.md`
- `.steering/[YYYYMMDD]-[task]/tasklist.md`

#### 日本語説明
各タスクごとに `.steering/[YYYYMMDD]-[task]/` を作り、要求整理、設計、進捗管理を分けて記録します。

## Template Notes

- Before `init-project`, this template intentionally does not include application source code or `package-lock.json`
- After `init-project`, the Expo app baseline and `package-lock.json` are expected to exist
- This template intentionally does not include `node_modules`
- This template includes reusable AI workflow configuration under `.agents/`

### 日本語説明
- `init-project` 前の配布テンプレートにはアプリ本体のソースコードや `package-lock.json` を含めません。
- `init-project` 後は Expo アプリの最小構成と `package-lock.json` が存在する前提です。
- `node_modules` は含めません。
- 代わりに `.agents/` 配下へ再利用可能な AI ワークフロー設定を含めています。
