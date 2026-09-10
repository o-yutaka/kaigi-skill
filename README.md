# kaigi v6

agentchattr のマルチAI会議を、`AI選定/起動 → 独立案 → 反証 → 最終統合 → 永続run → 再開 → proof packet → handoff` まで1本で扱う provider-neutral terminal CLI。

## 最短

```bash
cd ~/kaigi-skill
git pull
bash install.sh
kaigi "この設計を本番採用すべきか"
```

`@agent` で始まらないbare textはsafe-auto Councilとして扱います。個別送信は明示します。

```bash
kaigi @agent-a この差分だけ見て
kaigi say "general channelへ単発送信"
```

## Provider-neutral contract

kaigi本体が必須とするAI providerは **0** です。Claude / Codex / ChatGPT / Gemini / Hermes / local model等はすべて同格のoptional adapterであり、どれか1つが無くてもcontrol planeは成立します。

確認:

```bash
kaigi policy
kaigi policy --json
```

主要不変条件:

- `required_providers = []`
- safe-autoにprovider名の固定優先順位を持たせない
- FINAL synthesizerにprovider名の固定優先順位を持たせない
- cloud APIを暗黙起動/暗黙参加させない
- provider固有skill directoryをruntime依存にしない

既定synth policyは `balanced`。complete済みrunをnewest-firstで見て、現在の参加者のうち最も最近FINAL担当していないAIを優先します。never-usedが複数なら参加順で決めます。

```bash
KAIGI_SYNTH_AGENT=agent-b kaigi "議題"    # 明示固定
KAIGI_SYNTH_POLICY=first kaigi "議題"     # 参加順先頭
```

## Safe-auto governance

`kaigi "議題"` / `kaigi decide "議題"` は、config + `/api/status` から候補を分類します。選定基準はブランド名ではなくruntime属性です。

優先順位:

1. online実観測済み > offline
2. local API > CLI > 明示許可されたcloud API > 明示許可されたunclassified online
3. 同条件だけagent名を決定論的tie-breakに使用

既定:

- local API: 自動候補、自動wake可
- CLI agent: 自動候補、offlineなら通常 `wrapper.py` で起動可
- cloud API: 自動候補から除外
- configで分類不能なonline participant: 自動候補から除外
- `user`, `system`, bot: 除外

確認のみ:

```bash
kaigi decide "議題" --dry-run
```

cloud APIを明示許可:

```bash
kaigi decide "議題" --allow-cloud
```

参加者を明示固定:

```bash
kaigi decide "議題" --agents agent-a,agent-b,local-llm
```

制御:

```bash
kaigi decide "議題" --max-agents 6
kaigi decide "議題" --min-agents 2
kaigi decide "議題" --quorum 2
kaigi decide "議題" --synth agent-a
kaigi decide "議題" --round-timeout 90
kaigi decide "議題" --no-launch
kaigi decide "議題" --no-packet
```

## Council

Councilは3段です。

1. ROUND 1 — 全参加者を同時triggerし、他者を待たず独立分析。
2. ROUND 2 — 実際に返答した参加者だけを同時triggerし、反証・危険な前提・見落とし・修正版を出す。
3. FINAL — provider-neutral synth policyで選ばれた参加者が全ログを比較し `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` へ統合。

FINAL replyを実観測した場合だけcompleteです。各outbound markerには `RUN=<run_id>` が入り、message IDと組み合わせて再照合できます。

## Proof packet

complete Councilは `~/.local/state/kaigi/packets/<run_id>.json` に `kaigi.decision_packet.v1` として保存できます。safe-auto完走時は自動生成されます。

```bash
kaigi packet [RUN_ID]
kaigi verify [RUN_ID]
kaigi verify [RUN_ID] --live
```

packetはrun ID/topic/participants/roles/synth、ROUND1/ROUND2/FINALの実message IDs、evidence transcript、final decision、`transcript_sha256`、`packet_sha256` を束縛します。

`verify` はlocal packet hash・transcript hash・run ledger pointer/final整合を検証します。`--live` は同message IDsをagentchattrから再取得し、live transcriptまで再照合します。

## Authority boundary / Handoff

```bash
kaigi handoff [RUN_ID]
kaigi handoff [RUN_ID] --stdout
```

handoffはDecision packet hashに束縛されますが、権限は固定です。

```text
authority.classification = advisory
execution_authorized = false
requires_separate_authority = true
```

つまり **Evidence / Decision ≠ Authority / Execution** を維持します。

## Recovery

```bash
kaigi reconcile [RUN_ID]   # chat実績だけ再照合。新規sendなし
kaigi resume [RUN_ID]      # 不足stageだけ続行
kaigi recover [RUN_ID]     # reconcile→resume→completeならpacketまで
```

既存 `RUN=<run_id>` markerとmessage IDsを確認し、送信済みROUND2/FINALを二重送信しません。

## Result / Audit

```bash
kaigi result [RUN_ID]
kaigi history 50
kaigi export RUN_ID --format json -o result.json
kaigi audit
```

## CLI / API adapters

CLI agentはupstream agentchattrの通常 `wrapper.py AGENT` を使います。独自provider runnerやskip-permissions / bypass / yolo launcherを自動選択しません。

```bash
kaigi launch agent-a agent-b
kaigi launch --all
kaigi launch agent-a --dry-run
```

OpenAI-compatible API agentはgenericに追加できます。

```bash
kaigi api add local-llm --base-url http://127.0.0.1:8080/v1 --model my-model
kaigi api list
kaigi api start local-llm
```

secret値はconfig/repositoryへ保存せず、環境変数名だけ保持します。

ChatGPT bridgeもoptional adapterです。Web/App/Plus session流用ではなくAPI経路であり、ChatGPTが無くてもkaigi本体は動作します。

```bash
export OPENAI_API_KEY="..."
kaigi chatgpt setup
kaigi chatgpt start
```

## Skill install

canonical provider-neutral skillは常にここへ入ります。

```text
~/.local/share/kaigi/skills/kaigi/SKILL.md
```

Claude / Hermes / Codex / OpenCode等のtool固有skill locationはoptional adapterです。既定 `auto` では既存環境またはCLIが検出された場合だけ配置します。

```bash
KAIGI_SKILL_TARGETS=none bash install.sh
KAIGI_SKILL_TARGETS=codex,opencode bash install.sh
KAIGI_SKILL_TARGETS=all bash install.sh
```

`none` ではcanonical skillだけ入り、provider固有directoryを作りません。

## Native Sessions / room

既存の低レベル操作も維持します。

```bash
kaigi convene "議題"
kaigi templates
kaigi convene "実装計画" --template planning
kaigi room
kaigi log 30
kaigi watch
kaigi agents
kaigi doctor
kaigi open
```

## Verification

GitHub ActionsはPython compile、全unit regression、no-provider-name E2E、provider-neutral installerの2回冪等実行、optional adapter installを検証します。GUI terminal spawnや外部provider実replyなどCIで観測していないものはPASS扱いしません。
