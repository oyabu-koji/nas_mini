# リポジトリ構造定義書 (Repository Structure Document)

## 適用方針

- 本リポジトリは React Native + Expo + JavaScript を前提にする
- 実装ファイルは `.js` / `.jsx` を基本とする
- feature-first 構成を採用する
- 品質確認は `npm run lint`、`npm test`、`npx expo start` を基本とする

## プロジェクト構造

```text
project-root/
├── App.jsx
├── assets/
│   ├── audio/
│   ├── images/
│   └── icons/
├── src/
│   ├── app/
│   │   ├── navigation/
│   │   ├── providers/
│   │   └── theme/
│   ├── features/
│   │   └── [feature-name]/
│   │       ├── components/
│   │       ├── hooks/
│   │       ├── logic/
│   │       ├── screens/
│   │       ├── services/
│   │       ├── types/
│   │       └── __tests__/
│   └── shared/
│       ├── components/
│       ├── constants/
│       ├── test-support/
│       └── utils/
├── tests/
│   └── e2e/
├── docs/
├── .agents/
├── .steering/
├── package.json
├── eslint.config.js
└── [必要なら] jsconfig.json
```

## ディレクトリ詳細

### `assets/`

**役割**: [説明]

**配置ファイル**:
- [例1]
- [例2]

**命名規則**:
- `kebab-case`

### `src/app/`

**役割**: [説明]

**配置ファイル**:
- `providers/`
- `theme/`
- `navigation/`

**依存関係**:
- 依存可能: `shared/`, `features/`
- 依存禁止: `tests/`

### `src/features/[feature-name]/screens/`

**役割**: [説明]

**配置ファイル**:
- `[Feature]Screen.jsx`

**命名規則**:
- `PascalCaseScreen.jsx`

**依存関係**:
- 依存可能: `components/`, `hooks/`, `shared/`
- 依存禁止: Expo API の直接利用

### `src/features/[feature-name]/components/`

**役割**: [説明]

**配置ファイル**:
- `[ComponentName].jsx`

**命名規則**:
- `PascalCase.jsx`

**依存関係**:
- 依存可能: `shared/`, `types/`
- 依存禁止: `screens/`

### `src/features/[feature-name]/hooks/`

**役割**: [説明]

**配置ファイル**:
- `use[Feature].js`

**命名規則**:
- `use` で始める

**依存関係**:
- 依存可能: `logic/`, `services/`, `types/`
- 依存禁止: `screens/`

### `src/features/[feature-name]/logic/`

**役割**: [説明]

**配置ファイル**:
- `createSomething.js`
- `resolveSomething.js`

**命名規則**:
- `camelCase.js`

**依存関係**:
- 依存可能: `types/`, `shared/utils/`
- 依存禁止: React、Expo、画面コンポーネント

### `src/features/[feature-name]/services/`

**役割**: [説明]

**配置ファイル**:
- `feedbackService.js`
- `storageService.js`

**命名規則**:
- `camelCase.js`

**依存関係**:
- 依存可能: Expo / React Native 公式 API
- 依存禁止: `screens/`

### `src/features/[feature-name]/__tests__/`

**役割**: [説明]

**配置ファイル**:
- `[Target].test.jsx`
- `[Target].test.js`

**命名規則**:
- `*.test.jsx` / `*.test.js`

### `src/shared/`

**役割**: [説明]

**配置ファイル**:
- `components/`
- `constants/`
- `utils/`
- `test-support/`

## ファイル配置規則

| ファイル種別 | 配置先 | 命名規則 | 例 |
|------------|--------|---------|-----|
| 画面 | `screens/` | `PascalCaseScreen.jsx` | `GameScreen.jsx` |
| コンポーネント | `components/` | `PascalCase.jsx` | `MemoryCard.jsx` |
| hook | `hooks/` | `useSomething.js` | `useGameSession.js` |
| service | `services/` | `camelCase.js` | `feedbackService.js` |
| util / logic | `logic/`, `utils/` | `camelCase.js` | `createDeck.js` |
| test | `__tests__/` | `*.test.jsx` / `*.test.js` | `GameScreen.test.jsx` |

## 依存関係ルール

```text
screens -> hooks -> logic
screens -> hooks -> services
components -> shared
services -> Expo / React Native API
```

**禁止例**:
- `logic/` から Expo API を呼ぶ
- `components/` が `screens/` を import する
- `shared/` に feature 固有知識を持ち込む

## テスト / 起動確認

```bash
npm run lint
npm test
npx expo start
```

## 実機確認の記録先

- `.steering/[YYYYMMDD_N]-[feature-name]/tasklist.md`
