# kaigi

agentchattr のマルチAI会議を、ターミナルから最短で使うためのCLI。

## 最短

```bash
bash ~/kaigi-skill/install.sh
kaigi
```

`kaigi` だけで会議室に入ります。agentchattr が止まっていれば自動起動します。

```bash
kaigi @claude これ見て
kaigi log 30
kaigi watch
kaigi agents
kaigi doctor
kaigi open
```

## 1コマンドAI会議

```bash
kaigi convene "この設計を本番採用すべきか"
```

既定は `council`。オンラインのエージェントを可能な限り全員参加させ、3段で進めます。

1. ROUND 1: 各AIを同時に @mention し、他者に引っ張られない独立案を並列収集
2. ROUND 2: 応答したAIを同時に再招集し、相互批判・反証・修正版を収集
3. FINAL: 1エージェントが全ログを比較し `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` に統合

役割は `planner / red-team / implementer / evidence / ux / long-horizon` を参加人数に応じて割り当てます。

```bash
kaigi convene "議題" --agents claude,codex,chatgpt
kaigi convene "議題" --quorum 2
kaigi convene "議題" --synth chatgpt
kaigi convene "議題" --max-agents 4
kaigi convene "議題" --round-timeout 90
```

`--quorum 0`（既定）は全員待ち、`--max-agents 0`（既定）はオンライン全員です。

## agentchattr native Sessions

上流 agentchattr の Sessions API もそのまま使えます。

```bash
kaigi templates
kaigi convene "実装計画を作る" --template planning
kaigi convene "A案とB案を比較" --template debate
kaigi convene "この変更をレビュー" --template code-review
kaigi convene "UIを批評" --template design-critique
```

明示castも可能です。

```bash
kaigi convene "計画" --template planning \
  --cast planner=claude \
  --cast challenger=codex \
  --cast synthesiser=chatgpt
```

## ChatGPTを会議へ参加させる

agentchattr の OpenAI-compatible API agent 機構を使います。現在のChatGPTアプリ/Webログインを流用する方式ではなく、`OPENAI_API_KEY` を環境変数として使います。キー自体は `config.local.toml` に保存しません。

```bash
export OPENAI_API_KEY="..."
kaigi chatgpt setup
```

これで `~/agentchattr/config.local.toml` に `[agents.chatgpt]` を冪等追加します。agentchattr を再起動して設定を読み直した後:

```bash
kaigi chatgpt start
kaigi chatgpt status
kaigi agents
```

モデルは変更できます。

```bash
kaigi chatgpt setup --model gpt-5.6
```

以後は普通の参加者として使えます。

```bash
kaigi @chatgpt この案を反証して
kaigi convene "最終設計を決める" --agents claude,codex,chatgpt
```

`kaigi chat ...` は `kaigi chatgpt ...` の短縮です。

## interactive room

`kaigi` で入室後は通常文をEnterするだけで送信します。

```text
/convene TOPIC       council会議を開始
/agents              エージェント一覧
/to claude 本文      @claudeへ送信
/log 50              履歴
/status              状態
/quit                終了
```

## 自動認識されるAIツール

`install.sh` は同じ `SKILL.md` を Claude、Hermes、Codex、OpenCode のglobal skill locationへ冪等配置します。

## 設定

通常は不要です。環境が違う場合だけ上書きします。

```bash
export AGENTCHATTR_HOME=~/agentchattr
export AGENTCHATTR_SERVER=http://127.0.0.1:8300
export KAIGI_WRAPPER=lmstudio
export KAIGI_CHANNEL=general
```

認証は server log の `Session token:` を自動取得します。必要なら `KAIGI_TOKEN`、registered-agent tokenなら `KAIGI_BEARER_TOKEN` を使えます。
