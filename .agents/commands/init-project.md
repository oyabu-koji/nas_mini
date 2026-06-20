---
description: Expo managed workflow + JavaScript の初期環境を作成する
---

# init-project

このワークフローは、新規リポジトリに Expo managed workflow の土台を作成する。

`setup-project` より前に実行し、アプリ本体の雛形と開発環境ファイルを整える。

## 実行イメージ

- チャットで「init-projectを実行して」と依頼
- 新規プロジェクトで `package.json`、`app.json`、`App.jsx` などがまだない状態を前提にする

## 手順

1. `AGENTS.md` と `PROJECT_CONTEXT.md` を読み、技術前提を確認する
2. 既存の Expo アプリ初期化済みファイルがあるか確認する
   - `package.json`
   - `app.json`
   - `App.jsx`
3. 未初期化なら、Expo managed workflow + JavaScript の構成でアプリ土台を作成する
4. 以下の共通開発ファイルを整備する
   - `.gitignore`
   - `.nvmrc`
   - `.devcontainer/devcontainer.json` ただし利用は任意とし、今すぐ Docker 利用を前提にしない
5. 起動コマンドと依存追加ポリシーを確認する
   - `npx expo start`
   - Expo 関連依存は `npx expo install`
6. この段階では機能実装を行わない
7. `define-project` で `docs/ideas/initial-requirements.md` を作成し、`setup-project` へ進める準備を整える

## 必須ルール

- このワークフローは `setup-project` より前に実行する
- 作成対象は環境とアプリ土台のみで、要件定義や機能実装は行わない
- TypeScript は導入しない
- Expo SDK や Node バージョンは `AGENTS.md` / `PROJECT_CONTEXT.md` の前提から勝手に変更しない
- `.devcontainer/` は削除しない。現時点で使わなくても将来用の任意設定として保持する

## 完了条件

- Expo managed workflow の最小起動構成が存在すること
- `.gitignore` と `.nvmrc` が存在すること
- `.devcontainer/devcontainer.json` が存在し、必須利用ではないことが明記されていること
- 次に `define-project` で `docs/ideas/initial-requirements.md` を整備し、`setup-project` に進めること
