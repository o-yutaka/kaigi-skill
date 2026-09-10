# kaigi v7

agentchattr のマルチAI会議を、`能力推定/選定 → AI準備 → 独立案 → 反証 → 最終統合 → 永続run → 再開 → proof packet → advisory handoff` まで1本で扱うterminal CLI。

**特定AI providerへの依存はありません。** Claude / Codex / ChatGPT / Hermes / ローカルモデル等はすべてoptional adapterです。provider名はidentityとして扱い、safe-autoの優先順位には使いません。

## 最短

```bash
cd ~/kaigi-skill
git pull
bash install.sh
kaigi "この設計を本番採用すべきか"
```

`@agent` で始まらないbare textは会議議題です。v7では議題から必要そうなcapabilityをsoft推定し、登録済み能力・コスト・online状態・speed・実測response reliabilityからcastを組みます。

```bash
kaigi "このPRのセキュリティと実装をレビュー"
kaigi @agent-a この差分だけ見て
kaigi say "general channelへ単発送信"
```

## Capability Registry

登録先は既定 `~/.config/kaigi/capabilities.json`。`KAIGI_CONFIG_DIR` / `KAIGI_CAPABILITY_REGISTRY` で変更できます。

```bash
kaigi caps
kaigi caps show local-a --json
kaigi caps set local-a coding research deep-reasoning --cost local --speed fast --context 24576
kaigi caps set reviewer red-team research --cost free --speed normal
kaigi caps infer "このPRの安全性を調査して実装修正"
kaigi caps unset local-a
```

標準capability例:

- `coding`
- `research`
- `red-team`
- `vision`
- `long-context`
- `local-free`
- `fast`
- `deep-reasoning`

任意の拡張capability名も登録できます。agent名から能力を推測しません。registry / agent config / runtime factだけを使います。

## Castだけ確認

会議・起動・送信をせずcastだけ計算:

```bash
kaigi cast "このPRを本番投入していいか"
kaigi cast "このPRをレビュー" --need coding,red-team --max-agents 3
kaigi cast "調査" --prefer research,long-context --free-only
kaigi cast "画像UIを確認" --need vision --json
```

選定順序は概念的に:

```text
required capability coverage
→ inferred/preferred capability coverage
→ cost (free/local優先)
→ already online
→ speed
→ observed response reliability
→ deterministic tie-break
```

選ばれたagentはCouncilの `planner / red-team / implementer / evidence / ux / long-horizon` roleに対する能力適合が高くなるよう並べ替えます。

## Hard / soft capability

議題からの自動推定はsoftです。登録が足りなくても、それだけで会議を拒否しません。

一方 `--need` はhard requirementです。

```bash
kaigi decide "この変更を監査" --need coding,red-team
```

指定capabilityをcast全体でcoverできなければfail-closedします。明示的に妥協する場合だけ:

```bash
kaigi decide "この変更を監査" --need vision --best-effort-capabilities
```

コスト制約:

```bash
kaigi decide "実装案" --need coding --free-only
```

`--free-only` はregistry上 `cost=free` または local API由来の `cost=local` のagentだけを候補にします。

## Provider-neutral safe-auto

既定:

- local API: 自動候補、自動wake可
- CLI agent: 自動候補、offlineならupstream通常`wrapper.py`で起動可
- cloud API: 暗黙候補から除外
- configで分類不能なonline agent: 既定除外
- system/user/bot: 除外
- provider名による優先順位: なし
- synthesizer: balanced（最近FINAL担当していない参加者を優先）

```bash
kaigi policy --json
kaigi decide "議題" --dry-run
kaigi decide "議題" --allow-cloud
kaigi decide "議題" --agents agent-a,agent-b
kaigi decide "議題" --synth agent-b
```

cloud APIは `--allow-cloud` や明示 `--agents` 等、ユーザーが明示した場合だけ準備対象にできます。

## Council

1. ROUND 1 — 全参加者を同時triggerし独立分析。
2. ROUND 2 — 実応答者だけを同時triggerし、反証・危険な前提・見落とし・修正版を出す。
3. FINAL — synthesizerが全ログを比較し `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` へ統合。

FINAL replyを実観測した場合だけcomplete。各outbound messageは `RUN=<run_id>` とmessage IDで再照合できます。

v7 safe-autoで作ったrunには `capability_plan` も保存します。そこにはrequired/preferred/inferred capability、実際のcast、各agent profile、coverage、registry SHA256、selection policyをsnapshotします。

## Proof packet

complete Councilは `~/.local/state/kaigi/packets/<run_id>.json` にDecision packet化されます。

```bash
kaigi packet
kaigi verify
kaigi verify RUN_ID --live
```

packetはrun/topic/participants/roles/synth、ROUND1/ROUND2/FINAL message IDs、evidence transcript、final decision、`transcript_sha256`、`packet_sha256`を束縛します。v7 capability runでは `capability_plan` もpacket hashの対象です。

`--live` はagentchattrから同じmessage IDsを再取得してtranscript hashまで再照合します。

## Recovery

```bash
kaigi reconcile [RUN_ID]   # read-only再照合。新規送信なし
kaigi resume [RUN_ID]      # 不足stageだけ続行
kaigi recover [RUN_ID]     # reconcile→resume→completeならpacketまで
```

既存ROUND2/FINAL markerを二重送信しません。

## Advisory handoff

```bash
kaigi handoff RUN_ID --stdout
```

`kaigi.handoff.v1` はDecision packet hashへ束縛されます。capability planが存在すればhandoffにも含まれます。ただし権限は常に:

```text
authority.classification = advisory
execution_authorized = false
requires_separate_authority = true
```

会議の結論と実行権限は別です。

## CLI/API agents

```bash
kaigi launch agent-a agent-b
kaigi launch --all
kaigi wake
kaigi api add local-a --base-url http://127.0.0.1:8080/v1 --model my-model
kaigi api list
```

CLIはupstream `wrapper.py AGENT`、API agentはupstream `wrapper_api.py` を使います。skip-permissions / bypass / yolo系launcherを自動選択しません。

OpenAI-compatible cloud endpointもoptional adapterとして追加できますが、secret値は保存せず環境変数名だけ保持します。

## Install

```bash
bash install.sh
```

canonical skillは `~/.local/share/kaigi/skills/kaigi/` に常時配置。provider/tool固有skill locationはauto-detectされた場合だけoptional adapterとして配置します。

```bash
KAIGI_SKILL_TARGETS=none bash install.sh
KAIGI_SKILL_TARGETS=codex,opencode bash install.sh
KAIGI_SKILL_TARGETS=all bash install.sh
```

主な環境変数: `AGENTCHATTR_HOME`, `AGENTCHATTR_SERVER`, `KAIGI_STATE_DIR`, `KAIGI_CONFIG_DIR`, `KAIGI_CAPABILITY_REGISTRY`, `KAIGI_AUTO_MAX_AGENTS`, `KAIGI_AUTO_MIN_AGENTS`, `KAIGI_SYNTH_POLICY`。Python 3.11+ 推奨。
