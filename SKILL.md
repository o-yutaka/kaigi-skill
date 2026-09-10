---
name: kaigi
description: agentchattr のマルチAI会議を操作する。会議確認・発言・監視・招集・複数AIの独立分析/反論/統合・保存済み結論の回収、Claude/Codex/ChatGPT/ローカルAPIモデル等への問い合わせが必要な時に使う。
---
# kaigi

`kaigi` CLI で agentchattr 会議を操作する。

## 優先操作

- `kaigi convene "議題"` — 通常の複数AI検討はこれを優先。Councilを最後まで回し、runを保存する。
- `kaigi result` — 最新会議の確定済み最終結論を回収する。
- `kaigi history` — 過去runを確認し、同じ議題を無駄に再会議しない。
- `kaigi` — interactive room。
- `kaigi @NAME 本文` — 個別AIへ即送信。
- `kaigi agents` — onlineと設定済みagentを確認。
- `kaigi log 30` / `kaigi watch` — 会議ログ確認。
- `kaigi doctor` — 接続・認証・runtime診断。

## Council contract

既定Councilは3段。

1. ROUND 1: 全参加者を同時triggerし、独立分析を収集。
2. ROUND 2: 実際の応答者だけを同時triggerし、相互批判・反証・修正版を収集。
3. FINAL: synthesizerが全ログを比較し `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` を返す。

実際のFINAL replyを観測した場合だけ `complete`。timeout/応答欠落は `waiting` または `failed` としてrunへ残し、完成扱いしない。

役割は `planner`, `red-team`, `implementer`, `evidence`, `ux`, `long-horizon`。必要なら `--agents`, `--quorum`, `--max-agents`, `--synth`, `--round-timeout` を使う。

## Run ledger

各会議は `KAIGI_STATE_DIR`（既定 `~/.local/state/kaigi`）へJSON保存される。

- `kaigi result [RUN_ID]` — 最終結論。
- `kaigi history [N]` — run一覧。
- `kaigi export [RUN_ID] --format md|json` — 成果物化。

以前に同一目的のcomplete runがあり、その結論を再利用できる場合は、不必要に同じ会議を再生成せず既存runを参照する。

## API agent / wake governance

上流agentchattrの `wrapper_api.py` 契約を使う。

- `kaigi wake` — 設定済みlocal API agentだけ起床を試みる。
- Councilの自動wakeもlocal APIだけ。クラウドAPIを勝手に起動しない。
- `--agents chatgpt,...` のようにクラウドagentをユーザーが明示した場合、そのagentの起動を試みてよい。
- `kaigi wake NAME` / `kaigi api start NAME` は明示起動。
- `kaigi api add NAME --base-url URL --model MODEL [--api-key-env ENV]` で任意のOpenAI互換agentを設定。
- API key値は設定fileへ書かず環境変数参照だけを保存する。

## ChatGPT bridge

`kaigi chatgpt setup` は汎用API agent設定のChatGPTショートカット。`OPENAI_API_KEY` を参照し、Web/App/Plusセッションを流用したと報告してはいけない。設定再読込後 `kaigi chatgpt start`、状態は `kaigi chatgpt status`。

## Native Sessions

上流Sessionsを使う場合:

- `--template planning`
- `--template debate`
- `--template code-review`
- `--template design-critique`
- `--cast role=agent` で固定cast

HTTP 409などactive Session競合時に別会議を勝手に重ねない。

## 成功判定

送信API成功、Session start、agent reply、FINAL replyなど実際に観測できたものだけ成功として扱う。`/api/status` のavailableをonline根拠にする。未観測を推測でPASSにしない。

## room内

`/convene TOPIC`, `/result`, `/history`, `/agents`, `/to NAME TEXT`, `/log [N]`, `/status`, `/quit`。

## 環境変数

- `AGENTCHATTR_SERVER` — default `http://127.0.0.1:8300`
- `AGENTCHATTR_HOME` — default `~/agentchattr`
- `AGENTCHATTR_LOG` — default `/tmp/agentchattr-server.log`
- `KAIGI_CHANNEL` — default `general`
- `KAIGI_STATE_DIR` — default `~/.local/state/kaigi`
- `KAIGI_TOKEN` / `KAIGI_BEARER_TOKEN` — auth override
- `KAIGI_CHATGPT_MODEL` — ChatGPT bridge model override
