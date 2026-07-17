# リポジトリ構造ガイド

## 目的

`docs/repository-structure.md` は、React Native + Expo + JavaScript で実装を進めるための配置ルールを定義するドキュメントです。  
アーキテクチャの境界を、実際のディレクトリ構造へ落とし込んでください。

## このプロジェクトで固定する前提

- Expo エントリーポイントは `App.jsx`
- UI は `.jsx`、ロジックや service は `.js`
- feature-first で `src/features/<feature>/` にまとめる
- test は `npm test`、起動確認は `npx expo start`

## 必ず含めるディレクトリ

- `assets/`
- `src/app/`
- `src/features/`
- `src/shared/`
- `docs/`
- `.agents/`
- `.steering/`

必要に応じて次も追加します。

- `tests/e2e/`
- `src/shared/test-support/`
- `src/app/navigation/`

## おすすめの分け方

### 1. `src/app/`

- Theme
- Provider
- Navigation
- アプリ全体設定

### 2. `src/features/<feature>/`

- `screens/`
- `components/`
- `hooks/`
- `logic/`
- `services/`
- `types/`
- `__tests__/`

### 3. `src/shared/`

- 複数 feature で再利用する UI
- util
- constants
- test helper

## ファイル命名ルール

- 画面: `PascalCaseScreen.jsx`
- コンポーネント: `PascalCase.jsx`
- hook: `useSomething.js`
- service / util: `camelCase.js`
- JSDoc 定義補助: `*.types.js`
- test: `*.test.js` / `*.test.jsx`

## 配置ルールで明記すること

各ディレクトリごとに、最低でも以下を書きます。

- 役割
- 配置するファイル
- 命名規則
- 依存可能 / 依存禁止

## テスト配置の考え方

- 純粋ロジックは feature 近傍か `logic/` 近傍に置く
- component test は feature 配下の `__tests__/`
- 実機確認結果は `.steering/[YYYYMMDD_N]-[feature-name]/tasklist.md`

## コマンドとの整合

配置方針だけでなく、開発手順も矛盾させないこと。

```bash
npm run lint
npm test
npx expo start
```

## よくある不足

- `App.jsx` がなく、エントリーポイントが曖昧
- feature と shared の境界が曖昧
- `services/` と `logic/` の責務が混ざっている
- test の配置だけ決めて、実機確認の記録先がない

## 完成チェック

- [ ] `.js` / `.jsx` 前提の命名になっている
- [ ] `App.jsx` と Expo 起動前提が書かれている
- [ ] feature / shared / app の境界が明確
- [ ] `npm run lint` / `npm test` / `npx expo start` の流れと矛盾しない
