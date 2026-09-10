---
name: kaigi
description: agentchattr のマルチAI会議を操作する。安全な自動参加者選定、AI起動、独立分析/反論/統合、未完了run回復、Decision proof、runtime provenance、verified DecisionのTO DO queue、Claude/Codex/ChatGPT/ローカルAPIモデル等への問い合わせが必要な時に使う。
---
# kaigi

## 通常フロー

複数AIで判断する時は原則:

```bash
kaigi "議題"
```

safe-autoで参加者を分類/準備し、Councilを完走し、実FINAL replyが観測できた場合だけcomplete + proof packetを作る。個別送信は`kaigi @NAME 本文`または`kaigi say`。

## Safe-auto governance

既定:
- local API: 候補、自動wake可
- 設定済みCLI: 候補、offlineならupstream通常`wrapper.py`で起動可
- cloud API: 除外
- 分類不能online agent: 除外
- system/user/bot: 除外

cloudは`--allow-cloud`または明示`--agents`時だけ。unknownは`--allow-unknown`時だけ。`--dry-run`ではlaunch/wake/send/run creationを行わない。

## Council

ROUND1 independent fan-out → ROUND2 dissent/review → FINAL synthesis。FINAL reply実観測のみcomplete。各outboundに`RUN=<run_id>`を持たせる。

## Recovery

- `kaigi reconcile [RUN]`: chat read-only再照合。outbound zero。
- `kaigi resume [RUN]`: 既存markerを再送せず不足stageのみ続行。
- `kaigi recover [RUN]`: reconcile→resume→completeならproof生成。

## Proof / provenance

- `kaigi packet [RUN]`
- `kaigi verify [RUN] --live --current-runtime`
- `kaigi handoff [RUN]`
- `kaigi audit`

Decision packetは実message IDs、normalized transcript、`transcript_sha256`、`packet_sha256`に加え、v7では`decision.sections`とruntime file SHA256/size + `runtime_sha256`を保持する。

safe-autoはそのinvocationが作成したrun IDを直接捕捉してpacketへ束縛し、別processが`latest.json`を更新しても別runを採用しない。

## Queue bridge

verified Decisionをupstream agentchattr JobsへTO DOとして渡す時:

```bash
kaigi queue [RUN_ID]
kaigi queue [RUN_ID] --assignee claude
kaigi jobs --status open
```

`queue`は既定でlive transcriptまでverifyする。同一`run_id + packet_sha256`のJobが既にあれば重複作成しない。

upstream内部statusは`open=TO DO`, `done=ACTIVE`, `archived=CLOSED`。`POST /api/jobs`はStore defaultにより`done`になるため、kaigiは直後に`PATCH status=open`を必須実行する。PATCH失敗時は今作ったJobをpermanent deleteでrollbackし、誤ACTIVEを残さない。

Job body先頭へrun/packet hashと`execution_authorized=false`を保存する。Job threadへ@mentionを自動送信しない。

## Authority boundary

会議結果、proof、handoff、TO DO登録をAuthority/Executionへ自動昇格しない。

- `classification=advisory`
- `execution_authorized=false`
- `requires_separate_authority=true`

## Launch/API

CLIはupstream `wrapper.py AGENT`。skip-permissions/bypass/yolo系を自動利用しない。APIはupstream `wrapper_api.py`。local APIのみ暗黙wake可、cloudは明示時だけ。secret値は保存せず環境変数名だけ保持。

ChatGPT bridgeはWeb/App/Plus session流用ではなくOpenAI-compatible API経路。

## 成功判定

API応答、online status、agent reply、FINAL reply、hash/live/runtime検証など実観測済みのみ成功として扱う。未観測を推測でPASSにしない。
