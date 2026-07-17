# Feature Spec Template

このテンプレートは、追加機能の仕様を `docs/ideas/YYYYMMDD_N-[feature-name].md` として作成するときに使います。

## 役割

- `define-feature` が追加仕様を新規作成するときのベースにする
- `docs/ideas/` に置く追加仕様の正本にする
- `plan-feature` の入力にする
- 安定した仕様になった後、`docs/` 更新要否を判断する材料にする

## 使わないケース

- `docs/ideas/initial-requirements.md` には使わない
- `.steering/` の計画書や進捗メモには使わない

## 命名規則

- 形式: `YYYYMMDD_N-[feature-name].md`
- 例: `20260407_1-login-refresh.md`
- `YYYYMMDD` は仕様を作成または大きく更新した日付
- `N` は同じ日付に新規作成した feature spec の `1` 始まりの連番。既存最大値に `1` を加え、削除済み番号は再利用しない
- `[feature-name]` は英小文字のケバブケースを使う

---

# Feature Spec

## Metadata

- Date: YYYY-MM-DD
- Feature name:
- Status: draft | confirmed
- Related files:

## Background

- なぜこの仕様が必要か
- どの課題を解決するか

## Target Users / Use Cases

- 誰が使うか
- どの場面で使うか

## Scope

- 今回含めること

## Out of Scope

- 今回含めないこと

## User Flow

- ユーザー操作の流れ

## Functional Requirements

- 要件1
- 要件2

## Non-Functional / Technical Notes

- React Native / Expo 前提の制約
- 端末機能、パフォーマンス、オフライン、権限など

## Acceptance Criteria

- 条件1
- 条件2

## Open Questions

- 未決事項1
- 未決事項2

## Durable Docs Impact

- 更新候補:
- 更新要否:
- 理由:
