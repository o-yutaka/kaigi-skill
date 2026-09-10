# kaigi v2 specification

## Goal

agentchattr を「起動方法やtoken取得を毎回意識せず、`kaigi` だけで使える会議CLI」にする。

## UX contract

- `kaigi` 単体で interactive room を開始する。サーバー停止時は自動起動する。
- `kaigi @agent TEXT` / `kaigi TEXT` は `say` の短縮として扱う。
- 明示操作として `room|join`, `log`, `watch`, `say`, `status|who`, `start`, `stop`, `restart`, `doctor`, `open` を提供する。
- `log/watch/say/room/open` は server unavailable の場合だけ自動起動する。
- `status/doctor` は診断目的なので勝手に起動しない。

## Runtime contract

- 実装は Python 3 標準ライブラリのみ。`jq`, `websockets`, `websocket-client` は不要。
- server URL: `AGENTCHATTR_SERVER` または `http://127.0.0.1:8300`。
- messages: `GET /api/messages`。`limit` / `since_id` を使用。
- send: `POST /api/send` body `{"text":"...","channel":"..."}`。
- session auth: `X-Session-Token`。token は `KAIGI_TOKEN` / `AGENTCHATTR_TOKEN` / server log の `Session token:` の順で取得。
- agent auth: `KAIGI_BEARER_TOKEN` / `AGENTCHATTR_AGENT_TOKEN` があれば `Authorization: Bearer` を優先。
- agentchattr home: `AGENTCHATTR_HOME` または `~/agentchattr`。
- venv: `.venv/bin/python` を優先し、旧 `venv/bin/python` も互換対応。
- start: `<venv>/python run.py` を detached 起動。wrapper は `KAIGI_WRAPPER`（既定 `lmstudio`）を必要に応じて起動。
- stop: kaigi v2 が PID file に記録したプロセスだけ停止する。外部起動プロセスを推測で kill しない。

## Output contract

- message: `HH:MM sender: text`。TTY時のみ sender 色分け。
- multiline text を壊さず表示する。
- timestamp は UNIX sec / msec / ISO8601 を許容する。
- API response は list を標準とし、`messages|data|items` wrapper も後方互換で受ける。
- `status recent` は presence ではなく recent sender と明示する。

## Compatibility

- Linux / WSL を主対象。
- `install.sh` は CLI symlink と SKILL.md を Claude / Hermes / Codex / OpenCode のglobal skill locationへ冪等配置する。
