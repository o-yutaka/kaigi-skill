---
name: kaigi
description: agentchattr のマルチAI会議を操作する。安全な自動参加者選定、AI起動、独立分析/反論/統合、未完了runの再照合/再開、Decision proof packet、改ざん検証、非権限handoff、Claude/Codex/ChatGPT/ローカルAPIモデル等への問い合わせが必要な時に使う。
---
# kaigi

`kaigi` CLIでagentchattr会議を操作する。

## v6の通常フロー

複数AIで判断したい時は、原則これだけでよい。

```bash
kaigi "議題"
```

bare textはsafe-auto Councilとして扱う。個別agentへの単発送信は先頭を`@NAME`にするか`kaigi say`を使う。

```bash
kaigi @claude この差分だけ見て
kaigi say "general channelへ送る"
```

## Safe-auto contract

`kaigi "議題"` / `kaigi decide "議題"` は:

1. agentchattr serverを確認/起動。
2. config + `/api/status` から候補を分類。
3. local APIは暗黙候補、自動wake可。
4. CLI agentは候補、offlineなら通常`wrapper.py`で起動可。
5. cloud APIは既定で除外。`--allow-cloud`または明示`--agents`時だけ参加可能。
6. 分類不能online agentは既定で除外。必要なら`--allow-unknown`。
7. participantをonline実観測してからCouncil開始。
8. complete後、Decision proof packetを生成。

選定だけ確認する時は `kaigi decide "議題" --dry-run`。このモードはagent launchも会議message送信もしない。

## Council contract

ROUND1=独立fan-out、ROUND2=実応答者によるdissent/review、FINAL=synthesis。

FINAL replyを実観測した場合だけcomplete。timeout/欠落を成功扱いしない。各Council messageに`RUN=<run_id>`を付与し、message IDと合わせて再照合可能にする。

## Resume / recover

- `kaigi reconcile [RUN_ID]` — chat実績からledgerを副作用なしで再照合。outbound messageを送らない。
- `kaigi resume [RUN_ID]` — 既存markerを再送せず、不足stageだけ進める。
- `kaigi recover [RUN_ID]` — reconcile→resume→completeならproof packet生成まで行う。

同一runのROUND2/FINALが既に存在する場合は二重送信しない。

## Decision proof

complete Councilは `kaigi packet [RUN_ID]` で `kaigi.decision_packet.v1` にする。safe-auto完走時は自動生成。

packetにはrun/topic/participants/roles/synth、ROUND1/ROUND2/FINALの実message IDs、evidence transcript、final decision、`transcript_sha256`、`packet_sha256`を含める。

検証:

```bash
kaigi verify [RUN_ID]
kaigi verify [RUN_ID] --live
```

local verifyはpacket hash / transcript hash / run ledger pointer / final整合を確認。`--live`は同message IDsをagentchattrから再取得してtranscript hashまで再照合する。

## Authority boundary

会議結果はadvisory。Decision packetやhandoffを実行権限として扱わない。

`kaigi handoff [RUN_ID]` は `kaigi.handoff.v1` を生成し、以下を固定する。

- `authority.classification=advisory`
- `execution_authorized=false`
- `requires_separate_authority=true`

Evidence/DecisionとAuthority/Executionを混同しない。

## CLI launch

`kaigi launch claude codex` はupstream `wrapper.py AGENT` を使う。skip-permissions / bypass / yolo系launcherを自動選択しない。`--dry-run`ではprocessを開始しない。

## API agent governance

upstream `wrapper_api.py`を使用。local APIのみ暗黙wake可。cloudは明示指定時のみ。secret値は設定fileへ保存せず環境変数名だけ保持する。

`kaigi api add NAME --base-url URL --model MODEL [--api-key-env ENV]` でOpenAI互換endpointを追加可能。

ChatGPT bridgeは`kaigi chatgpt setup/start/status`。Web/App/Plus session流用ではなくOpenAI-compatible API経路。

## Run / audit

- `kaigi result [RUN_ID]`
- `kaigi history [N]`
- `kaigi export [RUN_ID] --format md|json`
- `kaigi audit`

`KAIGI_STATE_DIR`既定は`~/.local/state/kaigi`。

## 成功判定

server/API応答、online status、agent reply、FINAL reply、hash検証など実観測したものだけ成功とする。未観測・推測をPASSとして報告しない。
