---
description: docs/ideas/YYYYMMDD-[feature-name].md を入力に steering を作成する（実装は行わない）
---

# plan-feature

引数: `docs/ideas/YYYYMMDD-[feature-name].md` の個別機能仕様ファイル

例: `plan-feature docs/ideas/20260407-login-refresh.md`

このコマンドは `docs/ideas/YYYYMMDD-[feature-name].md` の個別機能仕様をもとに、新機能の**設計のみ**を作成します。
コードの実装は行いません。

---

# 手順

## 1. 入力チェック

- 引数がない場合は停止する
- `docs/ideas/` 配下の候補ファイルを列挙し、対象ファイルを指定するよう案内する
- 指定ファイルが存在しない場合は停止する
- 指定ファイルが `docs/ideas/` 配下にない場合は停止する
- 指定ファイル名が `YYYYMMDD-[feature-name].md` 形式でない場合は停止する
- 指定ファイルが `docs/ideas/initial-requirements.md` の場合は停止する
- `docs/ideas/initial-requirements.md` はプロジェクト初期要件なので、先に `setup-project` を実行するよう案内する

## 2. コンテキスト準備

- `docs/` の永続ドキュメントを読み込む
- `setup-project` 済みで、永続ドキュメント6点が存在するか確認する
- 不足している場合は停止し、`setup-project` を先に実行するよう案内する
- プロジェクトの前提技術を理解する

このプロジェクトの技術スタック

- React Native
- Expo
- JavaScript

---

## 3. 機能の理解

- 指定された `docs/ideas/YYYYMMDD-[feature-name].md` を読み、以下を整理する
- 会話中の短い補足コメントがある場合は考慮してよい
- ただし仕様の正本は `docs/ideas/YYYYMMDD-[feature-name].md` とする

- 機能の目的
- ユーザー操作
- UI構成
- データ構造
- 状態管理

---

## 4. steeringディレクトリ作成

- 仕様ファイル名をもとに対象タスク名を決める
- 以下のディレクトリを作成する

.steering/[YYYYMMDD]-[feature-name]/

その中に次のファイルを作成する

.steering/[YYYYMMDD]-[feature-name]/requirements.md
.steering/[YYYYMMDD]-[feature-name]/design.md
.steering/[YYYYMMDD]-[feature-name]/tasklist.md

---

## 5. requirements.md

記載内容

- 機能の目的
- ユースケース
- ユーザーフロー
- 非機能要件

---

## 6. design.md

以下を整理する

- 画面構成
- コンポーネント設計
- state設計
- hooks設計
- データモデル

React Native / Expo のベストプラクティスを考慮する。

---

## 7. tasklist.md

実装タスクを小さく分解する。

例

- navigation 追加
- screen 作成
- component 作成
- state 管理
- API / storage 接続
- テスト

タスクは **小さく具体的に**分割する。

---

# 重要ルール

このコマンドでは **コードを実装してはいけない**。

`docs/ideas/initial-requirements.md` を入力にしてはいけない。

入力が曖昧な場合は自動推測せず、ユーザーに対象 spec file を指定してもらう。

作成するのは以下の3ファイルのみ

- requirements.md
- design.md
- tasklist.md

---

# 完了条件

- steeringディレクトリが作成されている
- requirements.md が作成されている
- design.md が作成されている
- tasklist.md が作成されている
- 次に `implement-feature` に渡せる状態になっている

このコマンドは **設計完了で終了する**。
