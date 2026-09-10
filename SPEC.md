# kaigi v4 specification

## Goal

agentchattr を `kaigi` だけで操作し、`招集 → 並列独立分析 → 異論 → 統合 → 永続run → 後から結果回収` まで一貫させる。上流のrouting / Sessions / API-agent契約は再実装せず利用する。

## UX contract

- `kaigi` — interactive room。server unavailable時のみ自動起動。
- `kaigi TEXT` / `kaigi @agent TEXT` — say shortcut。
- `kaigi convene TOPIC` — Councilを既定で開始。
- `kaigi result [RUN_ID]` — 保存済み最終結論。
- `kaigi history [N]` — run履歴。
- `kaigi export [RUN_ID]` — Markdown/JSON出力。
- `kaigi agents` — onlineと設定済みagentを統合表示。
- `kaigi wake` / `kaigi api ...` — API agent管理。
- `status/doctor` は診断目的なので勝手にserverを起動しない。

## Council contract

### ROUND 1 — independent fan-out

- online agentを既定targetにする。
- Council前にlocal API agentだけ自動wakeを試みる。
- 1 human messageへ全targetの明示`@mention`を入れ、agentchattr routerのfan-outで同時trigger。
- `planner / red-team / implementer / evidence / ux / long-horizon` をround-robin割当。
- 他agentへの@mentionを禁止し、独立した conclusion/evidence/risk/action を要求。

### ROUND 2 — dissent fan-out

- ROUND 1で実際に応答したagentのみ再target。
- 同時triggerし、反証・危険な前提・見落とし・修正版・残存異論を要求。
- 同round中のagent-to-agent @mentionは抑止する。

### FINAL — synthesis

- `--synth` 明示を最優先。
- 未指定は `chatgpt > claude > codex > hermes > first responder`。
- `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` を要求。
- FINAL replyを実際に観測して初めてrunを `complete` にする。

### Controls

- `--agents A,B,C` — 対象固定。設定済みAPI agentなら明示起動を試みる。起動後もofflineならfail。
- `--max-agents N` — target上限。0=全員。
- `--quorum N` — round進行最低応答数。0=全target。
- `--round-timeout SEC` — 各round待機上限。
- `--kickoff-only` — ROUND1 trigger後、runをdetachedで保存して戻る。
- `--no-wake` — local API auto-wake無効。
- `--wake-timeout SEC` — wake後online待機上限。

## Run ledger contract

保存先: `KAIGI_STATE_DIR` または `~/.local/state/kaigi`。

- `runs/<run_id>.json` — canonical run record。
- `latest.json` — 最新run pointer。
- run_id: timestamp + random suffix。
- atomic temp-write + replaceで保存。
- minimum fields: schema_version, run_id, kind, state, stage, topic, channel, server, started_at, updated_at。
- Councilは participants, roles, synth, round1, round2, final request/reply IDs, final_text を可能な範囲で保存。
- timeoutは `waiting`、明示detachmentは `detached`、未処理例外は `failed`。未完了を `complete` にしない。
- `result --json` / `history --json` で機械可読に再利用可能。

## Native Sessions compatibility

`--template planning|debate|code-review|design-critique` はupstream `/api/sessions/start` を使用。

- cast未指定: upstream auto-cast。
- `--agents`: template rolesへround-robin cast。
- `--cast role=agent`: explicit cast。
- HTTP 409 active-session conflictはfallbackせずfail。
- Sessions API/template欠落時のみ明示@mention fallbackを許容。
- native Session runもrun ledgerへ開始・detached/follow結果を保存する。

## API agent contract

upstream `wrapper_api.py` のOpenAI-compatible API agent契約を使う。

### Configuration

`kaigi api add NAME --base-url URL --model MODEL [--label LABEL] [--api-key-env ENV]`

- `AGENTCHATTR_HOME/config.local.toml` に `# BEGIN KAIGI API NAME` 管理blockを冪等upsert。
- secret値は書かず、`api_key_env` 名だけ保存。
- `config.toml` + `config.local.toml` をPython標準`tomllib`で読み、local overlayを優先merge。

### Wake governance

- local endpoint判定: hostnameが `127.0.0.1`, `localhost`, `::1`, `0.0.0.0`。
- `kaigi wake` と通常Council自動wakeはlocal APIだけ。
- cloud APIは暗黙に起動しない。
- explicit `kaigi wake NAME`, `kaigi api start NAME`, またはCouncil `--agents` に明示されたAPI agentはcloudでも起動意思ありと扱う。
- `--all-api` / `api start --all` でcloudまで含めるには `--cloud` を要求。
- 複数wrapper起動は独立ならThreadPoolExecutorで並列化する。
- readinessはbase_urlと、指定されたapi_key_envの実環境値を確認する。

## ChatGPT bridge

- generic API agentのshortcut。
- default base URL `https://api.openai.com/v1`。
- default model `gpt-5.6`（`KAIGI_CHATGPT_MODEL` override）。
- `api_key_env="OPENAI_API_KEY"`。
- ChatGPT Web/App/Plus login/session流用ではない。

## Process ownership

- server PID: `<state>/server.pid`。
- API wrapper PID: `<state>/wrappers/<agent>.pid`。
- stopは記録PIDのcmdlineを確認し、自分が所有確認できるprocessだけ停止する。
- 外部起動processを名前推測だけでkillしない。

## Runtime contract

- Python 3 standard library。API config解析はPython 3.11+の`tomllib`を利用。
- messages: `GET /api/messages` (`limit`, `since_id`, `channel`)。
- send: `POST /api/send` body `{"text":"...","channel":"..."}`。
- status: `GET /api/status`。
- Sessions: `/api/sessions/templates`, `/api/sessions/start`, `/api/sessions/active`。
- auth: agent bearerを優先し、session token override、最後にserver log token。

## Verification contract

CI minimum:

```bash
python -m py_compile kaigi kaigi_core.py
python -m unittest -v tests/test_cli.py
bash -n install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
HOME="$RUNNER_TEMP/kaigi-home" bash install.sh
```

Regression suiteは shortcut/room/status/agents、native Session cast、Council 3-round完走、run result/history/export、generic API config idempotency、ChatGPT config idempotencyを検証する。installerは隔離HOMEで2回実行し冪等性を確認する。

## Install compatibility

Linux / WSLを主対象。`install.sh` は `kaigi` と `kaigi_core.py` の存在を事前確認し、CLI symlinkとSKILL.mdをClaude / Hermes / Codex / OpenCodeのglobal skill locationへ冪等配置する。
