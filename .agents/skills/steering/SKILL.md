---
name: steering
description: `plan-feature` で作成した作業計画を維持し、`implement-feature` から `validate-implementation` までの進捗を管理するスキル
---

# Steering Skill

## 役割

- `.steering/[YYYYMMDD]-[task]/` を作業単位の正本として扱う
- `implement-feature` 中は `tasklist.md` と実装を同期する
- 実装完了後に同じ `.steering/...` を `validate-implementation` へ引き渡せる状態を保つ

## ワークフロー上の位置

1. `define-project` が `docs/ideas/initial-requirements.md` を作成または更新する
2. `setup-project` が `docs/` の永続ドキュメント6点を作成する
3. `define-feature` が `docs/ideas/YYYYMMDD-[feature-name].md` を作成または更新する
4. `plan-feature <docs/ideas/YYYYMMDD-[feature-name].md>` が `.steering/...` を作成する
5. `implement-feature <.steering/...>` が `tasklist.md` に従って実装する
6. `validate-implementation <.steering/...>` が実装を検証する

## 入力契約

- `docs/ideas/` は仕様専用ディレクトリ
- `docs/ideas/initial-requirements.md` は `define-project` が扱う `setup-project` 用の初期要件
- `docs/ideas/YYYYMMDD-[feature-name].md` は `define-feature` が扱う個別機能仕様
- `plan-feature` に渡すのは `docs/ideas/YYYYMMDD-[feature-name].md`
- `implement-feature` と `validate-implementation` に渡すのは対象 `.steering/[YYYYMMDD]-[task]/`

## 計画時

1. 対象 feature spec と `docs/` の永続ドキュメント6点を確認する
2. `initial-requirements.md` しかない、または `docs/` が未整備なら停止し、先に `setup-project` を案内する
3. `plan-feature` が生成した `requirements.md` / `design.md` / `tasklist.md` を読み、元の feature spec と整合しているか確認する
4. 実装に必要な粒度まで `tasklist.md` を具体化する

## 実装時

1. 変更前に対象 `.steering/...` の3ファイルを読む
2. `tasklist.md` の未完了タスクを1件選ぶ
3. そのタスクだけを実装する
4. 完了したら `tasklist.md` を更新する
5. 設計判断が変わった場合だけ `requirements.md` / `design.md` も更新する
6. 未完了タスクがなくなるまで繰り返す

## 引き継ぎ時

- lint / test / 起動確認など、`tasklist.md` に含めた品質チェック結果を反映する
- 同じ `.steering/...` を `validate-implementation` に明示入力できる状態にする
- 実装検証そのものは `validate-implementation` で行う

## テンプレート

- `.agents/skills/steering/templates/requirements.md`
- `.agents/skills/steering/templates/design.md`
- `.agents/skills/steering/templates/tasklist.md`

## 重要ルール

- `.steering/...` を読まずに実装・調査・レビューを始めない
- `tasklist.md` を更新せずに次のタスクへ進まない
- `initial-requirements.md` から直接 `.steering/` を作らない
- タスクが大きすぎる場合は分割する
- スキップは技術的理由を明記した場合だけ許可する
