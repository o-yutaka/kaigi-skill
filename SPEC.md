# kaigi v6 specification

## Goal

agentchattrを `safe agent selection → agent wake/launch → parallel independent analysis → dissent → synthesis → durable run → recovery → proof packet → advisory handoff` まで一貫して扱う。upstream routing / Sessions / `wrapper.py` / `wrapper_api.py` を尊重し、同機能を再実装しない。

## Public UX

- `kaigi "TOPIC"` — v6 safe-auto Council。complete後proof packetを自動生成。
- `kaigi @agent TEXT` — direct say shortcut。bare meeting UXへ変更してもこの互換を維持。
- `kaigi say TEXT` — 明示単発送信。
- `kaigi decide TOPIC` — safe-autoの明示形。制御option用。
- `kaigi convene TOPIC` — lower-level Council/native Sessions入口。
- `kaigi launch A B` — CLI agentをupstream通常wrapperで起動。
- `kaigi go TOPIC --agents A,B` — 明示agent準備→Council。
- `kaigi reconcile/resume/recover` — 未完了Council回復。
- `kaigi packet/verify/handoff/audit` — proof surface。

## Safe-auto selection contract

候補は`load_agents_config()`と`/api/status`のunionから作る。

既定policy:

- `type="api"`かつloopback host (`127.0.0.1`, `localhost`, `::1`, `0.0.0.0`) = local API。暗黙候補、自動wake可。
- `type="api"`かつ非loopback = cloud API。暗黙候補から除外。
- `command`を持つ非API agent = CLI。暗黙候補、offlineなら通常`wrapper.py`起動可。
- configで分類不能なonline participant = 既定除外。
- `user`, `system`, `telegram-bot` = 除外。
- `--allow-cloud`時だけcloud APIをsafe-auto候補へ入れてよい。
- `--allow-unknown`時だけ分類不能online participantを候補へ入れてよい。
- `--agents`明示指定は対象agentについて明示同意とみなしcloud API準備も可。ただしonline実観測できない対象があれば開始拒否。
- default `max_agents=4`, `min_agents=2`。環境変数`KAIGI_AUTO_MAX_AGENTS`, `KAIGI_AUTO_MIN_AGENTS`で既定変更可。
- `--dry-run`はlaunch/wake/send/run creationを行わない。

## Council contract

ROUND1=independent fan-out、ROUND2=dissent/review fan-out、FINAL=synthesis。FINAL replyを実観測した場合だけcomplete。

各outbound Council markerは`RUN=<run_id>`を含む。run ledgerはparticipants, roles, synth, outbound message IDs, round replies, final replyを可能な限り保持する。

## Run persistence / recovery

`KAIGI_STATE_DIR` default `~/.local/state/kaigi`。

- `runs/<run_id>.json`
- `latest.json`
- atomic write
- `reconcile`はchat read-only。outbound zero。
- `resume`はreconcile相当を先行し、存在するROUND2/FINAL markerを再送しない。
- `recover`はreconcile→resumeをまとめ、complete時にpacket未生成なら生成する。

Council以外のrunはresume/reconcile/recover対象外。対応したと誤報しない。

## Decision packet contract

Path: `packets/<run_id>.json`
Schema: `kaigi.decision_packet.v1`

complete Councilのみ生成可能。packetは少なくとも:

- run: run_id, kind, topic, channel, server, state, started/completed times, participants, roles, synth
- decision: final sender, final message id, final text
- round1 / round2 records
- kickoff / round2 prompt / final prompt / final reply IDs
- evidence_ids
- normalized transcript (`id`, `sender`, `text`, `channel`)
- `transcript_sha256`
- `packet_sha256`
- authority classification

を持つ。

### Canonical hash

hash inputはUTF-8 JSON、`ensure_ascii=false`, `sort_keys=true`, separators `(',', ':')`。SHA-256。

`transcript_sha256 = SHA256(canonical(transcript))`。
`packet_sha256 = SHA256(canonical(packet_without_packet_sha256))`。

`generated_at`はpacket hash対象。run ledgerへpacket pointer/hashを書き戻す処理はpacket hash生成後でよい。

### Evidence selection

transcriptは任意の会話全体ではなく、run ledgerに束縛されたevidence message IDsのみをmessage ID昇順で取得する。

IDs:

- kickoff message
- recorded ROUND1 replies
- ROUND2 prompt
- recorded ROUND2 replies
- FINAL prompt
- FINAL reply

要求IDがagentchattrから取得できない場合、proof packet生成を成功扱いしない。

## Verify contract

`kaigi verify RUN` local mode:

1. schema確認
2. packet_sha256再計算
3. transcript_sha256再計算
4. run ledger final_textとの一致
5. ledger packet pointerが存在する場合はpacket hash一致

`--live`では上記が通った後、packetのevidence_idsをagentchattrから同channelで再取得し、normalized live transcript hashがpacketの`transcript_sha256`と一致することを確認する。

どれか不一致ならexit non-zero。改ざん/欠落を推測補完しない。

## Handoff contract

Path: `handoffs/<run_id>.json`
Schema: `kaigi.handoff.v1`

Decision packetをlocal verifyしてから生成し、source run ID / packet SHA / transcript SHA / topic / participants / synth / decisionを含む。

authorityは固定:

- `classification="advisory"`
- `execution_authorized=false`
- `requires_separate_authority=true`

handoff自体もcanonical JSONの`handoff_sha256`を持つ。会議/Decision/EvidenceをAuthority/Executionへ自動昇格しない。

## Agent launch contract

CLI agentはupstream `wrapper.py AGENT`。skip-permissions/bypass/yolo launcherを自動利用しない。terminal priorityはv5を維持。API agentはupstream `wrapper_api.py`。

## Cloud governance

cloud API agentを暗黙wake/参加させない。明示`--allow-cloud`、明示`--agents`、`kaigi wake NAME`、`kaigi api start NAME`のようなユーザー指定時だけ対象にできる。API secret値はrepository/configへ保存せず、環境変数名だけ保持する。

## Compatibility

v5の`launch`, `go`, `reconcile`, `resume`、v4 core surface、native Sessions、interactive roomを保持する。破壊的変更はbare non-command textの意味のみで、v6ではsay shortcutからsafe-auto meeting topicへ変更する。direct `@NAME` shortcutは維持する。

## Verification gate

GitHub CI最低条件:

```bash
python -m py_compile kaigi kaigi_core.py kaigi_ops.py kaigi_v6.py
python -m unittest discover -v tests -p 'test_*.py'
bash -n install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
```

Regression must cover:

- existing core/ops regressions
- public version 6.0.0
- bare topic→safe-auto Council→packet
- cloud API excluded by default
- `--allow-cloud` inclusion
- `@agent` direct-message compatibility
- dry-run outbound zero / no run creation
- packet tamper detection
- live source transcript tamper detection
- handoff `execution_authorized=false`
- audit proof PASS
- installer idempotency + 4 skill locations

## Install

`kaigi`, `kaigi_core.py`, `kaigi_ops.py`, `kaigi_v6.py`, `SKILL.md`を同repositoryに保持する。installerは4 runtime filesの存在を確認してからCLI symlink/skill配布する。
