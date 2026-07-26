# iPhone Apple Log動画変換アプリ
## LUT・Tailscale・App Store審査・バックエンド運用方針

最終更新: 2026-07-18

---

## 1. このドキュメントの目的

本ドキュメントは、以下のiPhoneアプリについて、現時点の設計判断と今後の実装方針をCodexへ正確に伝えるためのもの。

### アプリ概要

iPhoneでApple Log撮影した動画を、自分のMac miniへ転送し、Mac mini側で以下を実行する。

1. LUTまたは色変換プリセットを適用する
2. 動画を軽量化する
3. 処理済み動画をiPhoneへ返却する
4. iPhoneの写真ライブラリ等へ保存する

通常利用者は自分と家族を想定している。
通常運用では、iPhoneと自宅Mac miniをTailscale経由で接続する。

---

## 2. 現時点の重要な結論

### 2.1 Tailscale前提の現在の実装は捨てない

家族が通常利用するときは、引き続き以下の構成とする。

```text
iPhone
  ↓ Tailscale
自宅Mac mini
  ↓
LUT適用・色変換・軽量化
  ↓
iPhoneへ返却
```

ただし、iPhoneアプリ内にTailscaleのIPアドレスやURLを固定しない。

接続先は設定値として扱い、QRコード、設定ファイル、手入力などで変更可能にする。

---

### 2.2 自宅Mac miniをApp Reviewへ公開しない

Appleの審査員を、自宅のMac miniや自分のTailnetへ接続させない。

理由:

- 家族の動画や個人データが存在する
- 自宅サーバーを外部公開したくない
- 審査員を自分のTailnetへ参加させる運用は現実的でない
- Tailscaleアプリの導入やログインを審査員に要求する構成は、審査上の不確実性が高い

自宅環境と審査環境は完全に分離する。

---

### 2.3 App Review用には、独立したHTTPS処理環境を用意する

App Store審査の確実性を上げるため、審査員が自宅環境なしで一連の処理を実行できる経路を用意する。

```text
審査員のiPhone
  ↓ 通常のHTTPS
審査用バックエンド
  ↓
LUTまたは色変換・軽量化
  ↓
審査員のiPhoneへ返却
```

審査用環境は本番相当の性能である必要はない。

想定する制限:

- 数秒程度のサンプル動画
- 720pまたは1080p出力
- 小さいアップロード上限
- 同時処理数1件
- 処理後のファイル自動削除
- 審査専用トークン
- 審査時だけ起動可能

現時点では利用可能なクラウド環境を所有していないため、クラウドサービスの選定と構築は別タスクとする。

### 2.4 現在の開発優先順位

審査用クラウドは、Mac mini上で一連の処理が完成してから着手する。

現在の優先順位:

```text
1. MediaVaultのoriginal退避・検証完了後に、iPhone → Mac mini → 軽量化 → iPhone返却を完成
2. LUTの読み込み・選択・適用基盤を完成
3. Identity LUTで処理経路を検証
4. テスト用LUTで色変化を検証
5. Apple Log → Rec.709の自前変換を実装
6. Final Cut Pro出力と品質比較
7. 完成したバックエンドを審査用クラウドへ移植
```

クラウド上で新しい動画処理機能を開発しない。
Mac miniで動作確認済みのバックエンドを、審査用環境へ展開する方針とする。

---

### 2.5 LUT未登録時のフォールバック

Apple Logまたはユーザーが選択したLUTプリセットが未登録、無効化済み、または利用不可の場合は、ジョブ全体を`failed`にしない。

1. originalが`file_verified`になった後にBackendがApple Logを自動判定する。初期formal previewは自動preset解決だけを使い、Apple Logを検出した場合は`generated-apple-log-rec709`を要求する。ユーザーのidentity/test/custom選択は別のmanaged renditionとして維持し、formal preview、確認、削除条件へ昇格しない。
2. 要求プリセットが未登録または無効化済みの場合は、LUTを適用しない軽量化previewを生成し、iPhoneへ返却する。ジョブは`done`とする。
3. レスポンスと画面に、`requested_preset_id`、`applied_preset_id = compress-only`、`color_transform_status = unavailable`、`color_transform_error_code = lut_preset_unavailable`を明示する。
4. Apple Logの場合は、このpreviewを「Apple Logのままの未変換preview」と表示し、Rec.709変換済みとは表示しない。
5. Backend側originalのimmutable保持と`file_verified`を前提に、未変換previewでも`preview_ready`、内容確認、iPhone側originalの手動削除導線を提供できるものとする。Phase 2Cの他のhash検証条件と`preview_confirmed`を満たす場合は、`safe_to_delete_candidate`にもできる。判定根拠は「退避したoriginalの完全性と内容確認」であり、色変換の品質評価ではない。provenanceには`transform_kind = none`、要求・適用プリセット、色変換未適用の理由を記録する。
6. 後日LUTが有効化されたら、既存ジョブの結果を書き換えず、同じimmutable originalから新しいプリセット版ジョブを実行する。

LUTファイルが登録済みでも、manifest検証、SHA-256、形式、FFmpeg適用のいずれかが失敗した場合は、設定不備を隠さないためにジョブを`failed`にする。「未登録」と「破損・改ざん・適用失敗」は分けて扱う。

この方針を実装仕様へ昇格する際は、既存Phase 2Bの「Apple LogはLUT provenanceだけが`preview_ready`になれる」という制約を、上記の未変換`transform_kind = none`を許可する契約へ更新する。未変換previewは色評価用ではないことをMobileに常に表示する。

---

## 3. 接続先を切り替えられる設計

### 3.1 iPhoneアプリに接続先を固定しない

避ける実装:

```js
const API_BASE_URL = "http://100.x.x.x:8000";
```

採用する設計例:

```js
const serverConfig = {
  id: "home-mac-mini",
  name: "Home Mac mini",
  baseUrl: "http://100.x.x.x:8000",
};
```

`baseUrl`や名称は通常の設定保存領域に保存し、`accessToken`は`serverConfig.id`をキーとして`expo-secure-store`（iOS Keychain）へ分離保存する。ソースコードにトークンを固定したり、平文で保存したりしない。

---

### 3.2 通常利用と審査で同一APIを使用する

家族用と審査用で、iPhoneアプリの画面・操作・API仕様を分けない。

```text
家族利用:
iPhone → Tailscale URL → Mac mini API

App Review:
iPhone → 公開HTTPS URL → 審査用API
```

推奨API例:

```text
GET  /api/v1/capabilities
POST /api/v1/jobs
PUT  /api/v1/jobs/{jobId}/source
GET  /api/v1/jobs/{jobId}
GET  /api/v1/jobs/{jobId}/result
DELETE /api/v1/jobs/{jobId}
```

バックエンド実装は可能な限り共通化する。

---

### 3.3 初期リリースは手入力でサーバー設定を登録する

初期リリースでは、SettingsからBackend URLとアクセストークンを手入力する。トークンは`expo-secure-store`へ保存し、QRコードに含めない。

QRコードによる接続設定インポートは後続フェーズとする。実装する際は、ワンタイム交換、TTL、失効、漏えい時のrevokeを定義し、長期固定トークンをQRコードへ含めない。

---

## 4. LUTとApple Log → Rec.709変換の方針

### 4.1 Rec.709規格そのものとLUTファイルの権利は別

Rec.709形式の動画を生成すること自体と、Appleが配布・内蔵しているLUTファイルを再利用・再配布することは別問題として扱う。

---

### 4.2 Final Cut Pro内蔵LUTは抽出・同梱しない

このMacのFinal Cut Proには、Apple LogをRec.709へ変換するLUT資産が含まれている。

ただし、以下は行わない。

- Final Cut Pro.app内のLUTを抽出する
- 抽出したLUTをリポジトリへ追加する
- 抽出したLUTをDocker imageへ入れる
- 抽出したLUTをアプリやインストーラーへ同梱する

公開APIがないことと、ライセンス上明確に禁止されていることは別だが、技術面・権利面の両方で正式構成として採用しない。

---

### 4.3 Apple Developer Downloadsの公式LUTは個別条件を確認する

Apple Developer Downloadsには、Apple Log LUTおよびWhite Paperが存在する。

ただし、公式配布物であっても、以下を確認するまではDocker、アプリ、インストーラーへ同梱しない。

確認対象:

- ZIP内のREADME
- LICENSEまたは利用条件
- Backendでの利用可否
- Docker imageへの同梱可否
- アプリ利用者への再配布可否
- LUTのバージョン
- SHA-256
- Apple Log White Paperの版

現時点の扱い:

```text
公式LUTの採用可否: 保留
理由: 個別の利用条件を未確認
```

「利用不可」と断定するのではなく、「明示的に確認できるまで採用しない」とする。

---

### 4.4 LUT実装は段階的に進める

Apple Log → Rec.709の完成版LUTから着手しない。

以下の順番で実装する。

```text
Phase 1: LUTなしの軽量化
Phase 2: Identity LUT
Phase 3: 色変化が分かる自作テストLUT
Phase 4: Apple Log → Rec.709の自前変換
```

#### Phase 1: LUTなしの軽量化

最初に以下の一周をMac mini上で完成させる。

```text
iPhoneで動画選択
  ↓
Tailscale経由でMac miniへ送信
  ↓
FFmpegで軽量化
  ↓
処理進捗をiPhoneへ通知
  ↓
処理済み動画をiPhoneへ返却
  ↓
写真ライブラリ等へ保存
```

Apple Logの眠い色のままでもよい。ただしiPhone側では未変換previewと明示し、Rec.709変換済みと誤認させない。
この段階の目的は、MediaVaultのoriginal退避・検証後の転送・ジョブ処理・返却・保存を完成させることである。

#### Phase 2: Identity LUT

入力RGBをそのまま出力するIdentity LUTを自前生成し、以下を確認する。

- `.cube`を読み込める
- FFmpegへ正しく渡せる
- LUTあり／なしを切り替えられる
- エラー時に安全に失敗できる
- iPhone側にプリセット一覧を表示できる
- Mac mini側と将来の審査用環境で同じ処理が再現できる

#### Phase 3: 自作テストLUT

少し暖色化する、彩度を少し下げる等の、効果が目視で分かる自作LUTを使用する。

この段階ではApple Log対応を目的としない。
LUT選択から結果返却までの処理経路が正しく動作することを確認する。

#### Phase 4: Apple Log → Rec.709

公式Apple LUTの完全一致を必須要件にしない。

Apple Logの公開プロファイル等を参照し、再現可能な変換を実装または生成する。

概念的な処理:

```text
Apple Log
  ↓ Logデコード
Linear BT.2020
  ↓ BT.2020 → BT.709色域変換
  ↓ トーンマッピング
  ↓ 色域外処理・彩度調整
Rec.709 SDR
```

候補:

- `colour-science/colour`等の明示ライセンス付き実装を利用
- 固定バージョンのgeneratorから`.cube`を生成
- generator version、パラメータ、出力LUTのSHA-256を記録
- Final Cut Proの出力は品質比較用リファレンスとしてのみ利用

注意:

- Apple公式LUTと完全に同じ見た目になる保証はない
- ハイライトのロールオフ、色域外色、彩度処理を設計する必要がある
- OSSライセンスだけで全ての権利判断が完了するわけではない
- 採用ライブラリと生成物のライセンス記録を残す

---

### 4.5 カスタムLUTをユーザーがMac側へ追加できるようにする

Mac mini側に、ユーザー管理のLUTフォルダを用意する。

```text
data/
└── user_luts/
    ├── custom_01.cube
    └── custom_02.cube
```

Docker imageやGitリポジトリには、ユーザーが個別に取得したLUTを含めない。

アプリはMac側の`capabilities`または`presets` APIから利用可能なプリセット一覧を取得する。`compress-only`は常に返す。ユーザーLUTを選択した場合は、サーバーは実際に適用したプリセットID、version、SHA-256を結果に含める。

選択したユーザーLUTが未登録または無効な場合は、第2.5節に従い`compress-only`へフォールバックする。有効なユーザーLUTの結果も、Apple Log → Rec.709と明示するのは、その目的と証跡を満たす`generated-apple-log-rec709`プリセットだけとする。

例:

```json
{
  "presets": [
    {
      "id": "compress-only",
      "name": "軽量化のみ",
      "type": "transcode"
    },
    {
      "id": "generated-apple-log-rec709",
      "name": "Apple Log → Rec.709",
      "type": "color-transform"
    },
    {
      "id": "user-lut-001",
      "name": "Custom LUT",
      "type": "lut"
    }
  ]
}
```

iPhone側にプリセット名やLUT一覧を固定しない。

---

## 5. App Store審査時の機能の見せ方

### 5.1 審査用クラウドはMac mini版完成後に構築する

審査用クラウドは、現在の最優先実装ではない。

以下をMac mini上で確認してから、同じバックエンドを審査用環境へ移植する。

- 動画アップロード
- ジョブ作成
- 進捗取得
- LUTまたはプリセット適用
- 軽量化
- 処理結果のダウンロード
- iPhoneへの保存
- 一時ファイル削除
- エラー処理

審査用クラウド固有の処理ロジックを増やさず、環境変数、保存先、認証、容量制限等のみを切り替える。

### 5.2 審査時から本来の処理フローを動かす

審査員が確認できる操作:

1. 処理サーバーへ接続する
2. 動画を選択する
3. 利用可能な処理プリセットを取得する
4. LUTまたは色変換を選択する
5. 動画をアップロードする
6. 処理進捗を確認する
7. 処理済み動画をダウンロードする
8. iPhoneで再生・保存する

審査時だけボタンを隠したり、承認後に突然主要機能を解放したりしない。

---

### 5.3 審査用の色変換はApple公式LUTでなくてよい

審査用バックエンドには、権利関係が明確な以下のいずれかを置く。

- 自前生成のApple Log → Rec.709変換
- 自作Identity LUT
- 自作テストLUT
- 明示的に再配布可能なLUT

重要なのは、「接続先サーバーの登録済みプリセットを選び、実際に処理する」というアプリ機能を審査可能にすること。プリセットが未登録の場合は、軽量化だけの結果と色変換が未適用であることを審査員に見えるようにする。

---

### 5.4 App Review Notesに記載する内容

申請時には、以下を具体的に記載する。

- このアプリはユーザー所有の動画処理サーバーへ接続する
- 審査用には独立したHTTPS処理環境を用意している
- 審査用サーバーの接続方法
- 手入力するBackend URLと認証情報
- 認証情報
- 操作手順
- サンプル動画の選択方法
- 処理結果の保存方法

審査中は、審査用バックエンドを安定稼働させ、API仕様やURLを原則変更しない。

---

## 6. 申請後・公開後の変更ルール

### 6.1 iPhoneアプリ側のコード変更

以下を変更する場合は、新しいアプリビルドを作り、App Storeへ更新申請する。

- React Native / Swift / Objective-Cコード
- 画面、ボタン、ナビゲーション
- iPhone側の通信方式
- 新しい端末権限
- 新しい主要機能
- アプリ内に埋め込まれた接続先
- iPhone側で実行する動画処理

提出済みバイナリに、後からコードを追加・差し替えることはできない。

---

### 6.2 バックエンド側のコード変更

Mac miniまたは審査用サーバーのバックエンドコードは、公開後でも更新可能。

通常、以下はiPhoneアプリの再申請を必要としない。

- FFmpegのパラメータ調整
- エンコード品質・速度の改善
- バグ修正
- ログ処理の改善
- ファイル削除処理の改善
- LUTファイルの追加・入れ替え
- 色変換アルゴリズムの品質改善
- APIサーバーの移転
- 同一機能内での処理プリセット追加
- Mac mini版と審査用サーバー版の内部実装差し替え

ただし、バックエンド変更によって、審査時に存在しなかった全く別の主要機能を、iPhoneアプリ上へ突然出現させない。

---

### 6.3 「外部コードを追加してはいけない」の意味

避けること:

- iPhoneアプリがサーバーから新しい実行コードをダウンロードする
- ダウンロードしたコードでアプリの機能を変更する
- 審査中は隠していた主要機能を、Remote Config等で公開後に解放する

問題になりにくいこと:

- 接続URLや認証情報を設定データとして変更する
- バックエンド内部のPython、Node.js、FFmpeg処理を更新する
- 同一の動画変換機能内で品質や性能を改善する
- 接続先サーバーが返すプリセット一覧を更新する

---

### 6.4 互換性を維持する

古いiPhoneアプリでも新しいバックエンドを利用できるようにする。

推奨事項:

- APIに`/api/v1`等のバージョンを付ける
- 既存レスポンスフィールドを安易に削除しない
- 新しいレスポンスフィールドを追加する場合、古いアプリが無視できるようにする
- `capabilities`でサーバー機能を宣言する
- `minimumClientVersion`を返せるようにする
- 破壊的変更時は`/api/v2`を追加する

例:

```json
{
  "apiVersion": "1.0",
  "serverVersion": "0.8.0",
  "minimumClientVersion": "0.5.0",
  "features": {
    "customLut": true,
    "generatedRec709": true,
    "chunkUpload": false
  }
}
```

---

## 7. 配布方法の前提

### 7.1 毎週ビルドする運用は採用しない

以下は採用しない。

- 無料Apple IDによる7日ごとの再署名
- 頻繁な手動再インストール
- TestFlightを恒久利用する運用

---

### 7.2 App StoreまたはUnlisted Appを目標とする

家族利用が中心でも、アプリを通常のApp Storeバイナリとして配布できれば、毎週ビルドする必要はない。

Unlisted Appを目標候補とするが、Unlistedでも通常のApp Reviewは必要。

iPhoneアプリを変更した場合は、更新版として再審査が必要。
バックエンドだけの変更であれば、通常はアプリ更新申請を行わない。

App Reviewの最終判断はAppleが個別に行うため、審査通過を保証するものではない。

---

## 8. Codexに依頼する実装タスク

### Phase A: Mac miniで処理の一周を完成

最優先フェーズ。

- [ ] iPhoneから動画を選択できる
- [ ] Tailscale経由でMac miniへ動画を送信できる
- [ ] MediaVaultのupload session・ハッシュ検証・immutable original確定後に処理を開始する
- [ ] Mac mini側でジョブを作成できる
- [ ] FFmpegでLUTなしの軽量化を実行できる
- [ ] iPhone側で進捗を確認できる
- [ ] 処理結果をiPhoneへ返却できる
- [ ] iPhoneで処理済み動画を再生できる
- [ ] 写真ライブラリ等へ保存できる
- [ ] 成功時・失敗時に一時ファイルを削除できる（immutable originalは削除しない）
- [ ] 通信切断、容量不足、FFmpeg失敗を区別して扱える
- [ ] このフェーズではApple Logの色変換を完成条件にしない
- [ ] 選択プリセットが未登録の場合は、`compress-only`の結果を返し、未変換の状態を明示できる

---

### Phase B: 接続先の抽象化

Mac miniでの一周を壊さない範囲で進める。

- [ ] 固定のTailscale URL/IPをコードから除去する
- [ ] `ServerConfig`モデルを作成する
- [ ] URL・名称は通常の設定保存領域、トークンはKeychain（`expo-secure-store`）へ分離して保存する
- [ ] 複数サーバーを登録・選択可能にする
- [ ] QRコードによる接続設定インポートは初期リリース対象外とする
- [ ] 接続テストAPIを実装する
- [ ] TLSエラー、認証エラー、到達不能を区別して表示する

---

### Phase C: LUT処理基盤

完成版のApple Log変換より先に、LUT処理の土台を完成させる。

- [ ] `user_luts/`等の外部LUTフォルダを用意する
- [ ] LUTなし、Identity LUT、自作テストLUTを登録可能にする
- [ ] FFmpegの`lut3d`等でLUTを適用する
- [ ] LUTあり／なしを切り替えられる
- [ ] LUTファイルの存在、形式、サイズ、グリッド数を検証する
- [ ] 不正なLUTで安全に失敗する
- [ ] iPhone側からプリセットを選択できる
- [ ] LUT名、由来、ライセンス、SHA-256をmanifest化する
- [ ] 未登録プリセットは`compress-only`へフォールバックし、破損・改ざん・適用失敗は`failed`にする
- [ ] Identity LUT generatorを作成する
- [ ] 色変化が目視できるテストLUT generatorを作成する

---

### Phase D: Apple Log → Rec.709自前変換

- [ ] `file_verified` originalに対するApple Log自動判定の根拠とfixtureを固定する
- [ ] Apple Logの入力仕様を固定する
- [ ] Logデコードを実装する
- [ ] Linear BT.2020からBT.709への色域変換を実装する
- [ ] トーンマッピング方式を明示する
- [ ] 色域外処理と彩度処理を明示する
- [ ] Rec.709出力エンコードを実装する
- [ ] 33x33x33等の`.cube`を再現可能に生成する
- [ ] generator versionを固定する
- [ ] 採用ライブラリのバージョンを固定する
- [ ] 生成LUTのSHA-256を保存する
- [ ] NaN、Inf、範囲外値を検証する
- [ ] Mac mini上で実動画に適用する
- [ ] Final Cut Pro出力と目視・数値比較する
- [ ] Apple公式LUTとの完全一致を完成条件にしない

---

### Phase E: バックエンドAPIの安定化

Mac mini版が動作したAPIを基準にする。

- [ ] APIを`/api/v1`で統一する
- [ ] `GET /api/v1/capabilities`を実装する
- [ ] ジョブ作成・アップロード・進捗・結果取得を統一する
- [ ] 結果に要求プリセット、実際に適用したプリセット、色変換状態とエラーコードを含める
- [ ] iPhone側から固定プリセット定義を除去する
- [ ] サーバーから利用可能なプリセット一覧を返す
- [ ] `compress-only`を必ず利用可能にする
- [ ] APIのOpenAPI仕様を固定する
- [ ] 古いクライアントとの互換性方針を文書化する
- [ ] 将来のMac mini版と審査用版で同一API契約を使用できるようにする

---

### Phase F: App Review用クラウド環境

Mac mini版の処理が完成してから着手する。

- [ ] 完成したバックエンドをDocker化する
- [ ] Mac mini版と同じDockerfileを審査用にも利用可能にする
- [ ] 自宅Mac miniと独立したHTTPS環境へ展開する
- [ ] 環境変数で保存先、認証、容量、同時処理数を切り替える
- [ ] 小容量・短時間動画に制限する
- [ ] 審査専用トークンを設定可能にする
- [ ] 処理後の自動削除を実装する
- [ ] 審査用QRコード生成手順を用意する
- [ ] App Review Notesのテンプレートを作成する
- [ ] 審査中に変更しないデプロイ手順を文書化する
- [ ] クラウド上で新しい動画処理ロジックを開発しない

---

### Phase G: ライセンス・品質証跡

- [ ] Apple公式LUT packageの個別利用条件を確認する
- [ ] 確認前は公式LUTをリポジトリ・Dockerへ入れない
- [ ] 採用ライブラリのライセンスを保存する
- [ ] 生成LUTのgenerator versionを固定する
- [ ] 生成LUTのSHA-256を保存する
- [ ] Final Cut Pro出力との目視比較を行う
- [ ] 白飛び、黒つぶれ、色転びのテスト動画を用意する
- [ ] Rec.709のcolor primaries、transfer、matrixタグを確認する

---

## 9. Codexへの禁止事項

Codexは、明示的な許可がない限り以下を行わないこと。

- Final Cut Pro.appからLUTを抽出しない
- Apple公式LUTをGitへコミットしない
- Apple公式LUTをDocker imageへコピーしない
- ライセンス不明の第三者LUTを同梱しない
- Tailscale URLをソースコードへ固定しない
- 自宅Mac miniをインターネットへ公開しない
- App Review用に自宅Tailnetの認証情報を作成しない
- 審査後に主要機能をRemote Configで秘密裏に解放しない
- iPhoneアプリが外部から実行コードを取得する設計にしない
- APIの破壊的変更を既存バージョンへ直接適用しない

---

## 10. 現在地と未確定事項

### 現在地

現時点では、Mac mini上でアプリの処理全体がまだ完成していない。

現在の最優先は以下。

```text
iPhone
  ↓ Tailscale
Mac mini
  ↓
original退避・hash検証
  ↓
LUTなしの軽量化
  ↓
iPhoneへ返却・保存
```

この一周が完成するまでは、審査用クラウド構築を開始しない。

### 未確定事項

以下は今後決定する。

1. Apple Log → Rec.709変換の具体的なトーンマッピング
2. `colour-science/colour`等を採用するか
3. Apple公式LUT packageの個別ライセンス条件
4. 審査用クラウド／ホスティングサービス
5. 審査用環境を常設するか、申請時のみ起動するか
6. App Store通常公開、Unlisted App、その他配布方式の最終選択
7. Mac mini側バックエンドのDocker運用方法
8. QRコード認証の具体方式
9. サンプル動画をアプリに同梱するか、審査用サーバーに置くか

---

## 11. 最終方針の要約

```text
通常利用:
iPhone → Tailscale → 自宅Mac mini

App Review:
審査員のiPhone → HTTPS → 独立した審査用バックエンド
```

- 現在の最優先は、MediaVaultのoriginal退避・検証を維持した動画転送・軽量化・返却・保存の一周を完成させること
- クラウド構築はMac mini版完成後に行う
- 現在のTailscale前提実装は維持する
- 接続先を固定値から設定値へ変更する
- LUTはIdentity LUT、テストLUT、Apple Log → Rec.709の順で実装する
- Apple Logまたは選択LUTが未登録の場合は、未変換であることを明示した`compress-only` previewを返す
- 自宅Mac miniを審査員へ公開しない
- 審査用には独立した処理環境を用意する
- 審査用クラウドでは新機能を開発せず、完成したMac mini版を移植する
- LUT機能は審査時から確認可能にする
- Apple公式LUTは利用条件を確認するまで同梱しない
- 本命は、自前生成変換＋ユーザー追加LUT
- iPhoneアプリ変更は再申請が必要
- バックエンド内部の改善は通常、再申請不要
- バックエンドから未審査の全く新しい主要機能を追加しない
- 毎週の再ビルド・再署名運用は採用しない

---

## 12. 注意書き

本ドキュメントは、現時点の技術設計とApp Store審査対応方針を整理したものであり、法的助言ではない。

Appleの契約、App Review Guidelines、配布方式、各パッケージの利用条件は更新される可能性がある。申請時および正式採用時には、最新の公式条件を再確認すること。
