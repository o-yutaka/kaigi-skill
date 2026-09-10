---
name: kaigi
description: agentchattr のマルチAI会議を操作する。AIの起動、会議確認・発言・監視・招集、複数AIの独立分析/反論/統合、未完了runの再照合/再開、保存済み結論の回収、Claude/Codex/ChatGPT/ローカルAPIモデル等への問い合わせが必要な時に使う。
---
# kaigi

`kaigi` CLI で agentchattr 会議を操作する。

## 優先フロー

通常の複数AI検討は `kaigi convene "議題"`。必要なCLI agentがofflineで参加者を明示できる場合は `kaigi go "議題" --agents claude,codex,...` を優先し、起動→online確認→Councilまでつなぐ。

会議後は `kaigi result` で最終結果を回収する。既存runがある場合は `kaigi history` を先に確認し、同一目的のcomplete runを無駄に再生成しない。

## CLI launch

- `kaigi launch claude codex` — agentchattr公式 `wrapper.py` を新terminalで起動。
- `kaigi launch --all` — 設定済みCLI agentを明示的に起動。
- `--here` — 現terminalで1 agent。
- `--dry-run` — 起動せず経路確認。

通常wrapperだけを使う。skip-permissions / bypass launcherを自動選択しない。

## Council contract

1. ROUND1: 全参加者を同時triggerし独立分析。
2. ROUND2: 実応答者だけ同時triggerし反証・見落とし・修正版。
3. FINAL: synthesizerが `DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS` に統合。

FINAL replyを実観測した場合だけcomplete。timeout/欠落を成功扱いしない。

## Resume / reconcile

- `kaigi reconcile [RUN_ID]` — chat実績からrunを副作用なしで再照合。メッセージを新規送信しない。
- `kaigi resume [RUN_ID]` — reconcile後、未完了Councilの不足stageだけ進める。

resumeは保存済みmessage IDsと `RUN=<run_id>` markerを確認し、既に存在するROUND2/FINALを再送しない。complete runなら再実行せずfinalを返す。

## Run ledger

`KAIGI_STATE_DIR`（既定 `~/.local/state/kaigi`）にrun JSONを保存。

- `kaigi result [RUN_ID]`
- `kaigi history [N]`
- `kaigi export [RUN_ID] --format md|json`

## API agent / wake governance

上流 `wrapper_api.py` を使用する。

- 通常Council auto-wake: local API agentだけ。
- cloud APIは暗黙に起動しない。
- `--agents chatgpt,...`, `kaigi wake NAME`, `kaigi api start NAME` のような明示指定時だけcloud起動を試みてよい。
- `kaigi api add NAME --base-url URL --model MODEL [--api-key-env ENV]` でOpenAI互換endpointを追加。
- secret値は保存せず環境変数名だけ設定する。

## ChatGPT bridge

`kaigi chatgpt setup/start/status`。Web/App/Plusセッション流用ではなく `OPENAI_API_KEY` を使うOpenAI-compatible API agent。

## Native Sessions

`--template planning|debate|code-review|design-critique` は上流Sessions APIを使う。役割固定は `--cast role=agent`。active Session競合時に別会議を勝手に重ねない。

## 個別操作

- `kaigi` — room
- `kaigi @NAME 本文` — 即送信
- `kaigi agents` — online + config
- `kaigi log 30` / `kaigi watch`
- `kaigi doctor`

## 成功判定

送信API成功、online status、agent reply、Session start、FINAL replyなど観測済みのものだけ成功として扱う。未観測を推測でPASSにしない。
