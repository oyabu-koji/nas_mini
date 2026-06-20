---
description: 初回セットアップ。6つの永続ドキュメントを対話的に作成する
---

# setup-project

このワークフローは `docs/` の永続ドキュメントを初期作成します。

入力は `docs/ideas/initial-requirements.md` を前提とします。

## 実行イメージ

- チャットで「setup-projectを実行して」と依頼
- 必要なら `$prd-writing` などを明示指定

## 手順

1. `docs/ideas/initial-requirements.md` が存在するか確認する
2. 存在しない場合は停止し、先に `define-project` で初期要件を作成するよう案内する
3. `docs/ideas/initial-requirements.md` を読み、初期要件を整理する
4. `docs/ideas/YYYYMMDD-[feature-name].md` のような追加仕様ファイルは、このコマンドの入力として使わない
5. 利用可能ならドキュメント作成やレビューを背景実行へ委任する
6. `docs/product-requirements.md` を作成し、承認を得る
7. 以降を順に作成する
   - `docs/functional-design.md`
   - `docs/architecture.md`
   - `docs/repository-structure.md`
   - `docs/development-guidelines.md`
   - `docs/glossary.md`
8. 作成したドキュメントの整合性を確認する

## 必須ルール

- `docs/` が空、または6つの永続ドキュメントが不足している場合は、このワークフローを優先する
- `docs/ideas/initial-requirements.md` がない場合は実行せず、先に `define-project` を使う
- 実装や `.steering/` 作成を先に進めてはならない
- 作成完了条件は6ファイルがすべて存在すること

## 完了条件

- 上記6ファイルが作成済みであること
- 次に `define-feature` で個別機能仕様を整理し、`plan-feature` に進めるための論点が明確であること
