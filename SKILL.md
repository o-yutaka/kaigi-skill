---
name: kaigi
description: provider-neutralなマルチAI会議を操作する。capability registry、能力/コスト/online状態/応答実績によるcast、安全なAI起動、独立分析/反論/統合、run再開、Decision proof packet、改ざん検証、非権限handoffが必要な時に使う。
---
# kaigi

`kaigi` はagentchattr上の会議control plane。特定providerを必須・優先にしない。

## 通常フロー

複数AIで判断する時は原則:

```bash
kaigi "議題"
```

bare topicはcapability-aware safe-auto Councilとして扱う。個別agentへの単発送信だけ先頭を`@NAME`にする。

```bash
kaigi @agent-a この差分だけ見て
```

## Capability contract

能力はagent名から推測しない。`~/.config/kaigi/capabilities.json`、agent configのcapability metadata、runtime factのみを使う。

```bash
kaigi caps
kaigi caps set local-a coding research deep-reasoning --cost local --speed fast --context 24576
kaigi caps set reviewer red-team research --cost free
kaigi caps infer "議題"
kaigi cast "議題"
```

標準capability例: `coding`, `research`, `red-team`, `vision`, `long-context`, `local-free`, `fast`, `deep-reasoning`。未知capabilityも拡張可能。

議題からの推定はsoft。`--need`はhardで、cast全体がcoverできなければfail-closedする。

```bash
kaigi decide "議題" --need coding,red-team
kaigi decide "議題" --prefer research,long-context
kaigi decide "議題" --need coding --free-only
```

`--best-effort-capabilities` はユーザーが明示的に要件未充足を許す時だけ使う。

## Cast selection

safe-autoの優先概念:

1. hard capability coverage
2. inferred/preferred capability coverage
3. cost (`free/local`優先)
4. online
5. speed
6. observed ROUND1 response reliability
7. deterministic tie-break

選定後はCouncil role (`planner`, `red-team`, `implementer`, `evidence`, `ux`, `long-horizon`) へのcapability fitが高くなるよう順序を調整する。

応答実績は可用性指標としてのみ使い、回答品質を実測していないのにquality scoreとして扱わない。

## Provider-neutral contract

- required provider: none
- provider名によるauto priority: none
- local API: 暗黙候補/auto-wake可
- CLI agent: 暗黙候補/通常wrapperでlaunch可
- cloud API: 既定除外。明示許可時だけ
- synthesizer: balanced。明示`--synth`が最優先

`kaigi policy --json` でprovider policyを確認できる。

## Council contract

ROUND1=独立fan-out、ROUND2=実応答者によるdissent/review、FINAL=synthesis。

FINAL replyを実観測した場合だけcomplete。timeout/欠落は成功扱いしない。各Council messageに`RUN=<run_id>`を付与する。

v7 runには可能な場合 `capability_plan` を保存する。required/preferred/inferred、selected cast、profile snapshot、coverage、registry SHA256、selection policyを記録する。

## Resume / recover

- `kaigi reconcile [RUN_ID]` — chat実績から副作用なし再照合。outbound zero。
- `kaigi resume [RUN_ID]` — 存在するROUNDを再送せず不足stageだけ進める。
- `kaigi recover [RUN_ID]` — reconcile→resume→completeならpacket生成。

## Decision proof

complete Councilは `kaigi packet [RUN_ID]` で `kaigi.decision_packet.v1` にする。safe-auto完走時は自動生成。

packetはrun/topic/participants/roles/synth、実message IDs、evidence transcript、final decision、`transcript_sha256`, `packet_sha256`を持つ。capability planがある場合はpacketに含め、packet hashへ束縛する。

```bash
kaigi verify [RUN_ID]
kaigi verify [RUN_ID] --live
```

local verifyはhash/ledger/final整合、`--live`は同message IDsのlive transcriptまで確認する。

## Authority boundary

会議結果はadvisory。Decision packet/handoffをAuthority/Executionへ自動昇格しない。

`kaigi handoff [RUN_ID]` は:

- `authority.classification=advisory`
- `execution_authorized=false`
- `requires_separate_authority=true`

を固定する。

## Agent integration

CLIはupstream `wrapper.py AGENT`、API agentは`wrapper_api.py`。provider-specific integrationはoptional adapter。

skip-permissions / bypass / yolo系launcherを自動選択しない。cloud API secret値をrepository/configへ直接保存しない。

## 成功判定

server/API応答、online status、agent reply、FINAL reply、hash検証など実観測したものだけ成功とする。未観測・推測をPASSとして報告しない。
