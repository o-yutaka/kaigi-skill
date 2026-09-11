# kaigi v8

provider-neutral / capability-aware なマルチAI会議control plane。`能力推定→cast→Council→proof→recover` に加え、v8ではChatGPT等のcloud側から手元PCのkaigiへ安全に会議だけ依頼できる outbound relay を追加した。

特定AI providerは必須ではない。Claude / Codex / ChatGPT / Hermes / local model等はすべてoptional adapter。

## 最短

```bash
cd ~/kaigi-skill
git pull
bash install.sh
kaigi "この設計を本番採用すべきか"
```

bare textはcapability-aware Council。個別送信だけ `@agent` を使う。

```bash
kaigi @agent-a この差分だけ見て
kaigi cast "このPRを本番投入していいか"
kaigi result
```

## Capability cast

```bash
kaigi caps
kaigi caps set local-a coding research deep-reasoning --cost local --speed fast --context 24576
kaigi caps set reviewer red-team research --cost free
kaigi decide "この変更を監査" --need coding,red-team --free-only
```

議題からの能力推定はsoft、`--need`はhard。明示capabilityをcoverできなければfail-closedし、妥協する場合だけ `--best-effort-capabilities` を使う。

選定は概念的に `hard capability coverage → inferred/preferred coverage → cost → online → speed → observed response reliability → deterministic tie-break`。provider名は優先順位に使わない。

## ChatGPT/cloud → local kaigi relay

v8 relayはPC側で外向きHTTPS pollingする。localhost/agentchattrをインターネットへ公開せず、外部から任意shell commandも受け付けない。

```text
ChatGPT / cloud control side
        ↓ enqueue
Supabase kaigi_relay_requests
        ↓ authenticated claim
local `kaigi relay` worker
        ↓
capability cast → Council → Decision packet verify
        ↓
final decision + packet/transcript SHA256 only
        ↓
Supabase result
```

pairはsingle-use codeで行い、端末ごとのrandom worker tokenを1回だけ受け取る。Supabaseにはtoken本体ではなくSHA-256だけ保存し、PC側tokenは `~/.config/kaigi/relay.json` に0600で保存する。

```bash
kaigi relay pair --url https://PROJECT.supabase.co/functions/v1/kaigi-relay
# Pair code: は非表示prompt
kaigi relay start
kaigi relay status
```

通常はbackground workerがpending requestをpollする。1件だけ処理する場合:

```bash
kaigi relay once
```

remoteから許可されるのはCouncil用optionだけ:

- `need`, `prefer`
- `free_only`
- `allow_cloud`
- `max_agents`, `min_agents`, `quorum`, `round_timeout`
- `best_effort_capabilities`
- `no_launch`

`agents`, `synth`, shell command、任意subcommand等はrelayから指定できない。remote `allow_cloud=true` もlocal policyが既定denyする。

```bash
kaigi relay config --allow-remote-cloud   # 明示時だけ許可
kaigi relay config --deny-remote-cloud
```

relay requestはlocal runへ `relay_request_id` / request hashで束縛する。途中中断したrunは新規Councilを作らずrecover対象にする。完了後remote返送だけ失敗した場合は0600 local receiptから結果を再送し、Councilを二重起動しない。

relayはfull Council transcriptをcloudへ返さない。返すのは最終decision、run ID、`packet_sha256`, `transcript_sha256`。

### Local agentchattr auth / control identity

現行agentchattrはhuman/browser sessionとregistered-agent identityを分離している。browser/controlはsession token付きWebSocket `/ws`、agent側REST `/api/send` は `/api/register` で発行されたper-agent Bearerを使う。

kaigiはagentではなくmeeting control planeなので、通常のCouncil kickoff/ROUND2/FINAL依頼はhuman/control sessionとしてWebSocketから投稿する。kaigi自身を `/api/register` せず、Codex等のagent identityやslotをmint/impersonateしない。明示的なregistered-agent Bearerを渡した時だけREST `/api/send` を使う。

```text
explicit registered-agent Bearer ──→ REST /api/send
human/control session token       ──→ WebSocket /ws?token=...
```

detached relay daemonはinteractive shellのauth envを継承しない場合があるため、kaigiは次の順でlocal authを解決する。

```text
explicit KAIGI/AGENTCHATTR auth env
        ↓ absent
live loopback index session token
        ↓ unavailable
legacy server-log fallback
```

live discoveryとsession WebSocket送信はloopbackに限定し、tokenはmemory上だけで使ってdiskへ保存しない。agentchattr再起動でsession tokenがrotationしても次回requestで現行tokenを再取得する。

旧agentchattrとの互換性のためsession-auth RESTを試せるが、現行serverの明示的なBearer-only `/api/send` エラーを観測した場合だけWebSocketへ切り替える。その他の401/403はfail-closedする。

### Relay progress hardening

実機で「workerは生存しているがCouncilのどのstageで待っているかcloud側から判別できない」状態を検出したため、v8 relayには `8.1-progress-watchdog` hardeningを含める。

実行中はauthenticated heartbeatに、本文ではなく次の最小metadataだけを載せる。

- local `run_id`
- `stage` / `state`
- ROUND1 / ROUND2 reply件数
- participant件数
- synthesizer identity

Council発言本文、Decision packet、agent secretはprogress heartbeatへ含めない。`kaigi relay status` でも同じactive stageを確認できる。

さらに preparation、各Council stage、全体実行に独立watchdogを置く。stageが進まないまま許容時間を超えた場合はsuccessにせず明示エラーで停止する。`kaigi relay stop` はdaemonだけでなく、そのdaemonが実行中のCouncil子processも同じprocess treeとして停止する。

claimed requestのlocal処理が例外終了した場合はremote stateを必ずterminal `failed`へ移し、lease expiry後に同じ失敗requestを無限reclaimしない。

## Provider-neutral policy

```bash
kaigi policy --json
```

Invariant:

- required providers = `[]`
- provider名によるauto priority = none
- synthesizer = balanced（明示 `--synth` はlocal/direct CLI時のみ最優先）
- local API = auto候補 / wake可
- CLI agent = auto候補 / upstream normal wrapperでlaunch可
- cloud API = 暗黙利用しない

## Council / proof / recovery

Councilは `ROUND1 independent fan-out → ROUND2 dissent/review → FINAL synthesis`。FINAL replyの実観測だけをcomplete扱いする。

```bash
kaigi packet [RUN_ID]
kaigi verify [RUN_ID]
kaigi verify [RUN_ID] --live
kaigi reconcile [RUN_ID]
kaigi resume [RUN_ID]
kaigi recover [RUN_ID]
kaigi handoff [RUN_ID]
kaigi audit
```

Decision packetはmessage IDs、evidence transcript、capability plan、final decision、SHA256を束縛する。handoffは常にadvisoryで、`execution_authorized=false`。会議の結論を実行権限へ自動昇格しない。

## Supabase reproducibility

relay cloud sideのcanonical sourceをrepoにも保持する。

- `relay/supabase/schema.sql`
- `relay/supabase/migrations/001_v8_1_progress.sql`
- `relay/supabase/functions/kaigi-relay/index.ts`

fresh deploymentはbase schemaの後に `relay/supabase/migrations/` を順番に適用する。pairing code / device token / service-role key等のsecretはrepoへ入れない。

## Install

```bash
bash install.sh
```

canonical skillだけmandatory。provider固有skill pathはoptional adapter。

```bash
KAIGI_SKILL_TARGETS=none bash install.sh
KAIGI_SKILL_TARGETS=codex,opencode bash install.sh
KAIGI_SKILL_TARGETS=all bash install.sh
```

Python 3.11+ 推奨。主な設定: `AGENTCHATTR_HOME`, `AGENTCHATTR_SERVER`, `KAIGI_STATE_DIR`, `KAIGI_CONFIG_DIR`, `KAIGI_CAPABILITY_REGISTRY`, `KAIGI_RELAY_CONFIG`。
