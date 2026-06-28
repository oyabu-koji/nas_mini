# MediaVault アーキテクチャ設計書

## 前提と方針

- Mobile AppはReact Native + Expo managed workflow + JavaScriptで実装する。
- BackendはFastAPI、DBはSQLite、preview生成はffmpegを使う。
- Mac mini移行時はDocker内の実行環境を正とする。
- Phase 1のiPhone-backend通信は、LANまたはTailscale private network上のHTTP endpointと固定APIトークンを使う。
- 公開インターネットへbackendを直接公開しない。公開運用する場合はPhase 1対象外とし、HTTPSを必須にする。
- 外部SSDの保存先ルートは`MEDIA_ROOT`で指定する。
- originalはimmutableとして扱い、derived fileと分離する。
- Phase 1は`104857600 bytes`以下の通常uploadによるUX検証、Phase 2は大容量安全転送とする。

## コンテキスト図

```mermaid
graph LR
    iPhone[iPhone / MediaVault App]
    Photos[iPhone Photos Library]
    Tailnet[Tailscale private network / LAN]
    API[FastAPI API]
    Worker[Preview Job Worker]
    DB[(SQLite)]
    SSD[External SSD]
    FFmpeg[ffmpeg]

    iPhone --> Photos
    iPhone -->|HTTP + Token| Tailnet
    Tailnet --> API
    API --> DB
    API --> SSD
    API --> Worker
    Worker --> FFmpeg
    FFmpeg --> SSD
    Worker --> DB
```

## テクノロジースタック

| 分類 | 技術 | 方針 |
|------|------|------|
| Mobile runtime | Node.js 24, Expo SDK 54 | `.nvmrc`とdevcontainerをNode 24で統一 |
| Mobile UI | React Native, JavaScript | TypeScriptは明示依頼なしに導入しない |
| Device API | `expo-media-library` | Expo関連依存は`npx expo install`で追加 |
| Backend | Python, FastAPI | private endpoint APIとjob登録を担当 |
| Private network | Tailscale, LAN | Phase 1のiPhone-backend到達経路 |
| Backend dependency manager | uv | `pyproject.toml`と`uv.lock`で依存を固定 |
| DB | SQLite | 個人利用MVPに十分。migration方針は実装時に確定 |
| Preview | ffmpeg | originalを読み取り入力としてderived fileを生成 |
| Deployment | Docker on Mac mini | ホストNodeへ依存しない |
| Storage | External SSD | `MEDIA_ROOT`配下に保存 |

## コンポーネント責務

### Mobile App

- 写真・動画選択、nullable metadata取得、LOG指定。
- Backend URLと固定APIトークンの設定。
- Tailscale IPまたはMagicDNS名を含むprivate endpoint URLの設定。
- upload進捗、asset状態、preview表示、確認操作。
- 自動削除は実行しない。
- preview確認後にユーザーが明示操作した場合のみ、iPhone写真ライブラリ上のoriginal削除を端末service経由で実行する。
- Backend側original、derived file、asset DB recordはMobileの削除操作では削除しない。

### FastAPI API

- Token認証。
- upload size/type検証。
- 安全なファイル名と保存パスの生成。
- original保存、SHA256計算、SQLite記録。
- preview job登録、asset/job参照、preview配信、確認済み更新。

### Job Service

- job状態を`queued`, `running`, `done`, `failed`で管理する。
- Phase 1はpreviewとlut_previewを処理する。
- Phase 3+でAI解析jobを追加可能にする。

### Preview Adapter

- originalを改変しない。
- H.264 MP4、AAC音声、1080p上限でpreviewを生成する。
- LOG素材には`backend/assets/lut/rec709.cube`を既定とするRec.709 LUTを適用する。
- 写真はJPEG、長辺2048px上限、縦横比維持、EXIF orientation反映でpreviewを生成する。
- Phase 1でHEIC、JPEG、PNG入力の検証fixtureを用意し、Docker内ffmpeg buildのcodec対応を確認する。
- stdout/stderrを安全に扱い、機密値をログへ含めない。

## データ管理

### SQLite

- DBファイル配置はbackend設定で指定する。
- assets、derived_files、jobsをPhase 1で作成する。
- upload_sessions、upload_chunksはPhase 2で追加する。
- statusは一つの列へ集約せず、役割ごとに分離する。

### Mobile Local State

- backend asset idとiPhone写真ライブラリのlocal asset identifierを紐づける。
- iPhone側original手動削除の状態はMobile側で管理し、Backend側originalの状態と混同しない。
- local asset identifierは端末内の素材削除にのみ使い、backendへ保存先pathとして送らない。

### External SSD

```text
${MEDIA_ROOT}/
├── originals/
├── previews/
├── thumbnails/
├── jobs/
└── tmp/
```

- `originals/`: immutable original。
- `previews/`, `thumbnails/`: derived file。
- `tmp/`: upload中、一時生成中のファイル。
- `jobs/`: 必要なjob関連ファイル。DB jobレコードと役割を混同しない。

## ファイル保存フロー

1. uploadは`tmp/`へ保存する。
2. size/typeを検証する。
3. backend側生成パスで`originals/`へ確定保存する。
4. Mac mini側でSHA256を計算する。
5. assetsを記録し、preview jobを登録する。
6. ffmpegはoriginalを読み取り、previewを別パスに生成する。
7. derived_filesを記録する。

## iPhone側original手動削除フロー

1. ユーザーがMac mini側previewを再生し、内容確認する。
2. Mobileは`POST /assets/{asset_id}/preview-confirmation`で`review_status = preview_confirmed`にする。
3. Mobileは`preview_ready`かつ`preview_confirmed`のassetだけ削除操作を表示する。
4. ユーザーが対象情報を確認し、削除を明示実行する。
5. Mobileは`expo-media-library` service経由でiPhone写真ライブラリ上のlocal original削除を要求する。
6. 成功または失敗はMobile local stateへ反映する。Backend側originalは保持する。

## セキュリティ

- Phase 1でも固定APIトークンを必須にする。
- Tokenは環境変数でbackendへ渡し、ログに出さない。
- API要求は`Authorization: Bearer <token>`形式とする。
- Mobile側のTokenは`expo-secure-store`へ保存する。平文ハードコードは禁止する。
- クライアント由来のファイルパスを使用しない。
- Path traversalを防ぐため、保存先パスはbackend側で構成する。
- LANまたはTailscale private network内運用でも認証を省略しない。
- Tailscale private network内のHTTP endpointでも認証を省略しない。
- Tailscaleは到達経路であり、固定APIトークン認証の代替ではない。
- Phase 1で許容するHTTPはLANまたはTailscale private network内に限定する。
- iPhoneから接続するbackendは`127.0.0.1`ではなく、Tailscale IP、MagicDNS名、またはLAN IPで指定する。
- iPhone側original削除はユーザー確認を必須とし、background jobや自動同期で実行しない。

## 信頼性

- 外部SSD未接続時はupload開始前または保存時に明示的に失敗する。
- 容量不足、I/O失敗、ffmpeg失敗をjob/asset状態に反映する。
- Phase 1 SHA256はサーバー側計算・記録であり、end-to-end検証とは表示しない。
- Phase 2ではchunk hashと結合後hashを照合する。
- Phase 2ではiPhone側`expected_file_sha256`とMac mini側`server_sha256`が一致した場合のみ`file_verified`とする。
- iPhone側original削除の失敗、権限拒否、ユーザーキャンセルはBackend側保存済みassetの状態を壊さない。

## Docker方針

- Mac miniではDockerを正規実行環境とする。
- Node 24、Python、ffmpegのバージョンはDocker側で固定する。
- Backend Python依存は`uv.lock`を使ってDocker内で再現可能にinstallする。
- Backend imageへ`backend/assets/`をcopyし、workerが`/app/assets/lut/rec709.cube`を読めるようにする。
- ローカル`node_modules`をDockerへコピーしない。
- 外部SSDはcontainerへvolume mountし、container内の`MEDIA_ROOT`へ割り当てる。
- host上の`/Volumes/MediaVault`などのパス差分はcompose環境変数で吸収する。

## Phase 1 Job方針

- jobはSQLiteへ永続化する。
- Phase 1はAPIと単一workerを使い、SQLiteはWAL modeと`busy_timeout = 5000ms`を設定する。
- DBファイルはDocker volumeで永続化する。
- workerはSQLite transactionで`queued` jobをatomic claimする。
- workerは自身が処理可能なjob typeだけをclaimする。processor未実装のjobは`queued`のまま残す。
- jobは`claimed_at`と`lease_expires_at`を持ち、期限切れ`running` jobを`queued`へ回収する。
- Dockerではworkerを独立serviceとして起動し、`restart: unless-stopped`を設定する。
- 理由: ffmpeg処理をAPI request lifecycleから分離し、将来のAI jobへ拡張しやすくするため。

## 品質確認

- Mobile: `npx expo install --check`, `npm run lint`, `npm test`, `npx expo start`
- Backend: `uv run pytest`を標準のtest commandとし、lintを導入した場合も`uv run ...`で実行する。
- 実機: Development Buildでライブラリアクセス、TailscaleまたはLAN経由のHTTP通信、preview再生、iPhone側original手動削除の権限/キャンセルを確認する。

## Open Questions

- Docker Composeの具体構成とMac miniのSSD mount path。
- HTTP/HTTPSと将来のLAN/Tailscale endpoint discovery。
- iOS/ExpoでTailscale private endpointのHTTP通信を許可するapp config詳細。
