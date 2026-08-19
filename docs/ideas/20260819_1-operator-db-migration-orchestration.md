# Feature Spec

## Metadata

- Date: 2026-08-19
- Feature name: operator-db-migration-orchestration
- Status: confirmed
- Related files:
  - `.agents/workspaces/20260818-0.4.0-release-readiness.md`
  - `.steering/20260802_1-apple-log-container-signaling-detection/`
  - `backend/app/db/migrations.py`
  - `backend/app/services/phase2b_migration.py`
  - `backend/app/services/phase2c_migration.py`
  - `backend/app/services/detector_v2_migration.py`
  - `backend/scripts/`
  - `docker-compose.yml`

## Background

0.4.0 release前のoperator DBは`001_initial`だけが適用された状態であり、通常のapplication/worker startupへ任せると、002–007 migrationだけでなくstartup recoveryやbackfill、worker処理も同時に動き得る。さらに既存の008/009 host wrapperは成功後にAPI/workerを自動再起動するため、008 apply後のworker-only drainと、009 dry-runからapply完了までのwrite window閉鎖を一つのoperationとして保証できない。

010の現行preflightは共通のwritable connectionを使い、main DBを変更しなくても`-wal`/`-shm`を生成し得る。operator DBへ変更を加える前に、002–009を停止境界内で扱うrelease orchestration、真にread-onlyな010 preflight、DBと`MEDIA_ROOT`を含むrestore drill、release/rollback artifact固定を実装し、disposable DB/volumeだけで安全性を証明する必要がある。

## Target Users / Use Cases

- MediaVault 0.4.0 releaseを実施するoperatorが、operator DBへ触れる前にmigration pathとrollbackを再現可能に検証する。
- reviewerが、全writer停止、exact predecessor、artifact/env固定、failure時の停止維持、DB/sidecar不変を自動テストとsanitized operator recordから確認する。
- incident対応者が、部分commit又は008/009/010失敗時に、自動再開せずpre-release backupからDBとderived mediaの整合を復元する。

## Scope

- application/worker startupを使わない002–007 offline migration CLI。
- API停止維持、008 apply、worker-only drain、worker再停止、009 dry-run/applyを一つのwrite window閉鎖下で行う002–009 release orchestration。
- DB volumeをmountする全containerの検出と停止確認。
- SQLite URI `mode=ro`とCompose/Docker `:ro` mountを使う010専用read-only preflight。
- main DB、`-wal`、`-shm`の存在・内容・metadataがpreflight前後で不変であることのテスト。
- disposable Docker volumeとdisposable `MEDIA_ROOT`を使うbackup/restore drill。
- 002–007部分commit、008/009/010失敗時のrollback/restore契約。
- release/rollback用API/worker/migrator image ID/digestと環境変数の固定・検証。
- migration/operator/Compose関連のunit/integration testとdisposable volume検証。
- 安定した設計判断を`docs/`へ反映する。

## Out of Scope

- operator DB本体への002–010 apply。
- operator volumeのwritable mount、operator restore、operator volume削除。
- `scripts.run_detector_v2_migration`又は既存008/009 host wrapperの実環境実行。
- application startup、worker startup recovery/backfillをmigration手段として使うこと。
- operator DBへの010 apply、operator apply用host wrapper実行、post-start capability確認を含む実運用release operation。010のfault/rollback検証は`/private/tmp`又は明示したdisposable volumeに限って対象に含む。
- original mediaの削除・上書き、実動画又はroot `data/`の参照・開示・commit。
- repository-wide Ruff整備。

## User Flow

1. Operatorはrelease commit、API/worker/migrator image digest、release/rollback環境変数、DB volume、DB path、`MEDIA_ROOT`をmanifestへ固定する。operation中のbuild/pullは禁止される。
2. Orchestratorは対象DB volumeをmountする全containerを列挙し、API/workerを含む全containerが停止中であることを確認する。未知又は稼働中containerがあれば開始しない。
3. Operator volume `latest_template_backend-db`をmountできるのは、SQLite Backup APIでfresh owner-only backup copyを作る明示手順だけであり、mount modeを`:ro`に固定する。既存temporary DB copyを正本として信用しない。本featureのpreflight、migration、restore、drillを含むその他の全commandはoperator volume名を拒否し、fresh backupから作成したdisposable copy/volumeだけを対象にする。
4. Offline CLIはexact `001_initial`から002–007だけを固定順で適用する。application/worker、startup recovery/backfillは起動しない。
5. 各version適用後にmarker/schema/integrityを確認する。途中失敗又は部分commitを検出した場合は停止状態を維持し、自動再開せずbackup restoreを要求する。
6. Orchestratorはexact 007から008をapplyし、API停止を維持したままworkerだけを起動してPhase 2B workをdrainする。drain完了後にworkerを停止し、DB volume mount containerの停止を再確認する。
7. API/worker停止中のまま009 dry-run、009 apply、schema identity、integrity/FK確認を行う。途中失敗では全serviceを停止したまま終了する。
8. Exact 009 DBへ010 read-only preflightを`mode=ro`とvolume `:ro`で実行し、main DB、WAL、SHMの存在、digest、size、mtime等が前後で完全不変であることを確認する。
9. Disposable volumeへpre-release backupをrestoreし、stale WAL/SHMがなく、DB identity/integrity/FKと`MEDIA_ROOT` inventoryが復旧条件を満たすことを確認する。
10. Rollbackではmigration後に生成されたderived mediaだけをbounded inventoryからorphan cleanup対象とし、originalは削除・上書きしない。旧image digestと旧環境変数で再起動可能なrecordを検証する。

## Functional Requirements

### FR-1: 002–007 offline migration CLI

- CLIはDB pathと明示modeを受け、application、worker、FastAPI lifespan、startup recovery/backfillをimport又は起動せずに動作する。
- 許可するmarker列とSQL SHA-256は次のexact chainに固定する。未知、欠落、先行、後続、duplicate marker又はdigest mismatchを拒否する。

| 順序 | Version | SQL SHA-256 |
|---:|---|---|
| 1 | `001_initial` | `ad1070489641a6d964a44415d24d0e62702aab80da01159b9e591f75534a2f35` |
| 2 | `002_invalidate_identity_log_previews` | `3868eca4e6b21390fd60fe00982b24ec8d093956e4b33c59f661f2d13353fedd` |
| 3 | `003_phase2a_resumable_uploads` | `aadf4575a23b93805df17423d6b764ebf8dc3e57547209e2729e9f169e4e260e` |
| 4 | `004_processed_video_delivery` | `053678c304a2bcae581e382ebea4c907f8a52409bea62081b11c8c75958ead75` |
| 5 | `005_enforce_processed_result_derived_file_immutability` | `e11ff60be8853b3e25a80a2991f28a78a0636cdd962c04b0719ff07d72da3ef3` |
| 6 | `006_enforce_processed_result_lifecycle_immutability` | `a93270b5f1c90b519e12d6275f41791174500332b50f0d1d61087875e7aa9f89` |
| 7 | `007_managed_preview_presets` | `06198b6aef5d2936ce9e16ef520dfe868d4b4f6aac724c6313e87de2be61efd9` |

- 各migrationのpredecessor、SQL identity、commit結果、marker/schema/integrityを検証し、planでは各transaction内とcommit直後のfault injection位置を固定する。
- migration単位のcommitにより部分適用となった場合、stable failure stateと最終committed versionを返し、自動継続又は途中再開を禁止する。
- 部分commit後の次操作はpre-release backup restoreだけとし、同じDBへの再実行をfail closedにする。
- routine outputはversion、status、stable reason、aggregateだけに限定し、path、row、token、media metadata、complete secret/hashを出さない。

### FR-2: 002–009 release orchestration

- Orchestratorはlogical service名だけでなく、対象DB volumeをmountする全Docker containerを検出する。
- operation開始前、worker-only drain後、009完了/失敗後に全対象containerの停止を検証する。
- phaseごとの許可稼働集合は、各one-shot開始前/終了後=`{}`、002–007/008/009の各実行中=`{manifestで固定した当該one-shot migrator}`、008後のdrain中=`{pinned worker}`とする。APIと未知containerは常時不許可とする。列挙又は停止状態を確認できない場合は`operator_migration_unsafe_stop_unconfirmed`でfail closedにし、停止済みと報告せず、再開不可・手動介入必須とする。
- APIはoperation開始から終了まで起動しない。
- 008 apply後だけ、pinned worker imageとpinned envでworker-only drainを許可する。drain完了条件とtimeout/nonterminal条件をclosedにし、完了後はworkerを停止する。
- Drain完了は既存`phase2b_drain_counts`の対象job、queued/running `preview`/`lut_preview`/`rendition`、nonterminal rendition、`preview_generating` asset、およびnonterminal formal attemptがすべて0であることとする。timeout又はcheck failureではworkerを停止してrestore要否を返す。
- worker停止確認後にのみ009 dry-runとapplyを続ける。dry-runからapply、identity/integrity確認までAPI/workerを再起動しない。
- 既存008/009 wrapperの自動再起動pathを呼ばない。
- 既存008/009/010 host wrapperと旧Compose migratorはfail closedで無効化し、operator DB volumeをmountできない。container DB pathを使う旧migration CLIもdisposable markerがなければ処理を開始しない。
- 失敗、interrupt、timeout、unexpected output、container停止失敗ではbest-effort停止を試行した後に停止状態を再検証する。停止を確認できた場合だけ`stopped_after_failure`、確認できない場合は`unsafe_stop_unconfirmed`として再開不可・手動介入必須を返す。
- operation stateは再実行時に自動継続しない。mutation開始後の失敗では全consumer停止確認後にpinned read-only identity one-shotを起動し、phase履歴から推測せず実DBのmarker prefix、integrity、FKからlast committed versionを取得してrestore要否とともにoperatorへ返す。identityを安全に取得できない場合は不明のままfail closedにする。
- Manifestごとにowner-only one-time operation claimを作り、成功・失敗を問わず同じmanifest/nonceからの自動再開を拒否する。新しいbackup、disposable volume、manifestで最初からやり直す。
- Host側でactual Docker volumeのdisposable label/nonceを検証した後、inspect済みpinned one-shotだけがvolume内へowner-only markerを作る。002–009/worker/010 preflightの全entrypointはmarkerのvolume名/nonce一致前にmigration又はworkerを開始しない。

### FR-3: 真にread-onlyな010 preflight

- 専用connectionはSQLite URI `file:...?...mode=ro`を使い、共通のwritable `connect()`を使わない。
- parent directoryを作成せず、`journal_mode`、WAL、schema、marker、application dataを変更するPRAGMA/SQLを実行しない。
- Compose/Dockerのpreflight serviceはDB volumeを`:ro`でmountし、network無効、read-only root filesystem、capability drop、no-new-privilegesを維持する。
- operator apply用host wrapperとpreflight entrypointを分離し、preflightからapply flagへ昇格できないようにする。
- DB pathが存在しない、regular fileでない、exact 009でない、schema/data/artifact/env identityが不正な場合はfail closedにする。
- main DB、`-wal`、`-shm`それぞれについて、preflight前後の存在有無、type、size、digest、mtime/ctimeを比較し、作成・削除・内容/metadata変更がないことをtestする。

### FR-4: Rollback / restore drill

- Drillは`/private/tmp/mediavault-operator-*`又はcontainer内`/restore`に限定したdisposable rootを使い、owner-only temporary backupと同じrootのstrict childである`MEDIA_ROOT`だけを扱う。
- Restore正本はSQLite Backup APIで作成し、identity/integrity/FKを検証したfresh backupに限定する。raw main DB copy又は既存temporary copyを正本にしない。
- pre-release backupをempty disposable volumeへrestoreし、stale `-wal`/`-shm`を残さず、marker、schema identity、`integrity_check`、`foreign_key_check`、bounded aggregateを検証する。
- 002–007の各migration途中失敗、008/009/010のtransaction前/中/commit後 failureを分類し、部分commit時はrestore-requiredを返す。
- Restore前にfailure DBのforensic identityを記録し、restore後にbackup identityと照合する。complete DB hashはowner-only artifactに限定し、routine outputへ出さない。
- `MEDIA_ROOT`はoriginalとderivedを分離してinventoryする。migration開始後に生成されたderivedだけをorphan cleanup候補にでき、originalは削除・上書き・置換しない。
- Disposable `MEDIA_ROOT`にはpre-release original、pre-existing derived、operation中に生成したderived/orphanを用意する。DB restore後にbounded DB参照inventoryとfilesystem inventoryを突合し、operation中のorphanだけをcleanupした後、originalとpre-existing derivedのbytes/type/size/mtimeが不変で、DB参照先の欠損と残存orphanが0件であることを検証する。
- rollback用の旧image digestと旧環境変数でAPI/workerを再構成できることをdry validationする。drillでoperator serviceは起動しない。

### FR-5: Release artifact固定

- Release manifestはcommit、Compose project、DB volume/path、`MEDIA_ROOT`、API/worker/各migratorのimmutable image ID又はrepo digest、release env、rollback image/envを記録する。
- Manifestはowner-onlyで保存し、envはallowlistしたkeyとowner-only actual value artifact identityを記録する。secret/tokenの平文値、host path、complete DB/media hashをsanitized routine outputへ出さない。実containerはpinned imageの`Config.Env`とphase別envをmergeしたexact map以外、追加bind/volume、privileged/device/host namespace、restart policy、想定外tmpfsを拒否する。
- operation開始時と各one-shot起動直前にcontainer image identityがmanifestと一致することを検証する。
- operation開始時にmanifest commitとcurrent HEADだけでなく、host orchestration contract対象tracked filesがmanifest commitからdirtyでないことを検証する。
- operation中に`build`、`pull`、mutable tag解決を実行しない。Composeはpinned image参照と`--no-build`相当を使用する。
- 環境変数はallowlistしたkey/value digestで固定し、token値、host path等のsecret/sensitive valueをroutine outputへ出さない。
- missing、extra、変更済みenv又はimage mismatchではmigration前に停止する。

## Non-Functional / Technical Notes

- Python standard libraryと既存backend dependencyを優先し、新しいruntime dependencyは追加しない。
- mutationを伴うtestは`/private/tmp`又はtestが作成した明示的disposable Docker volumeだけを使う。
- Docker resource名はtest固有prefixを使い、operator volume `latest_template_backend-db`をmutation対象にできないguardを設ける。
- `latest_template_backend-db`はfresh SQLite Backup API copy作成用の明示`:ro` mount以外、010 preflightを含む全entrypointで拒否する。operator由来検証はfresh backupから作成したdisposable volumeだけを使う。
- destructive cleanupはtestが作成したresourceだけに限定し、label/nonce/expected nameを照合する。
- test failure時にもAPI/workerを起動せず、disposable resource以外を削除しない。
- root `data/`、実動画、repository rootの未追跡JPEGを読み取らず、stage/commitしない。
- operation outputはbounded、path-free、secret-freeとし、exact argvとstable error codeをtest可能にする。
- stableなrelease/rollback境界は`docs/architecture.md`、`docs/repository-structure.md`、`docs/development-guidelines.md`へ反映する。

## Acceptance Criteria

- 002–007 CLIがapplication/worker/startup recovery/backfillを起動せず、exact 001から007までを成功適用できる。
- 002–007 CLIが上記7 marker列とSQL SHA-256を照合し、各transaction内/commit直後faultでfinal committed markerとrestore-requiredを正しく返す。
- 002–007の各fault境界で、最終committed markerを検出し、service停止とrestore-requiredを返し、自動再開しない。
- OrchestratorがDB volumeをmountする全containerを検出し、未知/稼働中containerがあればmutation前に拒否する。
- 002–009成功pathで、APIは一度も起動せず、workerは008後のdrain中だけ起動し、009 dry-run/apply中は停止している。
- 008、worker drain、009 dry-run/applyの各失敗/interruptで、API/workerとDB volume使用containerが停止したままになる。
- container列挙又は停止確認不能時は`unsafe_stop_unconfirmed`となり、停止済み成功を返さず、migration/restartを続行しない。
- 010 preflightがSQLite URI `mode=ro`、専用connection、DB volume `:ro`で動作し、apply entrypointを呼ばない。
- 010 preflight前後でmain DB、既存又は不存在の`-wal`/`-shm`の存在、内容、size、mtime/ctimeが完全に不変である。
- Disposable DB/volumeで002–009成功、002–007部分commit、008/009/010 failure、rollback/restore drillが再現できる。
- Restore後にstale WAL/SHMがなく、DB identity/integrity/FKがbackupと一致する。
- Disposable `MEDIA_ROOT` drillでderived orphan cleanup候補だけが分類され、original bytesとmetadataが不変である。
- Disposable `MEDIA_ROOT` restore後、operation中のderived orphanだけがcleanupされ、pre-release original/pre-existing derivedのbytes/type/size/mtimeが不変で、DB参照先の欠損と残存orphanが0件である。
- API/worker/migratorのimage ID/digestとrelease/rollback envが固定・検証され、build/pull又はmismatchが拒否される。
- migration/operator/Compose関連testが成功し、`implementation_validator`の重大指摘が解消される。
- `data/`、実動画、未追跡JPEG、operator DB/volumeが変更・stage・commitされていない。
- Operator volume `latest_template_backend-db`はfresh backup copy作成時の`:ro` mount以外に使用されず、既存temporary DB copyをrestore/preflight正本として使用していない。

## Open Questions

- 実運用operator applyの承認者、実施日時、停止時間、operator backup保管先は本feature完了後のrelease operationで別途確定する。
- 010 applyとpost-start smoke/capability確認は、本featureの検証結果を承認後に別セッションで行う。

## Durable Docs Impact

- 更新候補:
  - `docs/product-requirements.md`
  - `docs/functional-design.md`
  - `docs/architecture.md`
  - `docs/repository-structure.md`
  - `docs/development-guidelines.md`
  - 必要に応じて`docs/glossary.md`
- 更新要否: 必要
- 理由: startup migrationとoperator orchestrationの境界、read-only preflight、全volume writer停止、artifact/env固定、DBと`MEDIA_ROOT`を含むrestore drillは0.4.0以降も維持する安定した運用・アーキテクチャ契約であるため。
