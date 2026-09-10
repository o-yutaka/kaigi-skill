# kaigi v5

agentchattr のマルチAI会議を、`AIを起こす → 招集 → 独立案 → 反証 → 最終統合 → 永続run → 再開/回収` まで短いコマンドで扱うCLI。

## 最短

```bash
cd ~/kaigi-skill
git pull
bash install.sh
kaigi convene "この設計を本番採用すべきか"
kaigi result
```

Claude/CodexなどCLI agentがまだ起動していない場合は明示して一気に揃えられます。

```bash
kaigi go "この設計を決める" --agents claude,codex
```

`go` は指定agentを準備してonline確認後、その同じ参加者でCouncilを開始します。

## 普通の会議操作

```bash
kaigi                           # interactive room
kaigi @claude これ見て          # 即送信
kaigi log 30
kaigi watch
kaigi agents
kaigi doctor
kaigi open
```

## CLI AIを起こす

agentchattr公式 `wrapper.py AGENT` を使います。独自agent runnerではありません。

```bash
kaigi launch claude codex
kaigi launch --all
kaigi launch claude --here      # 現在terminalで起動
kaigi launch claude --dry-run   # 起動経路だけ確認
```

新terminalの優先順は、現在のtmux → WSLのWindows Terminal → gnome-terminal → x-terminal-emulator → xterm → macOS Terminalです。通常wrapperのみを使い、skip-permissions/bypass系launcherは使用しません。

## Council

`kaigi convene "議題"` はオンライン参加者を使って3段で進みます。

1. ROUND 1 — 全員を同時triggerし、他者を待たず独立分析。
2. ROUND 2 — 実際に返答した参加者だけを同時triggerし、反証・見落とし・修正版を出す。
3. FINAL — synthesizer が全ログを比較し `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` に統合。

```bash
kaigi convene "議題" --agents claude,codex,chatgpt
kaigi convene "議題" --quorum 2
kaigi convene "議題" --synth chatgpt
kaigi convene "議題" --max-agents 4
kaigi convene "議題" --round-timeout 90
```

各Councilにはrun IDが付き、状態・参加者・各round・最終返答を `~/.local/state/kaigi/runs/` に保存します。

## 未完了Councilを捨てない

terminal切断、timeout、`--kickoff-only` 後でもrunは残ります。

```bash
kaigi reconcile                 # 最新runをchat実績と再照合。新規送信なし
kaigi reconcile RUN_ID
kaigi resume                    # 最新の未完了Councilを続きから実行
kaigi resume RUN_ID
kaigi resume RUN_ID --quorum 2 --round-timeout 90
```

`reconcile` は保存済みmessage IDと `RUN=<run_id>` markerを使って、既に送信済みのROUND2/FINALや遅れて返ったFINALを回収します。新しい会議メッセージは送りません。

`resume` はまず同じ再照合を行い、既に存在するROUNDを再送せず、不足しているstageだけ進めます。既にcompleteならその最終結果を表示して終了します。

## 結果・履歴

```bash
kaigi result
kaigi result RUN_ID
kaigi result --json
kaigi history
kaigi history 50 --json
kaigi export
kaigi export RUN_ID --format json -o result.json
```

## API AI / ローカルモデル

設定済みlocal API agent（LM Studio / llama-server / Ollama等）は通常Council開始時に自動wake対象です。cloud API agentは暗黙には起こしません。

```bash
kaigi wake
kaigi wake localglm
kaigi wake chatgpt              # 明示指定なのでcloudも対象
kaigi wake --all-api --cloud
```

任意OpenAI互換endpoint:

```bash
kaigi api add localglm \
  --base-url http://127.0.0.1:8080/v1 \
  --model my-model

export EXPERT_API_KEY="..."
kaigi api add expert \
  --base-url https://example.com/v1 \
  --model expert-model \
  --api-key-env EXPERT_API_KEY

kaigi api list
kaigi api start localglm
```

secret値は `config.local.toml` に保存せず、環境変数名だけ保存します。

## ChatGPT bridge

ChatGPT Web/App/Plus loginの流用ではなくOpenAI API agentです。

```bash
export OPENAI_API_KEY="..."
kaigi chatgpt setup
# agentchattr再起動後
kaigi chatgpt start
kaigi chatgpt status
```

## agentchattr native Sessions

```bash
kaigi templates
kaigi convene "実装計画" --template planning
kaigi convene "A案 vs B案" --template debate
kaigi convene "この変更をレビュー" --template code-review
kaigi convene "UIを批評" --template design-critique
```

役割固定は `--cast role=agent`。

## interactive room

```text
/convene TOPIC       Council開始
/result              最新result
/history             run履歴
/agents              AI一覧
/to claude 本文      個別送信
/log 50              履歴
/status              状態
/quit                終了
```

## インストール

`install.sh` は `kaigi`, `kaigi_core.py`, `kaigi_ops.py` の存在を確認し、`~/.local/bin/kaigi` をrepositoryへsymlinkします。同じ `SKILL.md` を Claude / Hermes / Codex / OpenCode のglobal skill locationへ冪等配置します。repository一式を保持してください。

主な環境変数は `AGENTCHATTR_HOME`, `AGENTCHATTR_SERVER`, `KAIGI_CHANNEL`, `KAIGI_STATE_DIR`, `KAIGI_TOKEN`, `KAIGI_BEARER_TOKEN`, `KAIGI_CHATGPT_MODEL`。Python 3.11+ 推奨。
