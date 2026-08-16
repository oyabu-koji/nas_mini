# MediaVault 用語集

## ドメイン用語

### original

iPhoneから退避した元の写真・動画ファイル。`MEDIA_ROOT/originals/`に保存し、改変しない。

### preview

iPhoneで内容確認するために生成するderived file。Phase 1の動画previewはH.264 MP4、AAC音声、1080p上限、写真previewはJPEG、長辺2048px上限、EXIF orientation反映とする。0.4.0のApple Log 1/2は、未変換であることをversion別に表示した`compress-only` previewを返す。

### derived file

originalから生成した別ファイル。`preview`, `thumbnail`, `proxy`, `lut_preview`, `rendition`を含む。originalとは分離して保存する。

### LOG素材

same-fd bounded container signalとallowlist済みFFprobe color fieldsから、承認済みdetector-v2 ruleの完全一致で`apple_log`と判定された動画素材。`source_profile`は`apple-log-1`又は`apple-log-2`だけを許可する。filename、codec、`bt2020nc`単独、pixel、legacyのユーザー指定LOGフラグは正式な判定根拠にしない。

### Apple Log 1

選択video trackのauthoritative `logs` identifierがexact `com.apple.rec2020.apple-log`で、track IDとprofile別color allowlistも整合したApple Log profile。`source_profile = apple-log-1`、requested presetは`generated-apple-log-rec709`とする。0.4.0では変換LUTを適用しない。

### Apple Log 2

選択video trackのauthoritative `logs` identifierがexact `com.apple.apple-wide-gamut.apple-log`で、track IDとprofile別color allowlistも整合したApple Log profile。`source_profile = apple-log-2`、requested presetは`generated-apple-log2-rec709`とする。0.4.0では変換LUTを適用しない。

### `logs`

本プロジェクトのdetector-v2が、選択video trackのsupported visual sample description直下だけで読むApple Log identifier box。payloadは1..128 byteのNULなしASCIIとする。`mdat`、`hoov`、audio/別track、path外の同名boxやwhole-file byte searchはauthorityにしない。box配置とserializationはreal/synthetic fixtureでversion管理するproject-owned observation contractである。

### container signal

bounded ISO BMFF parserが返すclosed result。recognized/no/unknown/conflicting `logs`、unsupported container、invalid、resource limitを区別し、matched resultだけが内部track IDを一時保持する。track ID、unknown identifier text、raw box/pathはcanonical evidenceとAPIへ出さない。

### unconverted preview

Apple Log 1/2を色変換せずH.264/AAC等のpreview制約へ軽量化した`compress-only` derived file。formal provenanceはprofile別requested presetに対し、`applied_preset_id = compress-only`、`transform_kind = none`、`color_transform_status = unavailable`、`color_transform_error_code = lut_preset_unavailable`、applied LUT identity nullを持つ。Mobile表示は`Apple Log 1 (unconverted)`又は`Apple Log 2 (unconverted)`とし、Rec.709変換済みを意味しない。

### safe delete candidate

Phase 2Cで、completed session、exact verified chunk ledger、whole-file identity、current formal
preview provenance、preview確認をBackend evaluatorが検証したstored projection。
自動削除やBackend media削除を意味せず、Phase 1 direct assetには付与しない。

### iPhone側original手動削除

Mac mini側previewを確認した後、ユーザーが明示操作してiPhone写真ライブラリ上のoriginalを削除すること。
共通条件に加え、Phase 1 direct assetは`server_hash_recorded`、Phase 2 session videoは
`video + file_verified`、formal/safe-delete capability、ready formal preview、
`safe_to_delete_candidate`を根拠にする。
Backend側originalやderived fileを削除する操作ではない。

### Phase 1 direct asset

通常`POST /assets/upload`で保存され、`verification_status = server_hash_recorded`を持つ
image又はhistorical video。original削除eligibilityではPhase 2B capabilityやformal previewを要求しない。

### original deletion eligibility

`preview_ready`、`preview_confirmed`、local mapping available、未削除、非busyを共通条件とし、
asset originごとの追加条件を評価するMobileのpure predicate。Phase 2 session videoではBackend
candidateと両capabilityを要求する。managed rendition、保存済みcopy、legacy `is_log`、
active processed resultをcandidateの代替根拠にしない。

### internal job

BackendのworkerがSQLite上でclaim/leaseし、preview、upload finalize、rendition等を実行する内部model。
初期リリースではpublic `/jobs` APIや専用Upload Queue画面を持たない。

## 技術用語

### MEDIA_ROOT

Mac miniに接続した外部SSD上の保存先ルートを指定する環境変数。例: `/Volumes/MediaVault`。

### SHA256 record

Phase 1でMac mini側が計算・記録するSHA256。iPhone側期待値との照合をしないため、end-to-end検証済みとは扱わない。

### hash verification

Phase 2以降でchunk hashや結合後ファイルhashを照合し、完全性を確認する処理。

### Rec.709 LUT

将来Apple Log previewをRec.709へ変換するためのLook-Up Table。originalには適用しない。Apple Log 1/2では異なるLUTを前提とし、generator又はsource、version、SHA-256、profile対応を記録する必要がある。identity LUTをRec.709変換用に使わない。0.4.0ではApple Log用LUTを生成・登録・適用しない。

### LUT registry

管理manifestを持つserver presetの集合。virtualな`compress-only`、repository内のgenerated identity/test、将来の自前生成又は承認済みApple Log to Rec.709 preset、Mac mini側`USER_LUT_ROOT`に登録したcustom LUTを統合する。各LUTはpreset id、version、SHA-256、由来、利用条件の参照を持つ。Mobileは有効presetを選択できるが、LUTファイルをuploadしない。

### compress-only

LUTを適用せずにH.264/AAC等のpreview制約へ軽量化するserver preset。要求LUTが未登録または無効化済みの場合のfallbackとしても使う。Apple Logで使う場合はRec.709変換済みとは表示しない。

### color transform status

preview又はmanaged renditionに対する色変換の結果。`not_requested`、`unavailable`、`applied`、`failed`を使う。`unavailable`は要求presetが未登録または無効化済みのため`compress-only`を適用した成功状態であり、job失敗ではない。この場合の`color_transform_error_code`は`lut_preset_unavailable`に固定する。

### requested preset

Mobileがmanaged renditionへ明示要求した、又はApple Log自動判定がformal previewへ要求したserver preset。Apple Log 1は`generated-apple-log-rec709`、Apple Log 2は`generated-apple-log2-rec709`へ固定する。実際には`compress-only` fallbackとなってもrequested identityはprovenanceへ残す。

### applied preset

workerがpreview又はrendition生成時に実際に使ったserver preset。要求presetが未登録または無効化済みの場合は`compress-only`となる。LUTを適用した場合はversionとSHA-256もprovenanceへ残す。

### detector manifest

人手承認済みdetector rule input v2を、repo外のユーザー所有Apple Log 2/ordinary動画とproject-owned synthetic Apple Log 1 container、same-fd bounded parser、pinned Docker FFprobeで検証して作るstrict JSON v2。rule/parser/FFprobe、exact identifier/profile/preset mapping、resource limits、fixture evidence identity、canonical digestを持つ。path、filename、raw metadataは持たない。manifest、certificate summary、successor 010 schema identityが有効になるまでformal preview capabilityを公開しない。

### detector rule input

repository ownerが判定実装前に人手で作成・承認するcanonical JSON v2。parser contract version、exact Apple identifier/profile/preset mapping、profile別FFprobe allowlist、not-log predicate、hard limits、Apple公式source URL、確認日時、承認role/referenceを持つ。certification scriptはこのruleを検証するだけで、fixture比較から追加・変更しない。

### formal preview attempt

session由来videoの`preview_generation`ごとに一意な処理record。detector manifest/rule/evidenceと自動要求presetのimmutable snapshot、job、phase、nullable resultを持つ。identity/test/customのmanaged rendition recordとは別であり、lease recoveryでもsnapshotを再解決しない。

### preview provenance

derived previewに一対一で紐づくformal record。`transform_kind`、source profile、target profile、detector rule version、evidence digest、生成日時、要求・適用preset、version、SHA-256、`color_transform_status`、nullableな未適用/失敗理由を保存する。0.4.0はApple Log 1/2のprofile別`compress-only` unavailable tupleと、非Log/判定不能の`compress-only` not-requested tupleだけをruntime authorityにする。DBが将来のapplied tupleを表現できても0.4.0では拒否する。identity/test/customは別のrendition provenanceを持ち、formal recordへ昇格しない。

### original finalization

Phase 2Aでchunk結合、hash照合、確定保存が完了し、preview jobが入力として利用できる状態。sessionごとの`upload_finalize` jobがlease/recoveryを担い、promote済みfileとSQLite commitの不整合を復旧する。未完了またはhash不一致のoriginalからpreviewを生成しない。

### preview generation

Phase 2Bでsession由来videoのformal previewを無効化するたびにassetへ記録する単調増加番号。formal result、formal attempt、formal provenance、preview jobは同じ番号を持つ。managed rendition resultでは常にnullとし、別の`rendition selection generation`を使う。番号が一致しない旧preview jobは、attemptがあれば`superseded`、jobは`failed` + `preview_generation_superseded`となってleaseをclearし、asset、formal/managed preview、review stateを変更しない。

### managed preset

Phase 2AでMac miniが所有・検証し、versioned catalogへsafe metadataだけを公開する処理preset。`compress-only`、generated identity/test、repo外customを含む。identity/test/customはApple Log又はRec.709変換済みを意味しない。

### registry classification

要求presetのserver registry上の状態。`absent`、`disabled`、`registered_invalid`、`valid`の4値を使う。`absent`と`disabled`だけが`compress-only`成功fallbackの対象で、登録破損を示す`registered_invalid`はterminal failureにする。

### rendition

eligibleな通常videoのimmutable originalから、ユーザーが選んだmanaged presetで明示生成する新しいvideo derived result。client request ID、selection generation、job、phase、nullable resultを持つ。formal preview、preview確認、original削除条件は変更しない。

### rendition selection generation

assetで新しいmanaged preset renderを明示するたびに増える単調増加番号。current generationの成功時だけactive processed resultを切り替え、失敗時は直前の成功済みactive resultを維持する。遅れて完了した古いgenerationは`superseded` auditになる。Phase 2Bの`preview generation`とは別物。

### immutable LUT snapshot

rendition作成時に保存したcanonical manifest identityと、workerがno-follow descriptorで開いたsourceからjob-private pathへcopyしたexact LUT bytesの組。FFmpeg直前にもsize/SHA-256を照合し、mutable registry pathを処理authorityとして読み直さない。

### rendition provenance

rendition、derived file、processed resultに一対一で紐づくimmutable record。要求・適用preset、registry classification、version、manifest/LUT SHA-256、transform kind/status/error、safe source/terms/target metadataを保持する。

### Development Build

Expo Goでは足りない実運用向け権限や動作を検証するためのアプリbuild。Apple Developer Programを前提とする。

### local asset identifier

`expo-media-library`が扱うiPhone写真ライブラリ上の素材識別子。iPhone側original手動削除に使う。Backend側保存pathやasset idとは別物として扱う。

### processed result

軽量化又は後続LUT renditionとして生成したvideo derived fileの不変な配信単位。opaqueな32桁lowercase UUID hexの`result_id`、asset、derived file、MIME、size、SHA-256、状態を持つ。`ready` resultだけがassetのactive pointerになれ、再renderでは新resultを作って旧resultを`superseded`として保持する。originalではない。

### processed result save store

iPhone写真ライブラリへのprocessed result保存だけを管理するMobile local store。`backend_asset_id`、`backend_result_id`、SHA-256、save state、保存済みcopyのlocal asset identifierだけを扱う。source originalの`mobile local asset mapping`と混用せず、token、URI、pathを保存しない。

### save outcome unknown

写真ライブラリnative saveの直前に保存するwrite-ahead状態。native call後にアプリが中断し成功可否を確認できない場合に残る。自動再保存やsource original削除を行わず、ユーザーが明示的に再試行する前に写真ライブラリを確認する。

## エンティティ

### asset

originalと関連metadata、分離statusを表す。

### mobile local asset mapping

backend asset idと`local asset identifier`をMobile側で紐づけるlocal state。iPhone側original手動削除の状態管理に使う。

### mapping unavailable

backend asset idに対応するlocal asset mappingを取得できない派生local状態。uploadやpreview確認の失敗ではないが、将来のiPhone側original削除操作は表示しない。backend asset idやfilenameからiPhone写真ライブラリの素材を推測しない。

### upload result unknown

Mobileがupload timeoutでrequestを中断した後、backendがoriginal保存とasset作成を完了したか判定できないUI状態。token、URI、filenameを含めず端末へ保存する。local asset idがある場合は同じ素材の再送を、ない場合はglobal pending markerで確認前のuploadを抑止し、asset一覧の確認済み操作で解除する。

### derived_files

assetから生成したpreview等のファイルを記録する。

### processed_results

`processed result`のBackend永続record。`derived_file_id`は一意で、current authorityは同一assetのready recordだけを指す。Phase 2Bではformal resultを`formal_preview_id`配下と一致するnon-null `preview_generation`、managed resultを`active_processed_result_id`が指す最新成功ready renditionと`preview_generation = null`で別々に識別する。より新しいfailed/superseded selectionはmanaged authorityを無効化しない。ready/superseded recordのidentity fieldsと参照derived fileは変更・削除できず、kindごとのgeneration/provenance一致を配信条件にする。

### renditions

managed rendition requestと永続phaseを表すBackend record。global uniqueなclient request ID、asset、job、selection generation、immutable preset snapshot、nullable resultを保持し、exact replayで同じrecordを返す。

### rendition_provenance

`rendition provenance`を保存するBackend record。rendition、processed result、derived fileとの一対一relationとimmutable identityをDB constraint/triggerで守る。

### jobs

preview、upload finalize、renditionや将来のAI解析処理を永続化するBackend内部model。
workerのclaim/lease/recoveryに使い、初期リリースのpublic APIや専用画面としては公開しない。

### upload_sessions

Phase 2で導入するchunk upload単位のsession。client idempotency key、immutable metadata、expected hash、expiry、failure/retry分類、finalization job/asset参照を持つ。

### upload_chunks

Phase 2で導入する個別chunkとhash照合結果。`(session_id, chunk_index)`は一意で、range、size、verified SHA256を持つ。

### upload session status

| 値 | 意味 |
|----|------|
| `created` | session作成済み |
| `uploading` | chunk upload中 |
| `assembling` | chunk結合中 |
| `completed` | 全chunk検証と結合が完了 |
| `failed` | session処理失敗。failure classでretryable/terminalを区別する |
| `cancelled` | ユーザーが未完了sessionをcancelした |
| `expired` | inactivity expiryによりtemporary chunkを削除した |

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
| `file_verified` | Phase 2で結合後ファイルを検証済み |
| `failed` | SHA256計算または検証失敗 |

### preview_status

| 値 | 意味 |
|----|------|
| `not_started` | preview生成未開始 |
| `preview_generating` | preview生成中 |
| `preview_ready` | provenance付きpreviewが利用可能。0.4.0のApple Log 1/2ではprofile別unconverted fallbackだけを許可する |
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
| `safe_to_delete_candidate` | Phase 2C evaluatorが完全なsession/formal/review relationを確認したstored projection。ユーザー明示操作の候補であり、自動削除ではない |

### local_delete_status

Mobile側で管理するiPhone側original手動削除の状態。Backend側asset statusではない。

| 値 | 意味 |
|----|------|
| `not_deleted` | iPhone側originalが未削除 |
| `delete_requested` | ユーザーが削除を要求し、端末側処理中 |
| `deleted` | iPhone側originalの削除が完了 |
| `failed` | 権限拒否、キャンセル、local asset不在などで削除未完了 |

### job status

| 値 | 意味 |
|----|------|
| `queued` | 実行待ち |
| `running` | 実行中 |
| `done` | 成功 |
| `failed` | 失敗 |

### rendition state

| 値 | 意味 |
|----|------|
| `queued` | rendition job実行待ち |
| `validating` | immutable preset/LUT snapshot検証中 |
| `rendering` | FFmpegでcandidate生成中 |
| `finalizing` | eligibility、provenance、result、active pointerを原子的に確定中 |
| `ready` | そのgenerationが成功し、active pointerに選ばれている間はcurrent managed resultとして利用可能 |
| `failed` | stable error codeを持つterminal failure。新resultは公開しない |
| `superseded` | 古いgenerationとして監査用result/provenanceを保持し、activeではない |

## 略語

### MVP

Minimum Viable Product。MediaVaultでは`104857600 bytes`以下の通常uploadからpreview確認までを指す。

### EXIF

Exchangeable Image File Format。撮影日時や位置情報等のmetadata。取得できない項目はnullableとする。`taken_at` に使う日時は秒精度の ISO 8601 datetime に正規化できる場合だけ送信する。

### LUT

Look-Up Table。Apple Log to Rec.709変換またはユーザーがMac mini側で管理するcustom color transformに使う。

### LAN

Local Area Network。Phase 1ではTailscale private networkと並ぶ、iPhoneからbackendへ接続するprivate networkの一つ。

### Tailscale

WireGuardベースのprivate networkを提供するツール。MediaVault Phase 1では、同じLANにいないiPhone、MBA、Mac miniを同じprivate network上で接続するために使う。

### Tailnet

同じTailscale networkに参加している端末群。MediaVaultではiPhone、MBA、Mac miniが同じTailnetに参加する想定。

### Tailscale IP

Tailscaleが端末に割り当てるprivate IP。Phase 1のBackend URLは`http://<tailscale-ip>:8000`を初期疎通確認の推奨形とする。

### MagicDNS

Tailscale上の端末名で到達できる名前解決機能。Phase 1ではBackend URLとして`http://<magicdns-name>:8000`も許容するが、初期疎通確認はTailscale IPを優先する。

### private endpoint

LANまたはTailscale private network内からだけ到達するbackend endpoint。Phase 1ではHTTP private endpointと固定APIトークンを組み合わせる。

### Backend endpoint policy

Backend URLをstructured parseし、HTTPではRFC1918、Tailscale IPv4、single-label MagicDNS、
`.local`だけ、又は有効なHTTPS originを許可するMobile authority。保存時と全通信境界で使い、
拒否時はAuthorization headerとnetwork callを作らない。
