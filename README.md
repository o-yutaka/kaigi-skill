# kaigi v4

agentchattr のマルチAI会議を、`議題を投げる → AIを集める → 独立案 → 反証 → 最終統合 → 結果保存` まで1コマンドで回すCLI。

## 最短

```bash
cd ~/kaigi-skill
git pull
bash install.sh
kaigi convene "この設計を本番採用すべきか"
kaigi result
```

通常チャットもそのまま使えます。

```bash
kaigi                           # interactive room
kaigi @claude これ見て          # 即送信
kaigi log 30
kaigi watch
kaigi agents
kaigi doctor
kaigi open
```

## Council: 既定のAI会議

`kaigi convene "議題"` はオンライン参加者を使って3段で進みます。

1. ROUND 1 — 全員を同時triggerし、他者を待たず独立分析。
2. ROUND 2 — 実際に返答した参加者を同時triggerし、反証・見落とし・修正版を出す。
3. FINAL — synthesizer が全ログを比較し `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` に統合。

各会議には `run_id` が付き、進行状態・参加者・各round・最終返答を `~/.local/state/kaigi/runs/` に保存します。

```bash
kaigi convene "議題" --agents claude,codex,chatgpt
kaigi convene "議題" --quorum 2
kaigi convene "議題" --synth chatgpt
kaigi convene "議題" --max-agents 4
kaigi convene "議題" --round-timeout 90
```

## 結果を後から回収

```bash
kaigi result                    # 最新runの最終結論
kaigi result RUN_ID
kaigi result --json
kaigi history                   # 過去20件
kaigi history 50 --json
kaigi export                    # 最新runをMarkdownへ
kaigi export RUN_ID --format json -o result.json
```

途中でtimeoutしてもrun自体は残ります。未完了を「完了」とは扱いません。

## AIの起床ルール

Council開始時、設定済みのローカルAPI agent（LM Studio / llama-server / Ollama等）は自動起床を試みます。クラウドAPI agentは、料金や外部副作用を勝手に発生させないため、既定の自動起床対象にはしません。

```bash
kaigi wake                      # local APIだけ
kaigi wake localglm             # 明示したagent
kaigi wake chatgpt              # 明示指定なのでcloudも起動対象
kaigi wake --all-api --cloud    # 全API agentを明示起床
kaigi convene "議題" --no-wake
```

`--agents chatgpt,claude` のようにクラウドAPI agentを明示参加させた場合は、その指定を起動意思として扱います。

## 任意のOpenAI互換APIを参加者にする

agentchattr の `wrapper_api.py` をそのまま利用します。

```bash
# ローカル llama-server / LM Studio 等
kaigi api add localglm \
  --base-url http://127.0.0.1:8080/v1 \
  --model my-model

# API keyが必要なクラウド互換endpoint
export EXPERT_API_KEY="..."
kaigi api add expert \
  --base-url https://example.com/v1 \
  --model expert-model \
  --api-key-env EXPERT_API_KEY

kaigi api list
# config追加後はagentchattrを再起動して設定を再読込
kaigi api start localglm
```

API keyの値は `config.local.toml` へ保存せず、環境変数名だけ保存します。

## ChatGPT bridge

ChatGPTは汎用API agentのショートカットです。ChatGPT Web/App/Plusログインを流用するものではなく `OPENAI_API_KEY` を使います。

```bash
export OPENAI_API_KEY="..."
kaigi chatgpt setup
# agentchattr再起動後
kaigi chatgpt start
kaigi chatgpt status
```

モデルは `kaigi chatgpt setup --model MODEL` または `KAIGI_CHATGPT_MODEL` で変更できます。

## agentchattr native Sessions

上流の構造化Sessionsも再実装せず利用します。

```bash
kaigi templates
kaigi convene "実装計画" --template planning
kaigi convene "A案 vs B案" --template debate
kaigi convene "この変更をレビュー" --template code-review
kaigi convene "UIを批評" --template design-critique
```

明示cast:

```bash
kaigi convene "計画" --template planning \
  --cast planner=claude \
  --cast challenger=codex \
  --cast synthesiser=chatgpt
```

## interactive room

```text
/convene TOPIC       Council開始
/result              最新の保存済み結果
/history             run履歴
/agents              参加/設定AI一覧
/to claude 本文      @claudeへ送信
/log 50              履歴
/status              状態
/quit                終了
```

## インストール先

`install.sh` は `~/.local/bin/kaigi` をこのrepositoryの実行shimへsymlinkし、同じ `SKILL.md` を Claude / Hermes / Codex / OpenCode のglobal skill locationへ冪等配置します。`kaigi` と `kaigi_core.py` は同じrepository内に保持してください。

## 主な環境変数

```bash
AGENTCHATTR_HOME=~/agentchattr
AGENTCHATTR_SERVER=http://127.0.0.1:8300
KAIGI_CHANNEL=general
KAIGI_STATE_DIR=~/.local/state/kaigi
KAIGI_TOKEN=...
KAIGI_BEARER_TOKEN=...
KAIGI_CHATGPT_MODEL=...
```

認証は明示tokenがなければserver logの `Session token:` を取得します。Python 3.11+ 推奨（API agent設定読込に標準 `tomllib` を使用）。
