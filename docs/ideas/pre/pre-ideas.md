# Pre Ideas

このファイルは、正式なfeature specにする前の「次やりたいこと」置き場です。

`docs/ideas/YYYYMMDD-[feature-name].md` に昇格したら、該当項目にチェックを入れて昇格先を追記します。

## Status Legend

- [ ] 未着手
- [x] feature spec化済み

## Area Legend

- Frontend: Expo React Native app側
- Backend: FastAPI / SQLite / worker / storage側
- Both: FrontendとBackendの両方にまたがる

## Ideas

### Backend

- [ ] Orphan original file cleanup
  - Area: Backend
  - Trigger: `POST /assets/upload`でoriginal保存後にDB書き込みが失敗し、保存済みoriginalの削除にも失敗した場合。
  - Idea: DBに記録がない迷子original fileを検出し、確認・清掃できる仕組みを作る。
  - Minimum behavior before this feature:
    - Upload API側では、DB失敗時に保存済みoriginalを削除しようとする。
    - 削除できなかった場合はログに残す。
    - 本格的な検出/清掃はこのpre ideaから別featureとして扱う。
  - Notes:
    - 自動削除の扱いは慎重にする。
    - original非改変ルールは維持する。
    - 清掃対象は「DBにasset recordが存在しないoriginal」に限定する。
  - Candidate feature spec: `docs/ideas/YYYYMMDD-orphan-original-cleanup.md`

### Frontend

- [ ] No frontend-only pre ideas yet
  - Area: Frontend
  - Notes: 必要になったら追加する。

### Both

- [ ] No cross-cutting pre ideas yet
  - Area: Both
  - Notes: 必要になったら追加する。
