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

relayはfull Council transcriptをcloudへ返さない。返すのは最終decision、run ID、`packet_sha256`, `transcript_sha256`。Decision packet本体とtranscriptはlocalに残る。

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
- `relay/supabase/functions/kaigi-relay/index.ts`

pairing code / device token / service-role key等のsecretはrepoへ入れない。

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
