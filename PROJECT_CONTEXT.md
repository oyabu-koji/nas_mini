# Project Context

This repository contains a React Native application built with Expo.

### 日本語説明
このリポジトリは、Expo を使って構築する React Native アプリを前提としています。

## Technology

React Native
Expo with checked-in native projects (non-CNG)
JavaScript (not TypeScript)

### 日本語説明
- UI 基盤は React Native です。
- Expoを使い、checked-in native projectを正本とするnon-CNG構成です。
- 実装言語は JavaScript で、TypeScript は前提にしません。

## Environment

Node 24
Expo SDK 57
React Native 0.86.3
iOS deployment target 16.4+
Xcode 26.4+
New Architecture

### 日本語説明
- Node の実行環境は 24 系を使います。
- Expo SDKは57、React Nativeは0.86.3を前提にします。
- iOSはdeployment target 16.4以上、Xcode 26.4以上、New Architectureを前提にします。

## Environment Transfer

This project uses Node 24 and treats Docker as the canonical runtime when moved to the Mac mini.

Do not rely on the Mac mini host Node version when the app runs inside Docker. Install dependencies inside Docker and do not copy local `node_modules`.

### 日本語説明
このプロジェクトは Node 24 を使い、Mac mini へ移す場合は Docker 内の実行環境を正とします。

Docker 内で動かす場合、Mac mini ホスト側の Node バージョンには依存しません。依存関係は Docker 内で `npm install` し、ローカルの `node_modules` は持ち込まないでください。

## Development

Start development server:

`npx expo start`

For Expo Go testing:

`npx expo start --go`

For remote Expo Go access to Metro:

`npx expo start --go --tunnel`

Before using iOS Expo Go with SDK 57, sign in to the same Expo account in the CLI and on the iPhone. The tunnel carries Metro traffic only; backend traffic still uses LAN or Tailscale.

### 日本語説明
通常の開発サーバー起動は `npx expo start`、Expo Go確認は `npx expo start --go` を使います。
リモート端末からMetroへ接続する場合は `npx expo start --go --tunnel` を使います。Expo tunnelはbackendを中継しないため、別networkのiPhoneからbackendへ接続する場合はTailscale IP又はMagicDNS名を使います。
SDK 57のiOS Expo Goを使う前に、Expo CLIとiPhoneを同一Expo accountへloginします。

## Dependency policy

Use `npx expo install` for Expo-related dependencies.

Do not upgrade Expo SDK unless explicitly requested.

Do not change Node version automatically.

Treat checked-in `ios/` and `Podfile.lock` as release inputs. Compare SDK upgrades with a temporary reference prebuild and do not run `prebuild --clean` in this repository.

### 日本語説明
Expo 関連の依存関係は `npx expo install` を使って追加・更新します。  
Expo SDK は明示依頼がない限りアップグレードしません。  
Node のバージョンも自動では変更しません。
checked-in `ios/`と`Podfile.lock`はrelease inputです。SDK更新では一時reference prebuildと比較し、このrepositoryで`prebuild --clean`を実行しません。
