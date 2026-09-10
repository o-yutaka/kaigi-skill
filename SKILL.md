---
name: kaigi
description: agentchattr のマルチエージェント会議を操作する。会議確認・発言・監視・招集・複数AIの独立分析/反論/統合、Claude/Codex/ChatGPT等への問い合わせが必要な時に使う。
---
# kaigi

`kaigi` CLI で agentchattr 会議を操作する。

## 優先操作

- `kaigi` — interactive room。server停止時は自動起動。
- `kaigi @NAME 本文` — そのエージェントへ即送信。
- `kaigi convene "議題"` — Council会議を開始。通常の複数AI検討はこれを優先。
- `kaigi agents` — online/status確認。
- `kaigi log 30` — 履歴。
- `kaigi watch` — 継続監視。
- `kaigi doctor` — 接続・配置・認証診断。

## Council

`kaigi convene` の既定モード。online agentを原則全員使う。

1. ROUND 1: 全参加者を同時にtriggerし、独立分析を収集。
2. ROUND 2: 応答者を同時にtriggerし、相互批判・反証・修正を収集。
3. FINAL: synthesizerが全ログを比較し、最終判断へ統合。

役割は `planner`, `red-team`, `implementer`, `evidence`, `ux`, `long-horizon`。ユーザーが対象を指定した場合だけ `--agents claude,codex,chatgpt` を使う。指定がなければonline全員を使う。

必要なら `--quorum N`, `--max-agents N`, `--synth NAME`, `--round-timeout SEC` を使う。

## Native Sessions

上流agentchattrの構造化Sessionを明示的に使う場合:

- `--template planning`
- `--template debate`
- `--template code-review`
- `--template design-critique`
- `kaigi templates` で利用可能template確認

役割固定が必要なら `--cast role=agent` を複数指定する。

## ChatGPT bridge

ChatGPTをAPI agentとして追加する場合:

```bash
kaigi chatgpt setup
```

これは `AGENTCHATTR_HOME/config.local.toml` に `[agents.chatgpt]` を追加し、APIキーそのものは保存しない。実行環境に `OPENAI_API_KEY` が必要。agentchattr再起動後に `kaigi chatgpt start`、状態は `kaigi chatgpt status`。

現在のChatGPT Web/Appセッションを流用したと報告してはいけない。このbridgeはOpenAI-compatible API経路。

## 成功判定

送信/API応答・Session start・実際のagent replyなど観測できたものだけ成功とする。online一覧は `/api/status` を使う。Councilでは応答がないagentを応答済みとして扱わない。

## room内

- `/convene TOPIC`
- `/agents`
- `/to NAME TEXT`
- `/log [N]`
- `/status`
- `/quit`

## 環境変数

- `AGENTCHATTR_SERVER` — default `http://127.0.0.1:8300`
- `AGENTCHATTR_HOME` — default `~/agentchattr`
- `AGENTCHATTR_LOG` — default `/tmp/agentchattr-server.log`
- `KAIGI_CHANNEL` — default `general`
- `KAIGI_WRAPPER` — default `lmstudio`
- `KAIGI_TOKEN` — session token override
- `KAIGI_BEARER_TOKEN` — agent bearer token override
- `KAIGI_CHATGPT_MODEL` — ChatGPT bridge model override
