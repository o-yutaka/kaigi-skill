# kaigi v5 specification

## Goal

agentchattrを `AI起動 → 招集 → 並列独立分析 → 異論 → 統合 → 永続run → 再照合/再開 → 結果回収` まで一貫して扱う。上流routing / Sessions / `wrapper.py` / `wrapper_api.py` を尊重し、同機能を再実装しない。

## Core UX

- `kaigi` — interactive room。
- `kaigi TEXT` / `kaigi @agent TEXT` — say shortcut。
- `kaigi convene TOPIC` — online agentでCouncil。
- `kaigi go TOPIC --agents A,B,...` — 明示agentを準備してonline確認後Council。
- `kaigi launch A B` — CLI agentを通常wrapperで対話terminalへ起動。
- `kaigi reconcile [RUN]` — 保存runとchat実績を副作用なしで再照合。
- `kaigi resume [RUN]` — 未完了Councilを不足stageから再開。
- `kaigi result/history/export` — run ledger利用。

## CLI launch contract

CLI agent起動はupstream `wrapper.py AGENT` を使用する。

- config上 `type="api"` ではなく `command` を持つagentが対象。
- agent CLI commandがPATH上に存在することを確認。
- terminal priority: existing tmux → WSL Windows Terminal → gnome-terminal → x-terminal-emulator → xterm → macOS Terminal。
- `--here` は現在terminalで1 agentのみ。
- `--dry-run` は実processを開始しない。
- skip-permissions / bypass / yolo等のlauncherは自動利用しない。
- 起動後は `/api/status` availableを観測してonline判定する。

## `go` contract

- `--agents` 明示時、API agentは既存API wake機構、CLI agentはlaunch機構を使用。
- 指定agent全員がonlineにならなければCouncil開始を拒否。
- `--agents` 未指定時は通常 `convene` と同じく既存online + local API auto-wakeを使う。
- `--quorum`, `--synth`, `--round-timeout` をCouncilへ引き継ぐ。

## Council contract

ROUND1=independent fan-out、ROUND2=dissent fan-out、FINAL=synthesis。FINAL replyを実観測してのみcomplete。

各outbound Council markerに `RUN=<run_id>` を含め、ledgerのmessage IDと組み合わせて再照合可能にする。

## Run ledger

`KAIGI_STATE_DIR`（default `~/.local/state/kaigi`）配下。

- `runs/<run_id>.json`
- `latest.json`
- atomic write
- states: running / detached / waiting / failed / complete
- stages: created / round1 / round1_timeout / round2 / round2_timeout / final / final_timeout / complete 等

Council recordはparticipants, roles, synth, kickoff_message_id, round1, round2_message_id, round2, final_request_message_id, final_message_id, final_textを可能な限り保持する。

## Reconcile contract

`reconcile` は外部chatへ新規messageを送信しない。

1. kickoff_message_id以後の同channel chatを読む。
2. `RUN=<id>` + ROUND2/FINAL markerを持つuser messageを検出し、ledger欠落message IDを復元。
3. marker間のparticipant返信をROUND1/ROUND2として再収集。
4. FINAL request後のsynth replyが観測できればcompleteへ更新。
5. 既にcompleteなら変更不要。

Council以外のrunは現時点でresume/reconcile対象外とし、対応したと誤報しない。

## Resume contract

`resume` は必ずreconcile相当処理を先に実行する。

- complete → outboundなしでfinalを返す。
- ROUND1送信済み → 既存replyを利用し、quorum不足分のみ待つ。quorum成立後、ROUND2 markerが無い場合だけ送信。
- ROUND2送信済み → 既存replyを利用。quorum成立後、FINAL markerが無い場合だけ送信。
- FINAL送信済み → 再送せずsynth replyだけ待つ。
- timeout時はwaitingとして保存し、同stageを再送しない。

## Native Sessions

`planning|debate|code-review|design-critique` はupstream Sessions API。HTTP 409競合時は別fallback会議を重ねない。Council以外のrun resumeは未サポート。

## API agent / wake governance

upstream `wrapper_api.py`を使う。local APIのみ暗黙auto-wake可。cloudは明示指定時のみ。secret値は設定fileへ保存しない。

## ChatGPT

汎用API agent shortcut。OpenAI API経路でありWeb/App/Plus login流用ではない。

## Process ownership

server/API wrapperは記録PID + cmdline確認後のみ停止。CLI agent terminalはupstream wrapperの対話プロセスとして扱い、kaigi stopで推測killしない。

## Verification

CI:

```bash
python -m py_compile kaigi kaigi_core.py kaigi_ops.py
python -m unittest discover -v tests -p 'test_*.py'
bash -n install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
```

Regression minimum:
- v4 core 7 tests
- reconcile late FINAL: outbound zero、ledger complete
- resume from stored ROUND1: ROUND1再送なし、ROUND2/FINAL各1回、complete
- launch `--here --dry-run`: process未起動
- public version 5.0.0
- installer idempotency / 4 skill locations

## Install

`kaigi`, `kaigi_core.py`, `kaigi_ops.py`, `SKILL.md` を同repositoryに保持する。installerは3 runtime filesの存在を確認してからsymlink/skill配布する。
