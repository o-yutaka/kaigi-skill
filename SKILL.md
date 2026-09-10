---
name: kaigi
description: agentchattrのマルチAI会議をprovider-neutralに操作する。safe-auto選定、AI起動、独立分析/反論/統合、中断run再照合/再開、Decision proof、改ざん検証、advisory handoff、CLI/API adapter連携が必要な時に使う。
---
# kaigi

`kaigi` は特定AIベンダーに依存しない会議control planeとして扱う。

## Provider-neutral invariant

- required providerは0。Claude / Codex / ChatGPT / Gemini / Hermes / local model等はoptional adapter。
- provider名によるsafe-auto優先順位を持たせない。
- provider名によるFINAL synth優先順位を持たせない。
- default synthは`balanced`。complete run履歴からFINAL担当の偏りを減らす。
- explicit `--synth NAME` > `KAIGI_SYNTH_AGENT` > synth policy。
- cloud APIを暗黙起動/暗黙参加させない。
- provider固有skill directoryをkaigi本体の依存先にしない。

確認:

```bash
kaigi policy
kaigi policy --json
```

## 通常フロー

```bash
kaigi "議題"
```

bare textはsafe-auto Council。個別送信は先頭`@NAME`または`kaigi say`。

```bash
kaigi @agent-a この差分だけ見て
kaigi say "general channelへ送る"
```

## Safe-auto

候補はconfig + `/api/status` から分類する。選択はprovider名ではなくruntime属性で行う。

1. online > offline
2. local API > CLI > 明示許可cloud API > 明示許可unclassified online
3. 同条件だけagent名を決定論的tie-breakに使用

local APIは暗黙候補/wake可。CLIは暗黙候補で通常`wrapper.py`起動可。cloud APIは`--allow-cloud`または明示`--agents`時だけ。分類不能onlineは`--allow-unknown`時だけ。

```bash
kaigi decide "議題" --dry-run
kaigi decide "議題" --agents agent-a,agent-b,local-llm
kaigi decide "議題" --allow-cloud
```

## Council

ROUND1=独立fan-out、ROUND2=実応答者によるdissent/review、FINAL=provider-neutral synthesis。

FINAL replyを実観測した場合だけcomplete。timeout/欠落を成功扱いしない。各Council outboundに`RUN=<run_id>`を付与する。

## Synthesis

既定`balanced`は現在participantのcomplete run履歴をnewest-firstで調べ、最も最近successful FINALを担当していないparticipantを選ぶ。never-usedが複数なら参加順。

```bash
kaigi decide "議題" --synth agent-a
KAIGI_SYNTH_AGENT=agent-a kaigi "議題"
KAIGI_SYNTH_POLICY=first kaigi "議題"
```

## Recovery

```bash
kaigi reconcile [RUN_ID]
kaigi resume [RUN_ID]
kaigi recover [RUN_ID]
```

`reconcile`はread-onlyで新規sendなし。`resume/recover`は既存message IDsと`RUN=<run_id>`を確認し、送信済みROUNDを再送しない。

## Decision proof

complete Councilは`kaigi.decision_packet.v1`へ変換し、run/topic/participants/roles/synth、実message IDs、evidence transcript、final decision、`transcript_sha256`、`packet_sha256`を束縛する。

```bash
kaigi packet [RUN_ID]
kaigi verify [RUN_ID]
kaigi verify [RUN_ID] --live
```

`--live`は同message IDsをagentchattrから再取得してlive transcriptまで再照合する。

## Authority boundary

Decision packet/handoffは実行権限ではない。

- `authority.classification=advisory`
- `execution_authorized=false`
- `requires_separate_authority=true`

Evidence / DecisionとAuthority / Executionを混同しない。

## Adapter governance

CLI agentはupstream通常`wrapper.py AGENT`を使う。skip-permissions / bypass / yolo系launcherを自動利用しない。

OpenAI-compatible APIはgeneric adapter:

```bash
kaigi api add NAME --base-url URL --model MODEL [--api-key-env ENV]
kaigi api start NAME
```

local APIのみ暗黙wake可。cloudは明示時のみ。secret値はconfig/repositoryへ保存しない。

ChatGPT bridgeはoptional API adapterで、Web/App/Plus session流用ではない。

## Skill install

canonical skill: `~/.local/share/kaigi/skills/kaigi/SKILL.md`。

provider/tool固有skill locationはoptional adapter。`KAIGI_SKILL_TARGETS=none`ならcanonicalだけ、`auto`なら検出済み環境だけ、comma listまたは`all`で明示配布。

## 成功判定

server/API応答、online status、実agent reply、FINAL reply、hash検証など実観測した事実だけ成功として扱う。未観測を推測でPASSにしない。
