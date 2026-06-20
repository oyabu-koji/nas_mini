---
description: 個別機能の仕様を docs/ideas/YYYYMMDD-[feature-name].md に作成または更新する
---

# define-feature

このコマンドは `docs/ideas/YYYYMMDD-[feature-name].md` に置く個別機能仕様を作成または更新します。

`docs/ideas/` は仕様専用ディレクトリです。  
プロジェクト全体の初期要件 `docs/ideas/initial-requirements.md` は `define-project` で作成または更新します。

## 対話ルール

- 日本語で対話する
- 必要な確認は短い選択肢付きで行う
- Claude Code など `AskUserQuestion` が使える環境では、それを優先して使う
- `AskUserQuestion` が使えない環境では、同等の選択肢提示を通常の会話で行う

## 入力の考え方

- ざっくりしたアイデアから始めてよい
- 既存の `docs/ideas/YYYYMMDD-[feature-name].md` を明示された場合は更新対象として扱ってよい
- `docs/ideas/initial-requirements.md` を指定された場合は停止し、`define-project` を使うよう案内する
- 追加仕様を新規作成する場合の命名規則は `docs/ideas/YYYYMMDD-[feature-name].md`
- 追加仕様のテンプレートは `.agents/templates/feature-spec-template.md` を参照する
- このテンプレートは `docs/ideas/initial-requirements.md` には使わない

## 手順

1. `AGENTS.md`、`PROJECT_CONTEXT.md`、`docs/ideas/` を確認し、追加仕様なら `.agents/templates/feature-spec-template.md` も参照する
2. `docs/ideas/initial-requirements.md` が存在するか確認する
3. 存在しない場合は停止し、先に `define-project` で初期要件を作成するよう案内する
4. `docs/` の永続ドキュメント6点が存在するか確認する
5. 不足している場合は停止し、先に `setup-project` を実行するよう案内する
6. 新しい feature spec を作るか、指定された既存 feature spec を更新する
7. 必要な不足情報を日本語で確認する
8. 仕様を `docs/ideas/` に保存する
9. 安定した仕様変更かどうかを確認し、更新対象の永続ドキュメントがあるか検討する

## 追加仕様作成・更新モード

- 出力先は `docs/ideas/YYYYMMDD-[feature-name].md`
- `.agents/templates/feature-spec-template.md` をベースにする
- この spec file は後続の `plan-feature` の入力になる
- 少なくとも以下を整理する
  - 背景
  - 対象ユーザー / ユースケース
  - スコープ
  - スコープ外
  - ユーザーフロー
  - 機能要件
  - 非機能要件 / 技術メモ
  - 受け入れ条件
  - 未決事項
  - `docs/` 更新影響

## 永続ドキュメント更新の扱い

- 仕様を作るたびに、`docs/` の更新要否を検討する
- ただし、実験メモや未確定事項が大きい段階では無理に `docs/` を更新しない
- ユーザー価値、画面フロー、アーキテクチャ、用語、開発ルールなどに安定した変更がある場合は、関連する `docs/` の更新候補として扱う

## 重要ルール

- `docs/ideas/` には仕様のみを置く
- `docs/ideas/initial-requirements.md` は作成・更新しない
- `setup-project` が未完了の場合は個別機能仕様を作成しない
- `.steering/` はこのコマンドで作らない
- コード実装は行わない
- 追加仕様の新規作成時は `YYYYMMDD-[feature-name].md` の命名を守る

## 完了条件

- 個別機能の spec file が `docs/ideas/` に作成または更新されている
- `plan-feature` に渡せる状態になっている
