---
description: プロジェクト全体の初期要件を docs/ideas/initial-requirements.md に作成または更新する
---

# define-project

このコマンドは、プロジェクト全体の bootstrap spec を `docs/ideas/initial-requirements.md` として作成または更新します。

`define-feature` は個別機能仕様を扱うため、このコマンドでは使いません。

## 対話ルール

- 日本語で対話する
- 必要な確認は短い選択肢付きで行う
- Claude Code など `AskUserQuestion` が使える環境では、それを優先して使う
- `AskUserQuestion` が使えない環境では、同等の選択肢提示を通常の会話で行う

## 入力の考え方

- ざっくりしたアプリのアイデアから始めてよい
- 既存の `docs/ideas/initial-requirements.md` がある場合は更新対象として扱う
- 個別機能の仕様は作らない
- 追加機能の仕様テンプレート `.agents/templates/feature-spec-template.md` は使わない

## 手順

1. `AGENTS.md` と `PROJECT_CONTEXT.md` を確認する
2. `docs/ideas/initial-requirements.md` が存在する場合は読み、存在しない場合は新規作成対象として扱う
3. プロジェクト全体の目的、対象ユーザー、スコープ、画面、技術制約を整理する
4. 必要な不足情報を日本語で確認する
5. `docs/ideas/initial-requirements.md` を作成または更新する
6. 次に `setup-project` を実行できる状態か確認する

## 出力先

- `docs/ideas/initial-requirements.md`

## 記載内容

- Project Overview
- Users
- Product Goals
- In Scope
- Out of Scope
- Screens
- Technical Constraints
- Development Rules
- Device or Platform Features
- Acceptance Criteria
- Open Questions

## 重要ルール

- `docs/ideas/initial-requirements.md` は `setup-project` の入力として扱う
- `docs/ideas/YYYYMMDD-[feature-name].md` は作成しない
- `.steering/` はこのコマンドで作らない
- コード実装は行わない
- React Native + Expo managed workflow + JavaScript の前提を維持する
- Expo SDK と Node バージョンは自動で変更しない

## 完了条件

- `docs/ideas/initial-requirements.md` がプロジェクト全体の初期要件として整理されている
- `setup-project` に進める状態になっている
