# kaigi v7 specification

## Goal

agentchattrを `safe selection → wake/launch → Council → durable run → recovery → evidence proof → advisory handoff → TO DO queue` まで扱う。upstream routing/Sessions/`wrapper.py`/`wrapper_api.py`/Jobs RESTを再実装せず利用し、Evidence / Decision / Authority / Executionの境界を維持する。

## UX

- `kaigi "TOPIC"`: safe-auto Council + proof。
- `kaigi @agent TEXT`: direct message互換。
- `kaigi decide TOPIC`: safe-auto明示形。
- `kaigi reconcile/resume/recover`: incomplete Council回復。
- `kaigi packet/verify/handoff/audit`: proof surface。
- `kaigi queue [RUN]`: verified Decisionをupstream Jobs TO DOへ登録。
- `kaigi jobs`: upstream Jobs read surface。

## Safe-auto / Council

v6 contractを継承。local API/設定済みCLIを既定候補、cloud APIと分類不能online participantは既定除外。FINAL reply実観測のみcomplete。各Council outboundは`RUN=<run_id>`を保持。

## Invocation binding

v6ではCouncil完了後のpacket生成が`latest.json`参照に依存する狭いraceを持ち得た。v7のsafe-auto wrapperは、同一process内で`core.new_run()`が返したrun IDをそのinvocationのidentityとして捕捉する。

v6内部が`load_run(None)`を行う場合、そのinvocation中だけ捕捉run IDへ解決する。処理終了後にmonkey-patchは必ずrestoreする。これにより他process/別runによるlatest更新をproof identityへ採用しない。

## Decision packet additive provenance

既存schema `kaigi.decision_packet.v1`を後方互換のままadditive拡張する。

### Structured decision

`decision.text`はraw FINALを保持し、`decision.sections`へ以下のheadingをmachine-readable抽出する。

- decision
- why
- dissent
- risks
- next_actions

headingが存在しないsectionは捏造しない。

### Runtime provenance

`provenance`:

- `kaigi_version = 7.0.0`
- runtime files: `kaigi`, `kaigi_core.py`, `kaigi_ops.py`, `kaigi_v6.py`, `kaigi_v7.py`
- 各fileのSHA-256とbyte size
- `runtime_sha256 = SHA256(canonical({kaigi_version, files}))`

packet全体を再hashし、run ledger `decision_packet` pointerも新packet hash/runtime hashへ更新する。

`kaigi verify --current-runtime`はpacket provenanceの内部hash検証に加え、現在runtimeを再fingerprintしてpacket作成時と同一か要求する。旧packetにprovenanceが無い場合、通常verifyは互換維持するが`--current-runtime`はfailする。

## Upstream Jobs facts / mapping

upstream `JobStore` status:

- `open` = UI `TO DO`
- `done` = UI `ACTIVE`
- `archived` = UI `CLOSED`

upstream `POST /api/jobs`は`jobs.create(...)`を呼び、現行Store default statusは`done`。create API bodyはtitle/type/channel/created_by/anchor_msg_id/assignee/bodyを使用し、statusをcreateへ渡さない。

`PATCH /api/jobs/{job_id}`はstatus/title/assignee更新を受け、statusは`open|done|archived`。

`DELETE /api/jobs/{job_id}?permanent=true`はpermanent delete経路。

JobStore create/update callbackはbroadcastへ接続される。agent routingはJob thread message側で発生するため、kaigi queueはJob thread messageを自動投稿しない。

## Queue contract

`kaigi queue [RUN_ID]`:

1. complete run/packetを解決。packetが無ければ生成可。
2. v7 packet verificationを行う。既定は`live=true`でexact evidence IDsを再取得してtranscript hashまで確認。`--no-live`で明示省略可。
3. `GET /api/jobs`で同一markerを検索。
4. `KAIGI-RUN:<run_id>` + `KAIGI-PACKET:<packet_sha256>`一致Jobがあればidempotent returnし、重複POSTしない。
5. 未存在なら`POST /api/jobs`。title/type/job channel/created_by=kaigi/final message anchor/optional assignee/bodyを送る。
6. POST成功直後に必ず`PATCH status=open`。
7. PATCHが例外またはresponse status != openなら、今作ったJobを`DELETE ?permanent=true`でcompensating rollbackする。誤ACTIVEを成功として残さない。
8. open確認後のみrun ledgerへ`job_bridge` pointerを保存し成功表示。

### Queue body

upstream bodyはStoreで1000 charsにtruncateされるため、identity/authority markerを先頭へ置く。

```text
KAIGI-RUN:<run_id>
KAIGI-PACKET:<packet_sha256>
AUTHORITY: advisory; execution_authorized=false; requires_separate_authority=true

DECISION:
<raw final text, remaining budget内>
```

markerがtruncateされないようdecision text側を先に縮める。

### Dry-run

`kaigi queue --dry-run`はJobs API writeを行わない。既存packetを要求し、local verify後にtitle/assignee/status/authority予定だけ表示する。

## Jobs read surface

`kaigi jobs [--channel X] [--status open|done|archived]`は`GET /api/jobs`のみ。id/status/assignee/source/titleを表示し、bodyにKAIGI-RUN markerがあればsource=kaigiと表示する。

## Authority contract

Queueはwork record生成でありExecutionではない。

- Decision packet: advisory-decision / execution_authorized=false
- handoff: advisory / execution_authorized=false / requires_separate_authority=true
- queue body: same authority marker
- queueはJob threadへagent mention/messageを送らない
- agent dispatch/activationはv7では自動化しない

## Verification gate

```bash
python -m py_compile kaigi kaigi_core.py kaigi_ops.py kaigi_v6.py kaigi_v7.py
python -m unittest discover -v tests -p 'test_*.py'
bash -n install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
```

Regression minimum:

- v4/v5/v6 behavior retained
- version 7.0.0
- safe-auto cloud governance/direct mention/tamper/live proof
- runtime provenance + structured decision
- `--current-runtime` PASS for unchanged runtime
- invocation binding resolves own run, not foreign latest
- queue creates `open` TO DO
- queue body run/packet/authority binding
- queue idempotency: second call no POST
- PATCH failure permanent rollback, no lingering job
- queue dry-run: zero Jobs write
- jobs list source/status
- installer idempotency + 4 skill locations

## Install

Repository runtime set: `kaigi`, `kaigi_core.py`, `kaigi_ops.py`, `kaigi_v6.py`, `kaigi_v7.py`, `SKILL.md`。installerはruntime file欠落時にfailし、CLI symlinkとClaude/Hermes/Codex/OpenCode skill配布を冪等実行する。
