# 開発プロセスガイド

## 基本フロー

1. `docs/` の永続ドキュメントを確認する
2. `.steering/[YYYYMMDD_N]-[feature-name]/` を作成または参照する
3. `requirements.md` / `design.md` / `tasklist.md` を整える
4. 実装またはドキュメント更新を行う
5. `npm run lint`、`npm test`、`npx expo start` で確認する
6. `.steering` と必要な `docs/` を更新する

## 日常的な作業サイクル

### 実装開始前

- 対象タスクの受け入れ条件を確認する
- 影響する `docs/` を確認する
- 実機確認が必要かを判断する

### 実装中

- `tasklist.md` を完了単位で更新する
- 要件判断が変わったら `requirements.md` / `design.md` を直す
- ドキュメントと実装を分離して考えず、同一タスクとして扱う

### 実装後

```bash
npm run lint
npm test
npx expo start
```

- lint が通る
- test が通る
- Expo で起動できる
- 必要なら実機またはシミュレータで主要フローを確認する

## Git 運用

### ブランチ

- `main`: 安定状態
- `develop`: 統合作業用
- `feature/<task>`: 機能追加
- `fix/<task>`: 修正
- `docs/<task>`: ドキュメント更新

### コミットメッセージ

Conventional Commits を使います。

```text
feat(game): add game session hook
fix(feedback): guard haptics failure
docs(architecture): define expo platform layer
```

## プルリクエスト前チェック

- [ ] 変更理由を説明できる
- [ ] 受け入れ条件を満たしている
- [ ] `npm run lint` が通る
- [ ] `npm test` が通る
- [ ] `npx expo start` で起動確認している
- [ ] 必要な `docs/` / `.steering/` が更新されている

## コードレビュー観点

### 実装面

- 画面が Expo API を直接持っていないか
- 純粋ロジックが `logic/` に閉じているか
- JSDoc でデータ契約が読めるか
- 無効入力やレースコンディションを防いでいるか

### テスト面

- 純粋ロジックの unit test があるか
- 主要フローの component test があるか
- 実機依存の変更に確認記録があるか

### ドキュメント面

- `docs/` と `.steering/` が更新されているか
- 用語や状態名が既存ドキュメントと揃っているか

## 自動化の考え方

CI に最低限入れるもの:

- `npm run lint`
- `npm test`

ローカルで必ず確認するもの:

- `npx expo start`

## 実機確認が必要なケース

- 音や振動を変更した
- オフライン挙動を変更した
- タップ体験やアニメーションを変更した
- 画面レイアウトを大きく変えた

確認結果は `.steering/[YYYYMMDD_N]-[feature-name]/tasklist.md` に残します。

## 完成チェック

- [ ] `.steering` の対象タスクが最新
- [ ] `docs/` との不整合がない
- [ ] `npm run lint` / `npm test` / `npx expo start` の結果が確認済み
- [ ] 実機確認が必要な変更は記録されている
