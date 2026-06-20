# MediaVault 用語集

## ドメイン用語

### original

iPhoneから退避した元の写真・動画ファイル。`MEDIA_ROOT/originals/`に保存し、改変しない。

### preview

iPhoneで内容確認するために生成するderived file。Phase 1の動画previewはH.264 MP4、AAC音声、1080p上限、写真previewはJPEG、長辺2048px上限、EXIF orientation反映とする。

### derived file

originalから生成した別ファイル。`preview`, `thumbnail`, `proxy`, `lut_preview`を含む。originalとは分離して保存する。

### LOG素材

ユーザーがLOGとして指定した動画素材。Phase 1では自動判定せず、preview生成時にRec.709変換用LUTを適用する。

### safe delete candidate

将来、iPhone側originalの削除候補として提示可能な状態。自動削除を意味しない。Phase 1では本番運用しない。

## 技術用語

### MEDIA_ROOT

Mac miniに接続した外部SSD上の保存先ルートを指定する環境変数。例: `/Volumes/MediaVault`。

### SHA256 record

Phase 1でMac mini側が計算・記録するSHA256。iPhone側期待値との照合をしないため、end-to-end検証済みとは扱わない。

### hash verification

Phase 2以降でchunk hashや結合後ファイルhashを照合し、完全性を確認する処理。

### Rec.709 LUT

LOG指定素材のpreviewを確認しやすい色へ変換するためのLook-Up Table。originalには適用しない。

### Development Build

Expo Goでは足りない実運用向け権限や動作を検証するためのアプリbuild。Apple Developer Programを前提とする。

## エンティティ

### asset

originalと関連metadata、分離statusを表す。

### derived_files

assetから生成したpreview等のファイルを記録する。

### jobs

preview生成や将来のAI解析処理を永続化して管理する。

### upload_sessions

Phase 2で導入するchunk upload単位のsession。

### upload_chunks

Phase 2で導入する個別chunkとhash照合結果。

### upload session status

| 値 | 意味 |
|----|------|
| `created` | session作成済み |
| `uploading` | chunk upload中 |
| `assembling` | chunk結合中 |
| `completed` | 全chunk検証と結合が完了 |
| `failed` | session処理失敗 |

### upload chunk status

| 値 | 意味 |
|----|------|
| `pending` | 未upload |
| `uploaded` | upload済み、hash照合前 |
| `verified` | chunk hash照合済み |
| `failed` | uploadまたはhash照合失敗 |

## Status

### transfer_status

| 値 | 意味 |
|----|------|
| `local_only` | iPhone側にのみ存在する |
| `uploading` | 転送中 |
| `uploaded` | Mac mini側へ転送済み |
| `failed` | 転送失敗 |

### verification_status

| 値 | 意味 |
|----|------|
| `not_started` | SHA256記録または検証未開始 |
| `server_hash_recorded` | Phase 1でMac mini側SHA256を記録済み |
| `chunk_verified` | Phase 2でchunk hash照合済み |
| `file_verified` | Phase 2で結合後ファイルを検証済み |
| `failed` | SHA256計算または検証失敗 |

### preview_status

| 値 | 意味 |
|----|------|
| `not_started` | preview生成未開始 |
| `preview_generating` | preview生成中 |
| `preview_ready` | preview利用可能 |
| `failed` | preview生成失敗 |

### review_status

| 値 | 意味 |
|----|------|
| `not_reviewed` | ユーザー未確認 |
| `preview_confirmed` | iPhoneで内容確認済み |

### delete_candidate_status

| 値 | 意味 |
|----|------|
| `not_candidate` | 削除候補ではない |
| `safe_to_delete_candidate` | Phase 2以降で安全条件を満たした削除候補。自動削除ではない |

### job status

| 値 | 意味 |
|----|------|
| `queued` | 実行待ち |
| `running` | 実行中 |
| `done` | 成功 |
| `failed` | 失敗 |

## 略語

### MVP

Minimum Viable Product。MediaVaultでは`104857600 bytes`以下の通常uploadからpreview確認までを指す。

### EXIF

Exchangeable Image File Format。撮影日時や位置情報等のmetadata。取得できない項目はnullableとする。

### LUT

Look-Up Table。LOG previewのRec.709変換に使う。

### LAN

Local Area Network。Phase 1ではiPhoneからMac mini backendへ接続するネットワーク範囲。
