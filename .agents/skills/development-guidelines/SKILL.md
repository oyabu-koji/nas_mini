---
name: development-guidelines
description: チーム全体で統一された開発プロセスと JavaScript + Expo 実装規約を定義するためのガイドとテンプレート。開発ガイドライン作成時、コード実装時に使用する。
allowed-tools: Read, Write, Edit
---

# 開発ガイドラインスキル

チーム開発に必要な 2 つの要素をカバーします:
1. JavaScript / React Native / Expo 実装規約
2. 開発プロセスと品質ゲート

## 前提条件

開発ガイドライン作成を開始する前に、以下を確認してください:

1. `docs/architecture.md` - 技術スタックとレイヤー境界
2. `docs/repository-structure.md` - ディレクトリ構造

## 既存ドキュメントの優先順位

`docs/development-guidelines.md` がある場合はそれを最優先にし、このスキルは補助資料として使います。

## 出力先

```text
docs/development-guidelines.md
```

## クイックリファレンス

### 実装時
参照: `./guides/implementation.md`

含まれる内容:
- JavaScript / JSDoc 規約
- React Native コンポーネント規約
- hook / service / logic の責務分離
- エラーハンドリング
- unit test / component test

### プロセス確認時
参照: `./guides/process.md`

含まれる内容:
- `.steering/` を使った進行管理
- Git 運用ルール
- PR / レビューの進め方
- 品質ゲート
- `npm run lint` / `npm test` / `npx expo start` の確認手順

### テンプレート
参照: `./template.md`

## 使用シーン別ガイド

### 新規開発時
1. `./guides/implementation.md` で規約確認
2. `./guides/process.md` で作業フロー確認
3. `.steering/[YYYYMMDD_N]-[feature-name]/` を準備
4. `npm run lint`、`npm test`、`npx expo start` を基準に進める

### コードレビュー時
- `./guides/process.md` のレビュー観点を使う
- `./guides/implementation.md` で規約違反を確認する

### テスト設計時
- `./guides/implementation.md` の test 実装例を使う
- `./guides/process.md` の品質ゲートと実機確認手順を使う

## チェックリスト

- [ ] JavaScript + Expo 前提が明記されている
- [ ] JSDoc と命名規則が具体例付きで定義されている
- [ ] `npm run lint` / `npm test` / `npx expo start` の確認手順がある
- [ ] component test と実機確認の役割分担が定義されている
- [ ] `.steering/` と `docs/` の更新ルールが記載されている
