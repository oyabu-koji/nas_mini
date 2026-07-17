# MediaVault プロダクト要求定義書

## プロダクト概要

### 名称

**MediaVault** - iPhoneメディアをMac miniへ安全に退避し、preview確認できる個人向け保管アプリ

### コンセプト

- originalファイルを改変せず、外部SSDへ保管する。
- ファイル完全性と内容確認を別の状態として扱う。
- Mac mini側previewで保存内容を確認した後、ユーザーの明示操作でiPhone側originalを削除できるようにする。
- Phase 1で操作体験を検証し、Phase 2で大容量ProRes/LOG動画の安全転送を実現する。

### 対象ユーザー

- iPhoneで写真・動画を多く撮影する個人ユーザー。
- ProRes/LOGなど大容量素材を扱うユーザー。
- LANまたはTailscale private network上のMac miniと外部SSDを保管先にしたいユーザー。

## 解決する課題

- iPhone内の大容量メディアが端末容量を圧迫する。
- 退避後にoriginalが保存されたか、内容が意図した素材かを確認しづらい。
- ファイル完全性、preview確認、安全削除候補の意味が混ざると誤削除につながる。

## 不変条件

- originalファイルは改変しない。
- iPhone内の素材を自動削除しない。削除はpreview確認後にユーザーが明示実行する場合のみ許可する。
- Backend側original削除とiPhone側original削除を混同しない。Backend側originalは保持する。
- 保存先ルートは環境変数 `MEDIA_ROOT` で指定し、ハードコードしない。
- Phase 1では `safe_to_delete_candidate` を本番運用しない。
- Phase 2以降でも、安全条件をすべて満たす場合のみ削除候補にする。iPhone側original削除も自動化しない。

## Phase 1 MVP

### 目的

`104857600 bytes`以下の検証用素材で、iPhoneからアップロードし、Mac mini側でSHA256記録とpreview生成を行い、iPhone側で内容確認する一連の操作体験を検証する。

### 対象範囲

- iPhoneライブラリから写真・動画を選択する。
- 取得可能な撮影日時、位置情報、EXIFを送信する。`taken_at` は秒精度の ISO 8601 datetime、欠落または正規化不能な値はnullableとする。
- LOG素材かどうかをユーザーが選択する。
- FastAPI backendへ通常アップロードする。
- `${MEDIA_ROOT}/originals/` にoriginalを保存する。
- Mac mini側でSHA256を計算し、`server_hash_recorded` として記録する。
- ffmpeg preview生成jobを登録・実行する。
- LOG指定素材は、Apple Log対応featureで正式なRec.709 LUTを適用する。安全ゲート中は変換済みpreviewを生成・配信せず、preview失敗として扱う。
- 写真previewはJPEG、長辺2048px上限、縦横比維持、EXIF orientation反映で生成する。
- iPhoneアプリでpreviewを再生し、`review_status = preview_confirmed` に更新する。
- preview確認後、ユーザー明示操作によるiPhone側original削除導線を提供する。
- Backend URLと固定APIトークンをSettingsから設定する。
- Phase 1のBackend URLは、LANまたはTailscaleで到達可能なprivate endpointとする。
- Tailscale利用時のBackend URLは`http://<tailscale-ip>:8000`または`http://<magicdns-name>:8000`を想定する。

### Phase 1対象外

- `104857600 bytes`を超える素材の本番退避。
- chunk upload、resume upload、chunk hash verification。
- end-to-end hash verification。
- 安全削除候補の本番運用。
- iPhone側originalの自動削除。
- Backend側original削除。
- AI解析、original再ダウンロード、外部SSD管理UI。

## Phase 2 必須拡張

Phase 2は、削除候補を有効化する前に大容量素材の保存完全性とApple Log previewの正しさを段階的に確立する。

### Phase 2A: 大容量安全転送

- upload sessionを作成する。
- chunk単位でuploadし、chunk hashを照合する。
- Wi-Fi切断後にresume可能にする。
- 全chunk完了後にファイルを結合する。
- 結合後ファイルのSHA256を計算・記録する。
- iPhone側`expected_file_sha256`とMac mini側`server_sha256`を照合する。
- original確定保存と`file_verified`を、preview jobを登録できる前提状態として扱う。
- 新規動画の`POST /assets/upload`は`409 video_session_required`で拒否し、imageだけPhase 1 direct uploadを継続する。
- session作成、chunk再送、finalizationを冪等にし、finalizationはleaseを持つ`upload_finalize` jobで実行・回復する。
- この段階では`safe_to_delete_candidate`を有効化しない。

### Phase 2B: Apple Log preview

- original確定保存後に、動画metadataからApple Logを高信頼で自動判定する。
- Apple Logを検出した素材は、利用可能なら`generated-apple-log-rec709`を既定プリセットとして要求する。自前生成変換または利用条件を確認済みの公式LUTだけを、このプリセットに登録する。
- 要求プリセットが未登録または無効化済みの場合は、`compress-only`で軽量化した未変換previewを`done`かつ`preview_ready`として返す。画面はApple Log未変換であることを表示し、Rec.709変換済みとは表示しない。
- 登録済みLUTのmanifest検証、SHA-256、形式、FFmpeg適用が失敗した場合だけ、jobと`preview_status`を`failed`にする。判定不能または未対応profileも、色変換を要求せず`compress-only`で内容確認可能なpreviewを生成する。
- Apple LogのRec.709変換、未変換fallback、custom LUTのいずれも、要求プリセット、適用プリセット、version、SHA-256、`color_transform_status`、必要時のerror codeをpreview provenanceとして記録する。未変換Apple Logは`transform_kind = none`とし、`color_transform_error_code = lut_preset_unavailable`を使う。
- Phase 2B rolloutでは、Phase 2A session由来で`file_verified`の動画だけを一意なprofile-aware preview jobへ移行する。新jobのdedup insertに成功したときだけasset preview generationとformal preview/review stateを更新し、旧generation jobはassetを書き換えない。Phase 1 direct assetは対象外とする。
- Mac mini管理者はrepo外のLUT rootにmanifest付きcustom LUTを登録できる。Mobileはサーバーが返す有効なプリセットだけを選択でき、LUTファイルをuploadしない。custom LUTはApple Log to Rec.709とは表示しない。

### Phase 2C: 安全削除候補

- `upload_sessions.status = completed`、全`upload_chunks.status = verified`、`assets.verification_status = file_verified`、`transform_kind = lut`または`none`の正式provenance付き`assets.preview_status = preview_ready`、`assets.review_status = preview_confirmed`をすべて満たす場合のみ`safe_to_delete_candidate`にする。Apple Logの`compress-only` fallbackも、未変換表示と`none` provenanceを持つ場合はこの条件に含める。

## Phase 3+ Backlog

- Wi-Fi/充電中のみ同期
- originalダウンロード
- LUT presetの管理UI/API、有効化、廃止管理
- 顔検出、笑顔判定、ピント/ブレ判定、ベストショット抽出
- 動画シーン解析、AIタグ付け
- FCPXML出力
- Mac miniからMBAへのoriginal取得

## 主要ユーザーストーリー

### P0: 素材退避

ユーザーとして、iPhone容量を空ける準備のために、選択した写真・動画をMac miniへ退避したい。

**受け入れ条件**

- `104857600 bytes`以下の素材を選択できる。
- Mobileとbackendの両方で超過素材を拒否する。
- originalが`${MEDIA_ROOT}/originals/` に保存される。
- originalのSHA256がMac mini側で計算・記録される。
- originalがpreview生成処理で変更されない。

### P0: preview確認

ユーザーとして、退避した素材が意図した内容か確認するために、iPhoneでpreviewを再生したい。

**受け入れ条件**

- ffmpegでH.264 MP4 previewを生成できる。
- previewは縦横比を維持し、1080pを上限とする。
- 音声がある場合はAAC音声を含む。
- Phase 2B以降、Apple Logと高信頼で判定された素材は、利用可能なら正式なRec.709変換を適用できる。要求LUTが未登録または無効化済みなら、未変換であることを表示した`compress-only` previewを確認できる。登録済みLUTの検証・適用失敗だけはpreviewをfailedにする。
- 写真previewはJPEG、長辺2048px上限、EXIF orientation反映で生成できる。
- 確認後に`review_status = preview_confirmed`となる。

### P0: preview確認後のiPhone側original手動削除

ユーザーとして、Mac mini側に保存された軽量previewを確認した後、iPhone容量を空けるためにiPhone側originalを自分の操作で削除したい。

**受け入れ条件**

- `preview_status = preview_ready`かつ`review_status = preview_confirmed`のassetだけ削除操作を表示する。
- 削除前に対象asset、filename、撮影日時など確認に必要な情報を表示する。
- 削除はユーザーの明示確認後に、iPhone写真ライブラリ上のlocal asset identifierを使って実行する。
- 削除操作はiPhone側originalだけを対象とし、Backend側originalやderived fileは削除しない。
- 権限拒否、ユーザーキャンセル、local asset不在の場合は失敗または未実行として表示し、Backend側statusを壊さない。
- Phase 1の手動削除は`safe_to_delete_candidate`を必須条件にしない。Phase 2以降の削除候補判定は、より強いhash verification条件として扱う。

### P0: Private endpointアクセス制御

ユーザーとして、LANまたはTailscale private network上の別端末から無制限に閲覧・uploadされないよう、backendへのアクセスを制限したい。

**受け入れ条件**

- Backend URLを手入力できる。
- 固定APIトークンを設定できる。
- 初期リリースではURLとトークンを手入力し、URL・名称は通常設定保存、トークンは`expo-secure-store`へ分離保存する。
- 接続先は固定値にせず、将来の複数サーバー設定に対応する。自宅利用はLAN/Tailscale private endpoint、App Review用の独立環境はHTTPS endpointとする。
- API要求は`Authorization: Bearer <token>`形式を使う。
- uploadとpreview APIはトークンなしの要求を拒否する。
- `/jobs`, `/jobs/{job_id}`を含む全Phase 1 APIはトークンなしの要求を拒否する。
- Tailscaleは通信経路を提供するだけで、固定APIトークン認証の代替にはしない。
- Phase 1ではTailscale private network内のHTTP endpointを許容する。
- 公開インターネットへbackendを晒す場合はPhase 1対象外とし、HTTPSを必須にする。

## 状態モデル

| 分類 | Phase 1で使用する状態 | Phase 2以降で追加する状態 |
|------|----------------------|--------------------------|
| `transfer_status` | `local_only`, `uploading`, `uploaded`, `failed` | 継続利用 |
| `verification_status` | `not_started`, `server_hash_recorded`, `failed` | `file_verified` |
| `preview_status` | `not_started`, `preview_generating`, `preview_ready`, `failed` | 継続利用 |
| `review_status` | `not_reviewed`, `preview_confirmed` | 継続利用 |
| `delete_candidate_status` | `not_candidate` | `safe_to_delete_candidate` |
| `local_delete_status` | `not_deleted`, `delete_requested`, `deleted`, `failed` | 継続利用 |
| `color_transform_status` | - | `not_requested`, `unavailable`, `applied`, `failed` |

## 非機能要件

### 信頼性

- originalはderived fileと別ディレクトリへ保存する。
- original保存後のpreview生成はoriginalを読み取り専用入力として扱う。
- Phase 1ではSHA256をサーバー側で計算・記録するが、end-to-end検証済みとは表示しない。
- iPhone側original削除はpreview確認後のユーザー操作に限定し、バックグラウンドで自動実行しない。
- 外部SSD未接続、容量不足、ffmpeg失敗をエラーとして記録する。

### セキュリティ

- `/assets/upload`, `/assets`, `/assets/{asset_id}`, `/assets/{asset_id}/preview`, `/assets/{asset_id}/preview-confirmation`, `/jobs`, `/jobs/{job_id}`は固定APIトークンを要求する。
- トークンや機密値をログへ出力しない。
- 保存パスはbackend側で生成し、クライアント由来のパスを信用しない。
- Phase 1のHTTP通信はLANまたはTailscale private network内に限定する。
- Tailscale利用時も固定APIトークンを必須とし、Tailnet参加だけでは認可済みと扱わない。
- 自宅Mac miniのbackendを公開インターネットへ直接公開しない。App Review用には自宅環境とデータを分離したHTTPS backendだけを使用する。

### 運用

- DockerをMac mini移行時の正規実行環境とする。
- ローカル`node_modules`をDockerへ持ち込まない。
- 開発中のiPhone実運用はDevelopment Buildを使う。配布はApp StoreまたはUnlisted Appを目標とし、審査時は独立したHTTPS backendを用意する。
- 開発中はMBA上のbackendをTailscale経由でiPhoneから確認し、Mac mini移行後はBackend URLをMac miniのTailscale IPまたはMagicDNS名へ差し替える。

## 未決事項

- preview bitrate。
- Apple公式LUTの配布元・利用条件、自前生成変換の入力仕様・metadata検出根拠・品質閾値・SHA-256の確定値。
- Docker Compose上のworker service詳細設定。
- 将来のLAN/Tailscale endpoint discovery。
- iOS/ExpoでHTTP private endpointへ接続するためのapp config詳細。
- `expo-media-library` で取得可能なEXIF/location項目。
- thumbnail/proxy生成はPhase 1では本番対象外とし、将来候補として扱う。
- ffmpeg失敗時のretry回数。
