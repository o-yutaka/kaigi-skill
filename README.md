# kaigi v6

agentchattr のマルチAI会議を、`AI選定/起動 → 独立案 → 反証 → 最終統合 → 永続run → 再開 → proof packet → handoff` まで1本で扱うterminal CLI。

## 最短

```bash
cd ~/kaigi-skill
git pull
bash install.sh
kaigi "この設計を本番採用すべきか"
```

v6では、`@agent` で始まらないbare textは会議議題として扱います。

```bash
kaigi "BLACKの次に実装すべきものを決める"
kaigi @claude この差分だけ見て
kaigi say "general channelへ単発送信"
```

`kaigi "議題"` は既定で **safe-auto**。設定済み/online agentを分類し、cloud APIを暗黙には参加させず、最大4 agentを準備してCouncilを完走し、complete後にSHA256付きDecision packetを生成します。

## safe-auto governance

既定:

- local API agent: 自動候補、自動wake可
- CLI agent: 自動候補、offlineなら通常 `wrapper.py` で起動可
- cloud API agent: 自動候補から除外
- configで分類不能なonline agent: 自動候補から除外
- system/user/bot: 除外

確認だけ:

```bash
kaigi decide "議題" --dry-run
```

cloud APIを明示的に許可:

```bash
kaigi decide "議題" --allow-cloud
```

参加者を固定する場合は、その指定自体を明示同意として扱います。

```bash
kaigi decide "議題" --agents claude,codex,chatgpt
```

制御:

```bash
kaigi decide "議題" --max-agents 6
kaigi decide "議題" --min-agents 2
kaigi decide "議題" --quorum 2
kaigi decide "議題" --synth claude
kaigi decide "議題" --round-timeout 90
kaigi decide "議題" --no-launch
kaigi decide "議題" --no-packet
```

## Council

Councilは3段です。

1. ROUND 1 — 全参加者を同時triggerし、他者を待たず独立分析。
2. ROUND 2 — 実際に返答した参加者だけを同時triggerし、反証・危険な前提・見落とし・修正版を出す。
3. FINAL — synthesizerが全ログを比較し `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` へ統合。

FINAL replyを実観測した場合だけcompleteです。各outbound markerには `RUN=<run_id>` が入り、message IDと組み合わせて再照合できます。

## Proof packet

complete Councilは `~/.local/state/kaigi/packets/<run_id>.json` にDecision packet化できます。safe-auto経由では自動生成されます。

```bash
kaigi packet
kaigi packet RUN_ID
kaigi verify
kaigi verify RUN_ID --live
```

packetは以下を束縛します。

- run ID / topic / participants / roles / synth
- ROUND1/ROUND2/FINALの実message IDs
- 実際に取得したevidence transcript
- `transcript_sha256`
- packet全体の `packet_sha256`
- final decision
- `execution_authorized=false`

`kaigi verify` はpacket hash・transcript hash・run ledger pointer/final整合を検証します。`--live` はagentchattrから同じmessage IDsを再取得し、現在のlive transcriptがpacketと一致するかまで確認します。

## Handoff

会議結果を下流agentやBLACKへ渡す時は:

```bash
kaigi handoff
kaigi handoff RUN_ID --stdout
```

`kaigi.handoff.v1` はDecision packet hashに束縛されますが、権限は明示的に:

```text
authority.classification = advisory
execution_authorized = false
requires_separate_authority = true
```

つまり **会議の結論 ≠ 実行権限** を維持します。

## 未完了Councilを捨てない

```bash
kaigi reconcile [RUN_ID]   # chat実績だけ再照合。新規送信なし
kaigi resume [RUN_ID]      # 不足stageだけ続行
kaigi recover [RUN_ID]     # reconcile→resume→completeならpacketまで
```

`resume/recover` は既に存在するROUND2/FINAL markerを再送しません。completeなら再実行せず結果を回収します。

## 結果・監査

```bash
kaigi result [RUN_ID]
kaigi history 50
kaigi export RUN_ID --format json -o result.json
kaigi audit
```

`audit` は保存runごとにDecision packetのlocal hash/ledger整合を確認します。

## CLI AIを起こす

agentchattr公式 `wrapper.py AGENT` を使います。独自runnerではありません。

```bash
kaigi launch claude codex
kaigi launch --all
kaigi launch claude --here
kaigi launch claude --dry-run
```

通常wrapperのみを使い、skip-permissions / bypass / yolo系launcherは自動選択しません。

## API AI / local model

```bash
kaigi wake
kaigi wake localglm
kaigi api add localglm --base-url http://127.0.0.1:8080/v1 --model my-model
kaigi api list
```

OpenAI互換endpointなら追加可能です。secret値は `config.local.toml` に保存せず、環境変数名だけ保存します。

ChatGPT bridgeはWeb/App/Plus login流用ではなくOpenAI API agentです。

```bash
export OPENAI_API_KEY="..."
kaigi chatgpt setup
kaigi chatgpt start
kaigi chatgpt status
```

## Native Sessions / interactive room

既存の明示操作も維持します。

```bash
kaigi convene "議題" --agents claude,codex
kaigi templates
kaigi convene "実装計画" --template planning
kaigi room
kaigi log 30
kaigi watch
kaigi agents
kaigi doctor
kaigi open
```

## Install

`install.sh` は `kaigi`, `kaigi_core.py`, `kaigi_ops.py`, `kaigi_v6.py`, `SKILL.md` の存在を確認し、`~/.local/bin/kaigi` をrepositoryへsymlinkします。同じSKILLを Claude / Hermes / Codex / OpenCode のglobal skill locationへ冪等配置します。

主な環境変数: `AGENTCHATTR_HOME`, `AGENTCHATTR_SERVER`, `KAIGI_CHANNEL`, `KAIGI_STATE_DIR`, `KAIGI_TOKEN`, `KAIGI_BEARER_TOKEN`, `KAIGI_AUTO_MAX_AGENTS`, `KAIGI_AUTO_MIN_AGENTS`。Python 3.11+ 推奨。
