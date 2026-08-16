# MediaVault 開発ガイドライン

## 基本原則

- originalを改変しない。
- 自動削除を実装しない。
- iPhone側original削除は、Mac mini側preview確認後のユーザー明示操作に限る。
- Backend側original削除とiPhone側original削除を混同しない。
- Phase 1 SHA256記録、Phase 2 hash検証、preview内容確認を混同しない。
- 保存先ルートは`MEDIA_ROOT`から解決する。
- Mobile、Backend、workerの責務を分離する。
- 安定した要件変更は`docs/`、個別仕様は`docs/ideas/`、実装計画は`.steering/`へ反映する。

## 開発環境

- Node.js: 24.x
- Expo SDK: 54
- Mobile: React Native + Expo managed workflow + JavaScript
- Backend: Python + FastAPI
- DB: SQLite
- Preview: ffmpeg
- Mac mini運用: Dockerを正規実行環境とする

## 依存追加

- Expo関連依存は`npx expo install`を使う。
- Expo SDKやNode versionは明示依頼なしに変更しない。
- Python依存はbackendの`pyproject.toml`と`uv.lock`で管理し、`uv`で解決する。
- `uv.lock`は再現性のためcommitし、`.venv/`はcommitしない。
- ローカル`node_modules`をDockerへコピーしない。

## コーディング規約

### JavaScript

- JavaScriptを使い、TypeScriptは明示依頼なしに導入しない。
- 公開hook、service、複雑なデータ構造はJSDocで契約を残す。
- 変数/関数は`camelCase`、componentは`PascalCase`、hookは`use`で始める。
- 真偽値は`is`, `has`, `can`, `should`で始める。

### Python

- module、function、variableは`snake_case`、classは`PascalCase`にする。
- route、service、repository、workerの責務を分離する。
- 外部コマンド実行は専用adapter/serviceへ閉じ込める。
- Backendのtest、worker、local server起動は原則`uv run ...`で実行する。

## Mobile実装ルール

- screenからExpo APIやHTTP clientを直接呼ばない。
- `expo-media-library`など端末APIはserviceに閉じ込める。
- 処理済みvideo保存はAsset Detailの明示操作だけで開始し、`processedResultSaveStore`をsource originalのmapping storeから分離する。保存成功は`review_status`、削除候補、source original mappingを変更しない。
- processed result downloadはasset/result IDから再構築したcanonical same-origin pathだけを使う。responseのabsolute URL、query、fragment、path不一致にはAuthorization headerを送らない。
- managed presetはserver catalogだけを表示し、local fallback option、Apple Log/Rec.709 label、LUT pathを合成しない。catalogにvalidな`compress-only`がなければ画面をerrorにする。
- rendition client request IDはsecure platform UUIDだけから32桁lowercase hexを作り、POST前にasset-scoped storeへ保存する。network timeoutと`rendition_precondition_changed`は同じIDでretryし、新しい明示selectionだけが新IDを作る。
- rendition pollはrequest ID、rendition ID、selection sequenceを照合し、新しいselection後の古いresponseをUI又は保存対象へ反映しない。ready時もAsset Detailのexact active resultを再確認する。
- `video/mp4`だけを検証済みresult ID由来のcache `.mp4`へdownloadし、response header、size、native streaming SHA-256が一致してから写真ライブラリへ保存する。token、URI、storage pathをAsyncStorageへ保存しない。
- 写真ライブラリpermissionは保存操作時だけ要求する。`createAssetAsync`直前に`unknown` write-ahead recordを永続化し、saved recordを書いてからtemporary fileをbest-effort cleanupする。保存結果が不明なら自動再保存しない。
- 固定APIトークンをログ出力しない。
- metadata欠落をエラーにせずnullableとして扱う。
- `taken_at` は秒精度の ISO 8601 datetime に正規化する。正規化不能なEXIF日時やoffset単体はnullとして送信し、文字列`null`を送らない。
- Phase 1では`104857600 bytes`超過をupload開始前に案内する。
- 初期リリースは1つのBackend URLを通常設定保存領域、1つの固定APIトークンを
  既存の`expo-secure-store` keyへ保存する。server name/ID、複数profile、QR importは追加しない。
- shared endpoint policyをSettings保存と全通信境界で使い、private HTTP又は有効なHTTPS originだけを許容する。
- rejected URLではtokenを文字列化せず、Authorization headerとnetwork adapterを構築しない。
- Tailscale IPまたはMagicDNS名を使う場合も、固定APIトークン認証を必須にする。
- `127.0.0.1`はiPhone自身を指すため、iPhone実機からMBA/Mac mini backendへ接続するURLとして使わない。
- 公開インターネット上のHTTP endpointをPhase 1の接続先にしない。
- App Review用の公開接続先は、自宅Mac miniとデータを分離したHTTPS backendだけを使う。
- iPhone側original削除は自動実行しない。
- iPhone側original削除の共通条件は`preview_ready`、`preview_confirmed`、
  local mapping available、未削除、非busyとする。
- 削除前に対象asset、filename、撮影日時などを表示し、ユーザーの明示確認を必須にする。
- iPhone側original削除は`expo-media-library` service経由で実行し、screenから端末APIを直接呼ばない。
- Phase 1 direct assetは`server_hash_recorded`だけをorigin authorityとしformal capabilityを要求しない。
  Phase 2 session videoだけ`video + file_verified`、sanitized compatible capability、
  ready formal preview、`formal_apple_log_preview`と`safe_delete_candidate` capability、
  Backendの`safe_to_delete_candidate`を追加要求する。
- Phase 2の削除操作を表示するときとnative削除確認直前にasset/capabilityを再取得する。asset取得失敗、
  asset/local mappingの変化、capability取得失敗・停止、candidate降格はPhase 2だけをfail closedにし、
  native削除を呼ばない。Phase 1 direct assetはasset再取得に成功して既存条件を満たす限り、
  capability取得失敗の影響を受けない。
- successor schema上のPhase 2 asset-specific APIは0.4.0 client version headerを必須にする。formal preview sanitizerは`apple-log-1`/`apple-log-2`とprofile別requested preset、`compress-only` unavailable tupleだけを受理し、cross-profile、unknown profile/error、Apple Log applied/LUT identityをauthorityとして扱わない。
- Apple Log表示は`formalPreviewPresentation.js`のpure helperをAsset DetailとPreview Reviewで共有し、versionなしのgeneric label又は変換済みlabelを画面側で合成しない。
- eligibilityはpure serviceへ置き、native削除成功をlocal outcome保存より先にterminal確定する。
- Backend側originalやderived fileをMobileの削除操作で削除しない。

## Backend実装ルール

- routeはvalidationとservice呼び出しに集中する。
- クライアント指定pathを保存先として使わない。
- path traversalを防止する。
- upload中ファイルは`tmp/`、確定originalは`originals/`に置く。
- Phase 2Aの新規動画は`POST /assets/upload`で受理しない。`/upload-sessions`のcreate/status/chunk/finalize/cancel APIだけを使い、finalizeはleaseを持つ`upload_finalize` jobへ委譲する。
- session createはMobileが永続化したidempotency keyを使う。chunkは固定range/hashの同一再送だけを成功にし、finalizationはsessionごとに一意なasset/jobを作る。
- retryable finalizationだけは同一`upload_finalize` jobを`failed -> queued`へ戻せる。terminal failureをgeneric workerが再queueしてはならない。
- 大容量動画のSHA256はnative streaming moduleで計算する。chunk digestを再hashしてcompleted-file digestとしてはならず、videoをJS memoryへ全量読み込みしてはならない。
- ffmpegはoriginalを読み取り入力とし、derived fileを別パスへ生成する。
- processed resultはready video derived fileのimmutable identityとして扱う。active pointerはsame-assetのready resultだけを指し、new result、old result supersede、pointer、asset preview state、preview job完了は同一transactionで確定する。
- result delivery endpointはasset/resultを同時に検索し、shared deliverability serviceでfile integrityとPhase 2A/2B gateを検証する。inactive resultを新active resultのbytesに置換して返さない。
- Apple Log判定はoriginal確定後のworkerだけが行う。Apple Log 1は`generated-apple-log-rec709`、Apple Log 2は`generated-apple-log2-rec709`をrequested presetとして記録するが、0.4.0では両方をabsent/disabledに固定し、`compress-only` unavailable previewだけを成功として生成する。LUT file、manifest、変換artifactを生成・登録・適用しない。
- 初期Phase 2B formal previewは自動preset解決だけを使う。非Log/判定不能は`compress-only`を要求し、identity/test/customのMobile選択は別のmanaged renditionとしてformal preview/review stateを変更しない。Apple Log applied/LUT tupleはDB互換性があってもworker、finalizer、API、delivery、confirmation、safe-deleteで拒否する。
- LUTは管理manifestを持つserver presetだけを使い、Mobileまたはasset単位の任意file uploadを受け付けない。custom LUTはrepo外の`USER_LUT_ROOT`で管理し、workerは要求・適用preset、version、SHA-256、色変換状態をrendition provenanceへ保存する。Apple Log fallbackと非Logはformal `transform_kind = none`、将来の承認済みApple Log変換だけをformal `transform_kind = lut`とする。
- schema v1 manifestはUTF-8/BOMなし/64 KiB以下、duplicate/unknown fieldなし、厳密な型としてparseし、top-level `manifest_sha256`だけを除いたRFC 8785 JCS bytesをhashする。`.cube`は16 MiB以下、3D grid 17/33/65、finite RGB、exact row count/hashだけを受理する。
- LUT sourceはrequest pathから選ばず、renditionへ保存した`source_root_kind`とrelative componentsを使う。各componentを`O_NOFOLLOW`相当でdescriptor openし、regular file/size/hashを検証しながらowner-only job-private snapshotへcopyする。FFmpegには`MEDIA_ROOT`内のbackend-generated pathだけを渡す。
- missing/disabled presetだけを`compress-only`へfallbackする。registered-invalid、snapshot source変更、FFmpeg LUT適用失敗をfallbackで隠さず、stable terminal errorにする。
- routine log/API errorへtoken、host path、raw manifest、LUT content、complete media metadata、FFmpeg stderrを出さない。外部errorは固定codeとretryable flagへ変換する。
- ffprobe/certifier subprocessは`Popen(..., shell=False, start_new_session=True)`と固定argvを使い、stdout/stderrを並行して各1 MiB以内へbounded captureする。timeout、output超過、reader failureではprocess groupをTERM/KILLし、certifierは一意な検証済みcontainer名を`docker rm -f`で回収する。cleanup完了前にartifactを公開せず、raw stderrやcontainer/pathを通常logへ出さない。
- runtime detectorはverified originalを`O_RDONLY`と利用可能な`O_NOFOLLOW`で一度だけ開く。regular file、DB size、open前後と検出前後のdescriptor/path identityを検証し、bounded parserとFFprobeへ同じinherited read-only fdを渡す。判定中にsource pathを再openしない。
- ISO BMFF parserはproject-owned standard-library実装とし、32/64-bit box size、top-level zero-size、parent boundary、header/depth/track/sample-description/metadata/identifier limitをmaximum-inclusiveで検証する。許可path外のbox、`mdat`、`hoov`、unknown payloadを読み込まず、whole-file byte search、filename、codec、legacy `is_log`をApple Log根拠にしない。
- `logs`は選択video trackのsupported VisualSampleEntry direct childだけから1..128 byteのNULなしASCIIとして読む。unknown text、raw atom、track ID、pathをcanonical evidence/API/logへ残さず、構造破損とresource limitはstable terminal error、bounded authority不足は`unknown`へ分ける。
- detector certificationのexternal recordingはroot `data/`の固定descriptorからだけ解決し、root 0700、descriptor 0600、owner、regular/no-symlink、relative confinement、known SHA-256をsnapshot作成前に検証する。no-follow descriptorからowner-only temporary snapshotへcopyし、copy中のidentity不変も確認する。Dockerへはsnapshotだけをread-only mountし、manifest digest確定後にexternal pathを再openしない。
- snapshot stale cleanupはfixed namespace、TMPDIR 4096 entry、namespace 64件、depth 8、tree 64 entry、age 300秒、0700/0400/0600 modeのclosed contractで行う。ambiguous、symlink、special file、owner/mode/identity変化は削除せずcertificationを停止する。
- 実動画、absolute/relative local filename、raw metadata、fixture-derived blob OIDをGit、artifact、Docker image/build context、routine outputへ含めない。Git混入監査はfixture hash確認後、reachable pathとobject databaseをrecord/stdout/timeout上限付きでfail closedに検査する。
- `file_verified`動画の`preview_ready`、stream、confirmationは`formal_preview_id`とそのprovenanceを検証する。Phase 1 direct image/videoはこのPhase 2B triggerの対象外とする。
- `safe_to_delete_candidate`はPhase 2C evaluatorから導出したstored projectionとし、request payload、
  filename、path、legacy `is_log`、managed rendition、Mobile local stateを判定根拠にしない。
- candidate evaluatorは既存SQLite connectionとasset IDだけを受け、最大4 SQLのindexed aggregateで
  completed session、verified chunk ledger、whole-file identity、current formal provenance、
  preview confirmationを評価する。commit、media file、ffmpeg、ffprobe、Photos APIを使用しない。
- formal preview confirmationはschema/client/runtimeを先に解決し、formal fileのsize/SHA-256を
  write transaction外で検証する。`BEGIN IMMEDIATE`後にsnapshotを再確認し、reviewとcandidateを
  同じtransactionで更新する。
- candidate authorityを変更する正規serviceは同じstatement又はtransactionでcandidateを
  `not_candidate`へ降格する。completed session/chunk、file-verified original identity、
  current formal derived identityの保護はSQLite triggerを境界とする。
- 外部SSD未接続、容量不足、I/O失敗を明示的に扱う。
- `/assets/upload`, `/upload-sessions`配下、`/assets`,
  `/assets/{asset_id}`配下、`/api/v1/capabilities`、`/api/v1/presets`、
  `/api/v1/assets/{asset_id}/renditions`配下は固定APIトークンを要求する。
- jobs repository/worker/leaseは内部modelとして維持し、public `/jobs`又は専用Upload Queueを追加しない。
- API要求は`Authorization: Bearer <token>`形式とする。
- Tailscaleは通信経路であり、backend認証の代替にはしない。

## Statusルール

- `transfer_status`: 転送状態のみ。
- `verification_status`: SHA256記録、Phase 2のhash検証状態のみ。
- `preview_status`: preview生成状態のみ。
- `review_status`: ユーザー確認状態のみ。
- `delete_candidate_status`: 安全削除候補状態のみ。
- `local_delete_status`: Mobile側local stateとして、iPhone側original手動削除状態のみ。Backend asset statusではない。
- processed result save state: Mobile側local stateとして、処理済みcopyの保存状態のみ。source original削除状態やBackend asset statusではない。
- 単一status列へ再統合しない。

## Jobルール

- jobはSQLiteへ永続化する。
- Phase 1は単一worker、SQLite WAL mode、`busy_timeout = 5000ms`を使う。
- workerはSQLite transactionでjobをatomic claimする。
- workerは処理可能なjob typeだけをclaimし、processor未実装のjobを通常処理でfailedへ落とさない。
- `claimed_at`と`lease_expires_at`で異常終了後のjobを回収する。
- Docker worker serviceは`restart: unless-stopped`で再起動する。
- job種別は`preview`, `lut_preview`から始め、Phase 2Aで`upload_finalize`と`rendition`を追加し、将来AI jobを追加する。
- workerは既知job typeを専用processorへ明示dispatchする。`rendition`は`renditions.job_id`をrelation authorityとし、payload IDは一致確認だけに使い、generic preview processorへfallbackしない。
- managed rendition finalizerはcurrent selection generationの成功時だけ、derived fileとready processed result作成、provenance insert、rendition `ready`、`active_processed_result_id`更新、job `done`の順で同一transactionに確定し、各write境界の失敗では全変更をrollbackする。managed resultの`preview_generation`は全Phaseでnullとする。pointer更新ではsteady-state authority classifierとは別のtransition validatorを使い、OLDが完全なformal又はmanaged relation、NEWがcurrent selectionの一意なready managed relationの場合だけ許可する。失敗時は直前の成功済みactive resultを維持する。stale completionはsuperseded auditとして確定する。Phase 2Bではpointer切替時にcurrent formal resultをsupersedeせず、直前のcurrent managed resultだけをsupersedeする。いずれもformal preview、preview/review/delete-candidate stateを変更しない。
- Phase 2Aではoriginal確定後にpreview jobを登録し、Phase 2B detector-v2ではApple Log判定とprofile別unconverted formal previewをそのjob境界の後に置く。新規動画はprofile-awareな`preview` jobを使い、historical `lut_preview`はaudit-onlyとする。
- Phase 2B migrationは旧`api`停止、旧workerによるdrain、旧`worker`停止の順でwriterを遮断し、host wrapperが両serviceの非稼働を確認した後、DB volumeを持つoffline one-shot migratorだけで実行する。preflightのread結果を信用してそのままwriteせず、`BEGIN IMMEDIATE`取得後にschema/marker、旧`preview`/`lut_preview`/`rendition`のqueued/running件数、nonterminal rendition、`preview_generating` assetを再検証する。残件又は競合変更があればmigrationは修復せずschema/data/markerを無変更rollbackし、完了までAPI/workerを再起動しない。active resultはpersist済みprovenanceのsteady-state classifierで分類し、current managedを保持、legacy Phase 2A previewだけをsupersede、ambiguous relationを全rollbackする。session由来video preview jobはassetと同じ`preview_generation`をpayload/columnに持ち、workerはclaim/commit時に両者が一致する場合だけasset、formal preview、review stateを更新する。世代不一致jobはattemptを`superseded`、jobを`failed` + `preview_generation_superseded`へlease clear付きで収束させ、assetを書き換えない。
- Phase 2C migrationは通常startup migrationへ追加しない。host wrapperがAPI停止、Phase 2B work drain、
  worker停止、両service非稼働を確認した後、network無効・read-only root・DB volumeだけRWの
  `phase2c-migrator`を一度だけ実行する。read/locked preflight、009 schema、metadata、
  shared evaluator backfill、integrity checkを一transactionでcommitし、dry-runは同じpathを
  rollbackする。失敗時はAPI/workerを停止したままにする。
- Phase 2C reconciliationはnetwork無効の`phase2c-reconciler`を使い、runtime snapshotをwrite lock前に
  取得する。lock内でschema identityを再確認し、confirmed Phase 2 assetと既存safe candidateだけを
  shared evaluatorでpromote/demote/no-opへ分類する。dry-run/applyを必ず明示する。
- detector-v2 `010_apple_log_container_signaling`は008/009を変更せず、通常startup migrationへ追加しない。default `--preflight-only`はPRAGMAを変更しないread-only処理、`--dry-run`はisolated DB限定のfull DDL/marker後rollback、`--apply`はoffline/release 0.4.0確認付きに分離する。
- detector-v2 migrationはtransaction外で`foreign_keys=ON`、`legacy_alter_table=OFF`をread backし、切替後にだけ`BEGIN IMMEDIATE`へ進む。read/locked preflight、全row compatibility、schema/object identity、foreign key、両reserved preset namespace identityを確認し、commit直前の再分類差異を完全rollbackする。success、dry-run、faultの全経路でPRAGMA既定値を復元し、復元失敗時はserviceを停止したままにする。
- `implement-feature`と`validate-implementation`ではmigration mutationを`/private/tmp`等の明示したisolated database copyだけへ実行する。operator databaseではread-only preflightまでとし、host apply wrapper `scripts.run_detector_v2_migration`を実行しない。operator applyは別の明示release operationとする。
- job失敗時は`error_message`へ運用に必要な情報を保存する。
- 固定APIトークン、filename、host/media path、complete hash、raw SQL row、不要な個人情報を
  routine error/log又はmigration/reconciliation summaryへ含めない。operator出力はaggregate件数と
  stable reason codeだけにする。
- identity LUTで生成済みのLOG previewはRec.709変換済みとして扱わない。preview配信・確認には、要求・適用presetと色変換状態を持つformal provenanceを必須にする。

## テスト戦略

### Mobile

- unit test: status表示変換、`104857600 bytes`制限、metadata nullable処理。
- unit test: upload timeout後の結果不明状態と再送抑止、EXIF `taken_at` 正規化、local mapping失敗後のupload成功状態。
- unit test: `result_unknown`の再起動後の復元、一覧確認済みの明示操作による解除、local asset idなしのglobal pending marker。
- component/実機 test: absent/disabled reserved presetのApple Log 1/2 assetで、version別未変換表示付き`preview_ready`、再生、confirmation、削除導線を確認する。registered-invalid/valid/collisionのblocked stateには同導線を出さない。mapping未取得時に削除導線を出さないことも確認する。
- component test: Settings、Asset Picker、Asset Detail、Preview Review。
- unit test: endpoint accept/reject matrix、rejected URLのheader/network 0 call、
  Phase 1/2 original deletion eligibility。
- unit/component test: iPhone側original削除導線がpreview確認後だけ表示されること。
- unit/component test: Phase 2削除導線がformal/safe capability、ready formal preview、
  `safe_to_delete_candidate`をすべて要求し、削除直前refreshの失敗又は状態変化でnative削除を
  0 callにすること。Phase 1 direct assetはPhase 2C条件を参照しないこと。
- unit/component test: canonical processed-result URL以外へtokenを送らないこと、header/size/digest mismatchで写真ライブラリへ保存しないこと、unknown write-ahead/save cleanup順序、source-original mappingとの非参照を確認する。
- unit/component test: malformed/unknown catalog、secure request ID、write-before-POST、same-ID retry、restart polling、A/B response guard、全rendition phase、fallback/terminal error、ineligible/legacy LOG非表示を確認する。
- unit/component test: Apple Log 1/2のprofile別fallback sanitizer、cross-profile/unknown/applied claim拒否、shared exact label、0.4.0 client guard、Asset Detail refresh失敗時のpreview/confirmation/result/local deletion 0 callを確認する。
- 実機確認: Development Buildでprocessed resultのdownload、permission denial、network interruption、無進捗timeout、cancel、supersession、unknown outcome、restart cleanupを確認する。
- 実機確認: Development Buildで権限許可/拒否、iCloud-only素材、metadata欠落、ライブラリアクセス、TailscaleまたはLAN経由の通信、preview再生、削除キャンセルを確認する。

### Backend

- unit test: path生成、SHA256計算、status遷移、token validation。
- API test: upload、一覧、詳細、preview、確認。
- API/worker test: `taken_at` の厳密な受理形式、malformed preview payloadのterminal failure、identity LUT preview失効migration。
- API/worker test: terminal failure時のjob/asset status updateが同一transactionでrollbackされること。
- API/worker test: Apple Log 1/2のsame-fd container判定、profile別`compress-only` fallback、provenance付きready、Apple Log applied/LUT claim拒否、successor triggerを確認する。
- API/worker test: session create/chunk/finalizeのidempotency、concurrent finalize、lease reclaim、promote後DB失敗、commit後timeout、expiry/cancel、`upload_finalize`の復旧を確認する。
- API/worker test: Apple Logと非Logのformal provenance、`preview_ready`を拒否するSQLite trigger、stream/confirmationのprovenance gateを確認する。
- migration/API test: processed resultのFK、active pointer trigger、transaction rollback/backfill、inactive/cross-asset result、`200`/`206`/`416` Range delivery、descriptor open前後のpointer切替を確認する。
- managed pointer test: `active managed N -> ready N+1 -> switch -> N superseded`、formal activeからmanagedへの切替、複数/non-current/incomplete targetのdirect SQL拒否、formal non-null/managed nullの`preview_generation`を確認する。
- unit/API/worker test: manifest JCSとstrict schema、`.cube`検証、catalog auth/sanitization、request replay/precondition、no-follow source snapshot、relation recovery、A/B両完了順、finalizer write failure、managed provenance deliveryを確認する。
- migration test: Phase 2B profile-aware jobのdedup insert成功時だけasset generation/stateを更新し、queued/done/failed jobが既存の再実行ではstateを戻さないこと、generation `0`の旧Phase 2A jobがlate commitしてもformal preview/review stateを変更しないことを確認する。
- Phase 2C unit/migration/API test: 10 reasonの固定順、131072 chunkでのindexed aggregate、
  4 SQL上限、trigger/evaluator parity、009全fault境界rollback、confirmation race、
  reconciliation dry-run/apply、0.3.0 rollout matrixを確認する。
- detector-v2 parser test: 全closed result、32/64/zero-size boundary、track/handler/stsd authority、VisualSampleEntry 86-byte boundary、decoy、全resource limitのexact/limit+1、seed固定mutation、metadata read/retention上限を確認する。
- detector-v2 certification test: same-fd identity、descriptor permission/path confinement、snapshot cleanup/stale sweep、strict rule/manifest/summary、synthetic Apple Log 1とlocal-only real Apple Log 2/ordinary fixtureを確認する。
- detector-v2 migration test: read-only preflight、isolated dry-run/apply/rollback、PRAGMA lifecycle、reserved preset state/race matrix、全fault rollback、0.4.0 compatibility、operator DB非変更を確認する。
- 実機 test: Apple Log、通常動画、判定不能動画でのpreview表示と、Phase 2Aのchunk完了後だけpreview jobが登録されることを確認する。
- integration test: tmp保存、original確定保存、ffmpeg成功/失敗、SSD未接続、容量不足。

## 品質ゲート

Mobileの正規品質command:

```bash
npm run lint
npm test
npm run test:coverage
npx expo install --check
npx expo export --platform ios
npx expo start
```

- `npm run lint`は`eslint App.jsx index.js jest.setup.js eslint.config.js src modules --max-warnings=0`を実行し、Mobile JavaScript/JSXとroot設定を非破壊で検査する。errorとwarningはいずれも0件を必須とする。
- ESLintはroot `eslint.config.js`のExpo flat configを正本とする。rule suppressionは最小範囲に限定し、false-positive又はtest mock上必要な理由を直前へ記載する。correctness ruleをrepository-wideに無効化しない。
- `npm run test:coverage`は`jest --runInBand --coverage`を実行する。`npm test`と同じbehavioral suitesを使い、coverage有無でtest結果を変えない。
- canonical coverage scopeは`src/**/*.{js,jsx}`と`modules/*/src/**/*.{js,jsx}`で、除外は`**/*.test.{js,jsx}`と`**/__tests__/**`だけとする。未importのproduction moduleも0 coverageとして母集団へ含める。
- reportはignoredな`coverage/`へ`text`、`lcov`、`json-summary`形式で出力する。global floorはstatements 80%、lines 80%、branches 69.46%、functions 80.08%とし、下げて通さない。
- 2026-07-22のcanonical initial値は36 production files、21 suites / 96 tests、statements 68.91%（1022 / 1483）、branches 62.48%（851 / 1362）、functions 69.67%（193 / 277）、lines 69.22%（1012 / 1462）で、4 floor不足によりexit 1だった。
- 2026-07-22のfinal値は同じ36 production files、32 suites / 157 tests、statements 86.07%（1280 / 1487）、branches 77.30%（1056 / 1366）、functions 89.56%（249 / 278）、lines 86.08%（1262 / 1466）でexit 0だった。
- coverage scopeを変更する場合はfeature specをreviewし、旧新glob、除外、matched production-file数、suite/test数、4指標とhit/total、理由、承認を記録する。既存numeratorの流用、silent exclusion、floor引下げは禁止する。
- Jest coverageはphysical-device validationを代替しない。端末固有の権限、Tailscale/LAN、media再生・保存・削除は上記の実機確認を別途行う。

Backendのlint/test commandの標準形:

```bash
uv run --directory backend pytest
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
```

release contractの追加固定command:

```bash
node scripts/verify-ios-native-config.mjs
env API_TOKEN=test-token docker compose --profile image-codec-validation \
  run --build --rm --no-deps -T image-codec-validator
```

Docker codec validatorはrepository-owned fixtureだけをread-only mountし、runtime networkを
無効化する。`--build`は未cache layer取得にnetworkを必要とし得る。

Phase 2C operator commandは`backend/`をcurrent working directoryとして実行する。production applyは
先にdry-runを成功させる。

```bash
cd backend
uv run python -m scripts.run_phase2c_safe_delete_candidate_migration --dry-run
uv run python -m scripts.run_phase2c_safe_delete_candidate_migration --apply
uv run python -m scripts.run_safe_delete_candidate_reconciliation --dry-run
uv run python -m scripts.run_safe_delete_candidate_reconciliation --apply
```

host wrapperは固定argvでCompose one-shot serviceを呼び、migration failure時はAPI/workerを
再起動しない。reconciliationはschema migrationの代替ではなく、valid 009 schema上のstored
projectionだけを修復する。

detector-v2の実装・検証中は`backend/tests/test_detector_v2_migration.py`と
`backend/tests/test_detector_v2_operator_paths.py`を正本に、temporary isolated databaseで
preflight/dry-run/apply/rollbackを検証する。operator DBに接続するhost apply wrapperはrelease operationまで
実行せず、検証結果へ「operator databaseへ010を適用していない」と明記する。

### Backend ローカル疎通確認

DockerなしでMBA上のbackendを確認する場合は、API serverとworkerを別Terminalで起動する。MBA自身から確認するだけなら`127.0.0.1`でよい。iPhoneからTailscale経由でMBA backendへ接続する場合は、API serverを`0.0.0.0`で待ち受け、Backend URLにはMBAのTailscale IPまたはMagicDNS名を使う。

API server:

```bash
cd /Users/oyabu/dev/rep/latest_template/backend

MEDIA_ROOT=/private/tmp/mediavault-local-media \
API_TOKEN=test-token \
DATABASE_PATH=/private/tmp/mediavault-local.sqlite3 \
LUT_PATH=/Users/oyabu/dev/rep/latest_template/backend/assets/lut/rec709.cube \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

iPhoneからTailscale経由で確認するAPI server:

```bash
cd /Users/oyabu/dev/rep/latest_template/backend

MEDIA_ROOT=/private/tmp/mediavault-local-media \
API_TOKEN=test-token \
DATABASE_PATH=/private/tmp/mediavault-local.sqlite3 \
LUT_PATH=/Users/oyabu/dev/rep/latest_template/backend/assets/lut/rec709.cube \
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

worker:

```bash
cd /Users/oyabu/dev/rep/latest_template/backend

PATH="/opt/homebrew/bin:$PATH" \
MEDIA_ROOT=/private/tmp/mediavault-local-media \
API_TOKEN=test-token \
DATABASE_PATH=/private/tmp/mediavault-local.sqlite3 \
LUT_PATH=/Users/oyabu/dev/rep/latest_template/backend/assets/lut/rec709.cube \
uv run python -m app.workers.worker
```

Homebrewで入れたffmpegをworkerから見えるようにするため、worker起動時は`PATH="/opt/homebrew/bin:$PATH"`を明示する。

疎通確認:

```bash
curl -H "Authorization: Bearer test-token" http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/assets/upload \
  -H "Authorization: Bearer test-token" \
  -F "file=@/path/to/sample.mp4" \
  -F "type=video" \
  -F "filename=sample.mp4" \
  -F "is_log=false"

curl -H "Authorization: Bearer test-token" http://127.0.0.1:8000/assets/{asset_id}

curl -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8000/assets/{asset_id}/preview \
  -o /private/tmp/preview-check.mp4

curl -X POST \
  -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8000/assets/{asset_id}/preview-confirmation
```

iPhoneから確認する場合は、`http://127.0.0.1:8000`ではなく`http://<MBAのTailscale IP>:8000`または`http://<MBAのMagicDNS名>:8000`を使う。

確認観点:

- `/health`が`{"status":"ok"}`を返す。
- upload responseで`preview_status = preview_generating`、jobが`queued`になる。
- worker処理後にasset detailで`preview_status = preview_ready`になる。
- preview取得で`/private/tmp/preview-check.mp4`が作成される。
- confirmation後に`review_status = preview_confirmed`になる。

実行できないcommandがある場合は、理由を`.steering/[YYYYMMDD_N]-[feature-name]/tasklist.md`へ残す。

## Git運用

- commitはConventional Commitsを基本とする。
- `.env`、token、実メディア、SQLite実データをcommitしない。
- `docs/ideas/`には仕様だけを置く。
- 一時メモは`.agents/workspaces/`へ置く。

## Definition of Done

- 受け入れ条件を満たす。
- Backend側original非改変、自動削除禁止、手動削除はpreview確認後のみ、Token必須を確認する。
- lint/test/起動確認または未実行理由を記録する。
- `docs/`と`.steering/`を必要に応じて更新する。
- 実装後に`validate-implementation`を実行する。
