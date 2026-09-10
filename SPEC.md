# kaigi v3 specification

## Goal

agentchattr を `kaigi` だけで操作し、単なるchat clientに留まらず「複数AIを招集して独立分析→異論→統合まで完走する会議入口」にする。

## UX contract

- `kaigi` 単体: interactive room。server unavailable時だけ自動起動。
- `kaigi TEXT` / `kaigi @agent TEXT`: `say` shortcut。
- `kaigi convene TOPIC`: Councilを既定で開始。
- `kaigi agents`: `/api/status` に基づく参加状態。
- `kaigi templates`: upstream Sessions template一覧。
- `status/doctor` は診断目的なので勝手にserverを起動しない。

## Council contract

Councilはkaigi側の3-round orchestrator。

### ROUND 1 — independent

- online agentを既定で全員targetにする。
- 一つのhuman messageへ全targetの明示`@mention`を入れ、agentchattr routerのfan-outで同時triggerする。
- `planner / red-team / implementer / evidence / ux / long-horizon` をround-robin割当。
- 他agentへの@mentionを禁止し、独立案を要求する。
- conclusion/evidence/risk/actionを要求する。

### ROUND 2 — dissent

- ROUND 1で実際に応答したagentのみ再target。
- 同時triggerして相互批判、危険な前提、見落とし、修正版、残存異論を要求。
- 同round中のagent-to-agent @mentionは抑止して意図しないrouting loopを避ける。

### FINAL — synthesis

- `--synth` 明示を優先。
- 未指定は `chatgpt > claude > codex > hermes > first responder` の順で選択。
- `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` を要求。
- 実際のFINAL replyを観測して完了とする。

### Controls

- `--agents A,B,C`: 対象固定。offline指定はfail。
- `--max-agents N`: target上限。0=全員。
- `--quorum N`: roundを進める最低応答数。0=全員。
- `--round-timeout SEC`: 各round待機上限。
- `--kickoff-only`: ROUND 1 triggerだけ行い戻る。

## Native Sessions compatibility

`--template planning|debate|code-review|design-critique` はkaigiで再実装せず、upstreamの `/api/sessions/start` を使用する。

- `cast` 未指定: upstream auto-cast。
- `--agents`: template rolesへround-robin cast。
- `--cast role=agent`: explicit cast。
- HTTP 409（同channelにactive Session）は勝手にfallbackを重ねずfail。
- native Sessions API/templateが無い場合のみ、既定では明示@mention meetingへfallbackできる。

## ChatGPT bridge

- upstream `wrapper_api.py` のOpenAI-compatible API-agent契約を利用。
- `kaigi chatgpt setup` は `AGENTCHATTR_HOME/config.local.toml` に管理blockを冪等upsert。
- `[agents.chatgpt]`: `type="api"`, default base URL `https://api.openai.com/v1`, default model `gpt-5.6`, `api_key_env="OPENAI_API_KEY"`。
- API key値は設定fileやrepositoryへ保存しない。
- server再起動でagent configを再読込後、`kaigi chatgpt start` が `wrapper_api.py chatgpt` を起動。
- ChatGPT Web/App login/sessionを流用する機能とは扱わない。

## Runtime contract

- Python 3 standard libraryのみ。`jq`, `websockets`, `websocket-client` 不要。
- server URL: `AGENTCHATTR_SERVER` or `http://127.0.0.1:8300`。
- messages: `GET /api/messages` (`limit`, `since_id`)。
- send: `POST /api/send` body `{"text":"...","channel":"..."}`。
- status: `GET /api/status`。
- Sessions: `/api/sessions/templates`, `/api/sessions/start`, `/api/sessions/active`。
- session auth: `X-Session-Token`。
- registered agent auth: `Authorization: Bearer`。
- token priority: `KAIGI_BEARER_TOKEN|AGENTCHATTR_AGENT_TOKEN` > `KAIGI_TOKEN|AGENTCHATTR_TOKEN` > server log `Session token:`。
- home: `AGENTCHATTR_HOME` or `~/agentchattr`。
- venv: `.venv/bin/python` preferred, `venv/bin/python` backward-compatible。
- stopはkaigi PID filesで所有確認できたprocessだけを対象とし、外部processを推測killしない。

## Verification contract

GitHub CIは最低限:

```bash
python -m py_compile kaigi
python -m unittest -v tests/test_cli.py
```

Testsはmessage/status/session APIsのmock serverを使い、shortcut、room、native Session cast、Council full 3-round、fallback、ChatGPT config idempotencyを検証する。

## Install compatibility

Linux / WSLを主対象。`install.sh` はCLI symlinkとSKILL.mdをClaude / Hermes / Codex / OpenCodeのglobal skill locationへ冪等配置する。
