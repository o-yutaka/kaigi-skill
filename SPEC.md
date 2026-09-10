# kaigi v6 specification

## Goal

agentchattrを `safe agent selection → agent wake/launch → parallel independent analysis → dissent → synthesis → durable run → recovery → proof packet → advisory handoff` までprovider-neutralに扱う。upstream routing / Sessions / `wrapper.py` / `wrapper_api.py` を尊重し、特定AI providerをcontrol-plane dependencyにしない。

## Provider-neutral contract

- required providers = none。
- Claude / Codex / ChatGPT / Gemini / Hermes / local model等はoptional adapter。
- safe-auto candidate orderingにprovider名の固定priorityを持たせない。
- FINAL synthesizer selectionにprovider名の固定priorityを持たせない。
- legacy named prioritiesはentrypoint policyで無効化する。
- provider固有skill directoryをruntime prerequisiteにしない。
- `kaigi policy [--json]` はactive provider policyを表示する。

### Synth policy

precedence:

1. explicit `--synth NAME`
2. `KAIGI_SYNTH_AGENT`
3. `KAIGI_SYNTH_POLICY`

既定`balanced`:

- current participantsのみ候補。
- complete runをnewest-firstで調べる。
- never-used successful synthを先に選ぶ。
- 全員使用済みならsuccessful FINAL担当から最も時間が空いているparticipantを選ぶ。
- tieはparticipant order。

`KAIGI_SYNTH_POLICY=first` はparticipant order先頭。未知policyはfail closed。

## Public UX

- `kaigi "TOPIC"` — provider-neutral safe-auto Council。complete後proof packetを自動生成。
- `kaigi @agent TEXT` — direct say shortcut。
- `kaigi say TEXT` — 明示単発送信。
- `kaigi decide TOPIC` — safe-auto明示形。
- `kaigi convene TOPIC` — lower-level Council/native Sessions入口。
- `kaigi launch A B` — CLI agentをupstream通常wrapperで起動。
- `kaigi go TOPIC --agents A,B` — 明示agent準備→Council。
- `kaigi reconcile/resume/recover` — 未完了Council回復。
- `kaigi packet/verify/handoff/audit` — proof surface。
- `kaigi policy` — provider policy surface。

## Safe-auto selection contract

候補は`load_agents_config()`と`/api/status`のunion。

classification:

- `type="api"` + loopback host = `api-local`
- `type="api"` + non-loopback = `api-cloud`
- `command`を持つ非API agent = `cli`
- configで分類不能だがonline = `online-unclassified`
- その他 = `unclassified`

既定除外:

- cloud API（`--allow-cloud`で許可）
- online-unclassified（`--allow-unknown`で許可）
- unconfigured/unclassified
- `user`, `system`, bot

明示`--agents`はその対象について明示同意と扱うが、online実観測できない対象があれば開始拒否。

### Ordering

provider名ではなくoperational propertyのみでsortする。

1. online実観測済み before offline
2. `api-local` before `cli` before explicitly-allowed `api-cloud` before explicitly-allowed `online-unclassified`
3. 同条件のみagent名をdeterministic tie-breakに使用

default `max_agents=4`, `min_agents=2`。`KAIGI_AUTO_MAX_AGENTS`, `KAIGI_AUTO_MIN_AGENTS`で変更可。

`--dry-run`はlaunch/wake/send/run creationを行わない。

## Council contract

ROUND1=independent fan-out、ROUND2=dissent/review fan-out、FINAL=synthesis。FINAL replyを実観測した場合だけcomplete。

各outbound Council markerは`RUN=<run_id>`を含む。run ledgerはparticipants, roles, synth, outbound message IDs, round replies, final replyを保持する。

## Run persistence / recovery

`KAIGI_STATE_DIR` default `~/.local/state/kaigi`。

- `runs/<run_id>.json`
- `latest.json`
- atomic write
- `reconcile`はchat read-only、outbound zero。
- `resume`はreconcile相当を先行し、存在するROUND2/FINAL markerを再送しない。
- `recover`はreconcile→resume、complete時にpacket未生成なら生成する。

Council以外のrunはresume/reconcile/recover対象外。

## Decision packet contract

Path: `packets/<run_id>.json`
Schema: `kaigi.decision_packet.v1`

complete Councilのみ生成可能。packetは少なくとも:

- run: run_id, kind, topic, channel, server, state, timestamps, participants, roles, synth
- decision: final sender, final message id, final text
- round1 / round2 records
- kickoff / round2 prompt / final prompt / final reply IDs
- evidence_ids
- normalized transcript (`id`, `sender`, `text`, `channel`)
- `transcript_sha256`
- `packet_sha256`
- authority classification

を持つ。

### Canonical hash

UTF-8 JSON、`ensure_ascii=false`, `sort_keys=true`, separators `(',', ':')`。SHA-256。

`transcript_sha256 = SHA256(canonical(transcript))`。
`packet_sha256 = SHA256(canonical(packet_without_packet_sha256))`。

### Evidence selection

run ledgerに束縛されたevidence message IDsのみをID昇順で取得する。

- kickoff
- recorded ROUND1 replies
- ROUND2 prompt
- recorded ROUND2 replies
- FINAL prompt
- FINAL reply

要求IDをagentchattrから取得できない場合、proof packet生成を成功扱いしない。

## Verify contract

`kaigi verify RUN` local:

1. schema
2. packet_sha256再計算
3. transcript_sha256再計算
4. run ledger final_text一致
5. ledger packet pointer hash一致（pointer存在時）

`--live`は上記後、evidence_idsをagentchattrから同channelで再取得しnormalized live transcript hashも照合。不一致はexit non-zero。改ざん/欠落を推測補完しない。

## Handoff contract

Path: `handoffs/<run_id>.json`
Schema: `kaigi.handoff.v1`

Decision packetをlocal verify後に生成し、source run ID / packet SHA / transcript SHA / topic / participants / synth / decisionを含む。

authority固定:

- `classification="advisory"`
- `execution_authorized=false`
- `requires_separate_authority=true`

会議/Decision/EvidenceをAuthority/Executionへ自動昇格しない。

## Agent adapter contract

CLI agentはupstream `wrapper.py AGENT`。skip-permissions/bypass/yolo launcherを自動利用しない。API agentはupstream `wrapper_api.py`。

local APIのみ通常Councilで暗黙wake可。cloud APIは明示`--allow-cloud`、`--agents`、`wake NAME`、`api start NAME`等の場合のみ対象。API secret値はrepository/configへ保存せず環境変数名だけ保持する。

## Skill install contract

canonical provider-neutral skill:

`~/.local/share/kaigi/skills/kaigi/SKILL.md`

これは常にinstallする。

provider/tool-specific skill locationsはoptional adapter。

- `KAIGI_SKILL_TARGETS=auto`（default）: 既存root / CLI / explicit homeを検出したadapterのみinstall。
- `none`: canonicalのみ。
- comma list: 指定adapterのみ。
- `all`: 全既知adapter。

存在しないprovider directoryを本体依存として無条件生成しない。

## Compatibility

v5 `launch/go/reconcile/resume`、v4 core surface、v6 safe-auto/proof/recover、native Sessions、interactive roomを保持する。provider integrationは削除せずoptional adapterとして残す。

## Verification gate

GitHub CI minimum:

```bash
python -m py_compile kaigi kaigi_core.py kaigi_ops.py kaigi_v6.py kaigi_policy.py
python -m unittest discover -v tests -p 'test_*.py'
bash -n install.sh
```

Regression must cover:

- existing core/ops/v6 regressions
- bare topic→safe-auto Council→packet
- cloud API excluded by default
- explicit cloud inclusion
- direct `@agent` compatibility
- dry-run outbound zero / no run creation
- packet/live transcript tamper detection
- handoff `execution_authorized=false`
- audit proof PASS
- provider-neutral no-named-vendor E2E (`alpha`, `beta`, `cloud-x` only)
- balanced synth rotation
- `required_providers=[]` policy surface
- empty HOME + `KAIGI_SKILL_TARGETS=none` 2回installでcanonical skillだけ存在しprovider-specific dirsが作られないこと
- `KAIGI_SKILL_TARGETS=all`でoptional adaptersを配置可能なこと

GUI terminal spawnや外部provider実replyをCIで観測していない場合、それらをPASSと報告しない。
