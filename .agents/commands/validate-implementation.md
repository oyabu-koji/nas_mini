---
description: 対象 steering ディレクトリに対応する実装を検証する
---

# validate-implementation

引数: 対象 `.steering/[YYYYMMDD_N]-[feature-name]/` ディレクトリ

例: `validate-implementation .steering/20260407_1-login-refresh/`

このコマンドは、指定された `.steering/...` に対応する実装済みコードを検証します。  
実装自体は行わず、`implementation-validator` サブエージェントを使って仕様整合性と品質を確認します。

## 手順

1. 指定された `.steering/...` が存在するか確認する
2. その中の以下を読む
   - `requirements.md`
   - `design.md`
   - `tasklist.md`
3. 関連する `docs/` の永続ドキュメントを読む
4. 対象実装のコードとテストを確認する
5. `implementation-validator` サブエージェントを呼び出す
6. 検証結果をまとめて報告する

## 停止条件

- 引数がない
- 指定された `.steering/...` が存在しない
- `requirements.md` / `design.md` / `tasklist.md` のいずれかが不足している

## サブエージェント呼び出し

```
subagent:
  agent: implementation-validator
  input:
    steering:
      - .steering/[YYYYMMDD_N]-[feature-name]/
```

## 重要ルール

- このコマンドは実装済みコードに対して行う
- 対象 `.steering/...` は明示で受け取る
- コード修正はこのコマンドの主目的ではない

## 完了条件

- `implementation-validator` による検証結果が提示される
- 重大な問題、改善提案、テスト不足領域が整理されている
