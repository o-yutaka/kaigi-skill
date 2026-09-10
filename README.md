# kaigi v7

agentchattr のマルチAI会議を、`安全な参加者選定 → AI起動 → 独立分析 → 反証 → 最終統合 → 永続run → recovery → proof → TO DO queue` までterminalから一貫して扱うCLI。

## 最短

```bash
cd ~/kaigi-skill
git pull
bash install.sh
kaigi "この設計を本番採用すべきか"
```

`@agent`で始まらないbare textはsafe-auto Council。個別送信は明示します。

```bash
kaigi @claude この差分だけ見て
kaigi say "generalへ単发送信"
```

## Safe-auto

```bash
kaigi "BLACKの次に実装すべきものを決める"
kaigi decide "議題" --dry-run
kaigi decide "議題" --allow-cloud
kaigi decide "議題" --agents claude,codex,chatgpt
```

既定はlocal API/設定済みCLIを候補にし、cloud APIと分類不能online agentは暗黙参加させません。`--allow-cloud` / `--allow-unknown` または明示`--agents`で解除できます。既定`max_agents=4`, `min_agents=2`。

CouncilはROUND1 independent → ROUND2 dissent → FINAL synthesis。FINAL replyを実観測した場合だけcompleteです。

## Proof v7

complete CouncilはDecision packetへ束縛されます。

```bash
kaigi packet [RUN_ID]
kaigi verify [RUN_ID]
kaigi verify [RUN_ID] --live --current-runtime
```

packetはrun/topic/participants/roles/synth、ROUND1/ROUND2/FINALの実message IDs、evidence transcript、final decision、`transcript_sha256`, `packet_sha256`を保持します。

v7はさらに以下を追加します。

- `decision.sections`: `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` をmachine-readableに分離
- `provenance.kaigi_version`
- `provenance.files`: `kaigi`, `kaigi_core.py`, `kaigi_ops.py`, `kaigi_v6.py`, `kaigi_v7.py` のSHA256/size
- `provenance.runtime_sha256`: runtime一式のcanonical fingerprint

`--current-runtime`は、packet作成時runtimeと現在のruntime fingerprintが同一かまで検証します。

v7のsafe-autoは、そのinvocationが実際に作成したrun IDを内部捕捉してproofへ束縛します。別kaigi processが同時にrunを作って`latest.json`が動いても、別runをpacket化しない設計です。

## Verified Decision → TO DO

上流agentchattrにはJobs機能があります。v7では独自task DBを作らず、その公式REST APIへverified Decisionを繋ぎます。

```bash
kaigi queue
kaigi queue RUN_ID
kaigi queue RUN_ID --assignee claude
kaigi queue RUN_ID --dry-run
kaigi jobs --status open
```

`queue`は既定でpacketをlocal+live検証してから登録します。Job bodyへ以下を先頭固定します。

```text
KAIGI-RUN:<run_id>
KAIGI-PACKET:<packet_sha256>
AUTHORITY: advisory; execution_authorized=false; requires_separate_authority=true
```

同一run + packet hashのJobが既にあれば新規作成せず、そのJobを返します。

### なぜPOST→PATCHなのか

agentchattr upstreamの内部statusは:

```text
open     = TO DO
done     = ACTIVE
archived = CLOSED
```

一方、upstream `POST /api/jobs` が使う`JobStore.create()`のdefaultは`done`です。そこでkaigiは作成直後に`PATCH status=open`を必須実行します。PATCHに失敗した場合は、kaigiが今作ったJobを`DELETE ?permanent=true`でrollbackし、誤ったACTIVE Jobを残しません。

Job作成/更新のcallbackはUI/WebSocketへのbroadcastであり、それ自体でagentへJob messageを投げません。v7はJob threadへ`@agent` messageを自動送信しないため、**queue ≠ execution**です。

## Authority boundary

Decision packet / handoff / queueはいずれも実行権限ではありません。

```text
Evidence / transcript
      ↓
Decision packet
      ↓
Advisory handoff
      ↓
TO DO Job

≠ Authority
≠ Execution
```

`kaigi handoff`も引き続き:

```text
classification=advisory
execution_authorized=false
requires_separate_authority=true
```

を固定します。

## Recovery

```bash
kaigi reconcile [RUN_ID]   # read-only再照合。outboundなし
kaigi resume [RUN_ID]      # 不足stageのみ再開
kaigi recover [RUN_ID]     # reconcile→resume→proof
```

既存ROUND2/FINAL markerは二重送信しません。

## AI起動 / API model

```bash
kaigi launch claude codex
kaigi launch claude --dry-run
kaigi wake
kaigi api add localglm --base-url http://127.0.0.1:8080/v1 --model my-model
kaigi api list
```

CLI agentはupstream `wrapper.py AGENT`、API agentはupstream `wrapper_api.py`を使用。skip-permissions/bypass/yolo launcherは自動利用しません。cloud APIは暗黙wakeしません。

ChatGPT bridgeはWeb/App/Plus session流用ではなくOpenAI API agentです。

```bash
export OPENAI_API_KEY="..."
kaigi chatgpt setup
kaigi chatgpt start
```

## その他

```bash
kaigi result [RUN_ID]
kaigi history 50
kaigi export RUN_ID --format json -o result.json
kaigi audit
kaigi room
kaigi log 30
kaigi watch
kaigi agents
kaigi doctor
kaigi open
```

## Install

`install.sh`は`kaigi`, `kaigi_core.py`, `kaigi_ops.py`, `kaigi_v6.py`, `kaigi_v7.py`, `SKILL.md`の存在を確認し、`~/.local/bin/kaigi`をrepositoryへsymlinkします。SKILLはClaude / Hermes / Codex / OpenCodeへ冪等配置します。

state default: `~/.local/state/kaigi`。Python 3.11+推奨。
