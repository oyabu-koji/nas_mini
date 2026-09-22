# Feature Spec

## Metadata

- Date: 2026-09-05
- Feature name: expo-sdk-57-upgrade
- Status: confirmed
- Related files:
  - `package.json`
  - `package-lock.json`
  - `app.json`
  - `src/application/AppShell.jsx`
  - `src/features/preview-review/screens/PreviewReviewScreen.jsx`
  - `ios/`
  - `AGENTS.md`
  - `PROJECT_CONTEXT.md`
  - `README.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`

## Background

iPhoneにインストールされているExpo GoはSDK 57系であり、SDK 54の現行projectを開けない。画像軽量化・preview閲覧の実機確認を再開するため、projectをExpo SDK 57へ更新し、SDK 55–57のbreaking changeとchecked-in iOS native projectを整合させる必要がある。

## Target Users / Use Cases

- 開発者がNode 24環境でSDK 57のmobile appを起動し、iPhoneのExpo GoからExpo tunnel経由でMetroへ接続する。端末で画像を選択し、LAN又はTailscale経由のbackendへuploadしてpreviewを閲覧する。
- 開発者がDevelopment Buildを作成できるMacでcustom native moduleを含む動画uploadを確認する。
- reviewerがSDK 57互換dependency、app config、source migration、native設定、品質gateを再現可能に確認する。

## Scope

- Expo SDKを54から55、56、57へ順番に上げ、各段階でExpo推奨dependencyへ整合する。
- SDK 57はHermes memory/startup regression修正を含む`expo@57.0.17`以上を使用し、React Native 0.86.3以上のSDK 57互換patchへ揃える。
- React、React Native、Expo package、Jest/ESLint連携packageをSDK 57互換版へ更新する。
- SDK 55以降で廃止されたAndroid `edgeToEdgeEnabled`を削除する。
- `expo-video`の`allowsFullscreen`をSDK 57の`fullscreenOptions`へ移行する。
- SDK 56で刷新された`expo-media-library`について、現行function API利用箇所を明示的な`/legacy` importへ移して挙動を維持する。
- React Nativeで非推奨となったcore `SafeAreaView`を`react-native-safe-area-context`へ移行する。
- custom native module入りDevelopment Buildの標準launcherとして`expo-dev-client`をSDK 57互換版で追加する。Expo Go確認時はCLIを明示的にGo modeで起動する。
- `newArchEnabled`設定はtrue/falseを問わずapp configへ残さず、SDK 55以降の必須New Architectureを使用し、custom Expo Moduleのautoload境界を保持する。
- checked-in iOS projectのdeployment target、Pods、autolinking情報をSDK 57へ整合する。
- SDK 57前提へstable docsとproject memoryを更新する。
- lint、unit test、coverage、Expo dependency check、Expo Doctor、iOS export、Metro起動を検証する。

## Out of Scope

- Node 24の変更。
- TypeScript導入。
- application/backend API contract又は画像軽量化仕様の変更。
- App Store/TestFlight release。
- Apple signing credential又はExpo account credentialの保存・自動入力。
- backendを公開する新規HTTPS tunnelの導入。
- Xcode未導入環境でのDevelopment Build実機build。native設定とPod解決は可能な範囲で検証し、実buildはXcode 26.4以上を導入後に行う。

## User Flow

1. 開発者はNode 24でdependencyをSDK 55、56、57の順に更新する。
2. 各SDK段階で`npx expo install --fix`、dependency check、`npx expo-doctor@latest`を行い、不整合を次段階へ持ち越さない。
3. SDK 57 breaking changeに合わせてapp config、safe area、video fullscreenを更新する。
4. Mobile品質gateとExpo Doctor、bundle exportを通す。
5. Expo CLIとiPhone Expo Goを同じExpo accountへloginする。
6. `npx expo start --go --tunnel`のQRをiPhoneで読み、Metroへ接続する。
7. iPhoneのBackend URLへ、同一LANならMBAのLAN IP、別networkならMBAのTailscale IP又はMagicDNS名を設定する。
8. 画像upload・preview閲覧を確認する。
9. 動画のresumable uploadはExpo Goではなくcustom module入りDevelopment Buildで確認する。

## Functional Requirements

### FR-1: SDK 57 dependency整合

- `expo`は`57.0.17`以上へ更新し、Expo管理packageは`npx expo install`が示すSDK 57互換版へ揃える。
- React NativeはSDK 57対応版、Reactとrenderer/test dependencyは同じReact versionへ揃える。
- lockfileを更新し、`npx expo install --check`が不整合を報告しない。
- Node versionは24のまま変更しない。

### FR-2: Source/config migration

- Android app configに廃止済み`edgeToEdgeEnabled`を残さない。
- `VideoView`のfullscreen許可は`fullscreenOptions={{ enable: true }}`で維持する。
- `getAssetInfoAsync`、`createAssetAsync`、`deleteAssetsAsync`等の現行function APIは`expo-media-library/legacy`からimportし、新object APIへの機能変更を本upgradeへ混在させない。
- safe areaは`react-native-safe-area-context`のprovider/viewを使用し、既存screen layoutを維持する。
- Expo Goでcustom `StreamingSha256`が存在しなくても画像flowのmodule loadでcrashしない既存lazy load境界を維持する。

### FR-3: iOS native整合

- checked-in `ios/`をSDK 57のNew ArchitectureとiOS 16.4以上へ整合する。
- custom `StreamingSha256` podspecのdeployment targetを16.4以上へ更新する。
- CocoaPodsを用意してPod lock/autolinkingをSDK 57 dependencyへ必ず更新する。実行不能又は更新失敗ならnative整合とSDK移行は未完了とする。
- `ios/`はnon-CNGのrelease inputとしてchecked-inするnative projectと定義する。temporary reference projectのSDK 57 prebuild結果とNative Project Upgrade Helperを比較し、tracked `ios/`へ必要な差分だけを適用してPod installする。
- full Xcode不足でnative build不能な場合は、その環境制約を検証結果へ明記し、Expo GoでのJavaScript/image flow検証とは分離する。

### FR-4: Remote device test

- Expo tunnelはMetro接続だけを中継し、MBA上のbackend `:8000`を中継しない。
- Metro接続はTailscale又は同一LANを前提にしない。
- Backend接続は同一LANのprivate IP、又は別networkからのTailscale IP/MagicDNS名を使う。`127.0.0.1`を使わない。
- 2026-09以降のExpo Go要件に従い、Expo CLIとiPhoneを同一Expo accountへloginする。
- Expo Go検証範囲は画像選択・upload・preview閲覧とし、custom native SHA-256 moduleを使う動画flowはDevelopment Buildへ限定する。

## Non-Functional / Technical Notes

- Expo公式のupgrade手順に従い、SDKは一段ずつ更新する。
- SDK 57はNew Architecture前提で扱う。
- SDK 57 / React Native 0.86.3のローカルnative buildにはXcode 26.4以上とiOS deployment target 16.4以上を前提とする。
- 既存のbackend、preview authority修正、未追跡mediaを変更・削除・stageしない。
- dependency更新は`npx expo install`を使用する。

## Acceptance Criteria

- `package.json`とlockfileがSDK 57互換dependencyで整合している。
- `expo`が`57.0.17`以上、React NativeがSDK 57の0.86.3以上へ解決されている。
- `npx expo install --check`と`npx expo-doctor@latest`が重大な問題なく成功する。
- 廃止済み`edgeToEdgeEnabled`と`allowsFullscreen`、React Native core `SafeAreaView`がproduction sourceから除去され、旧MediaLibrary function APIは明示的な`/legacy` importから利用される。
- Mobile lint、unit test、coverageが成功する。
- `npx expo export --platform ios`が成功する。
- MetroがSDK 57のExpo Go modeで起動し、tunnel URLを発行できる。
- iPhone Expo Goで画像選択、upload完了、Asset Detailからpreview表示まで成功し、結果をtasklistへ記録する。
- checked-in iOS native projectがiOS 16.4以上へ整合し、`Podfile.lock`がSDK 57 Podsへ必ず更新される。full Xcode不足で免除できるのはnative build、custom module load、動画flowの実機検証だけとする。
- full Xcode 26.4以上がある場合はDevelopment Buildを再作成し、custom module loadと動画resumable uploadを確認する。ない場合は未検証項目として記録する。
- 画像upload・preview閲覧に関する既存修正が保持される。
- `implementation_validator`の必須指摘が解消される。

## Open Questions

- Expo CLI loginはcredentialを扱うため、実機接続時に開発者が対話的に行う。
- Development Buildの実機buildはXcode 26.4以上の導入後に行う。

## Durable Docs Impact

- 更新候補:
  - `AGENTS.md`
  - `PROJECT_CONTEXT.md`
  - `README.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
- 更新要否: 必要
- 理由: SDK version、New Architecture、minimum iOS/Xcode、Expo Go/Development Build境界は今後の実装・検証へ継続的に影響するため。
