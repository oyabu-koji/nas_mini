---
description: steering の tasklist に従って機能を実装する
---

# implement-feature

引数: 対象 `.steering/[YYYYMMDD]-[feature-name]/` ディレクトリ

例: `implement-feature .steering/20260407-login-refresh/`

このコマンドは `.steering` に作成された設計を元に
機能を実装します。

---

# 手順

## 1. steeringディレクトリ確認

- 指定された `.steering/[YYYYMMDD]-[feature-name]/` が存在するか確認する
- 存在しない場合は停止する
- その中のファイルを読み込む

.steering/[YYYYMMDD]-[feature-name]/requirements.md
.steering/[YYYYMMDD]-[feature-name]/design.md
.steering/[YYYYMMDD]-[feature-name]/tasklist.md

---

## 2. 設計理解

以下を理解する

- 機能の目的
- UI構造
- コンポーネント設計
- 状態管理
- データ構造

このプロジェクトは以下の技術スタックを使用する

- React Native
- Expo
- JavaScript

---

## 3. 実装

tasklist.md に書かれているタスクを
**1つずつ実装する**

重要ルール

- 一度に複数タスクを実装してはいけない
- 1タスク実装したら tasklist.md を更新する
- 必要なら design.md を更新する

---

## 4. 品質確認

実装後に、`package.json` に該当 script がある場合は以下を実行する

npm run lint

`lint` script が未定義の場合は、実行できない理由を `tasklist.md` または報告に残す。

必要に応じて

npx expo start

---

## 5. 次のステップ

このコマンドでは実装検証を完了させない。

実装後は別コマンドの `validate-implementation` に、
同じ `.steering/...` ディレクトリを渡して検証へ進む。

---

# 完了条件

- tasklist.md のタスクが完了している
- `lint` script がある場合は lint エラーがない
- `lint` script がない場合は未実行理由が明記されている
- 必要なら `design.md` と `tasklist.md` が最新化されている
- 次に `validate-implementation` を実行できる状態である
