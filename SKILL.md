# kaigi

ターミナルから agentchattr の会議に参加・監視・発言するCLI。

## 概要

`kaigi` は agentchattr (マルチエージェント会議室) を CLI から操作するスキルです。リアルタイムメッセージ表示、メッセージ送信、サーバー状態確認ができます。

**サーバー**: http://127.0.0.1:8300
**WebSocket**: ws://127.0.0.1:8300/ws?token=`<TOKEN>`

## インストール

```bash
bash ~/kaigi-skill/install.sh
```

同スクリプトで以下が実行されます：
1. `kaigi` を `~/.local/bin/kaigi` にシンボリックリンク
2. `SKILL.md` を `~/.claude/skills/kaigi/` にコピー
3. `SKILL.md` を `~/.hermes/skills/kaigi/` にコピー

## サブコマンド

### kaigi log [N]

直近 N 件（既定 20 件）のメッセージを「HH:MM sender: text」形式で表示。sender ごとに異なる色で表示されます。

**例:**
```bash
kaigi log
kaigi log 50
```

### kaigi watch

新着メッセージを 2 秒ごとにポーリングしてリアルタイム表示します。Ctrl-C で終了。

**例:**
```bash
kaigi watch
```

### kaigi say "TEXT"

指定したテキストを generalチャネルに発言します。@メンション可。

**例:**
```bash
kaigi say "こんにちは、@claude"
kaigi say "タスク完了しました"
```

### kaigi status

agentchattr サーバーの生死状況と参加エージェント一覧を表示します。

**例:**
```bash
kaigi status
```

**出力例:**
```
✓ agentchattr サーバー: 起動中
参加エージェント:
  - claude
  - codex
```

### kaigi start

agentchattr が停止していれば起動します。既に起動していれば何もしません。

起動コマンド：
```bash
cd ~/agentchattr && env -u TMUX nohup ./venv/bin/python run.py > /tmp/agentchattr-server.log 2>&1 &
nohup ./venv/bin/python wrapper_api.py lmstudio >> /tmp/agentchattr-server.log 2>&1 &
```

**例:**
```bash
kaigi start
```

### kaigi help

使用方法を表示します。

**例:**
```bash
kaigi help
```

## トークン認証

内部的には `/tmp/agentchattr-server.log` から "Session token:" 行を抽出してトークンを得ます。サーバー起動時に自動的に設定されます。

## エラーメッセージ

**"トークンが見つかりません"**
→ `kaigi start` を実行してサーバーを起動してください。

**"agentchattr サーバーが起動していません"**
→ `kaigi start` を実行してください。

## 環境変数

- `AGENTCHATTR_SERVER` — サーバーURL（既定: `http://127.0.0.1:8300`）

## 依存関係

- bash
- curl
- python3 （標準ライブラリのみ + websockets）
- `/home/user/agentchattr/venv/bin/python` （websockets導入済み）

## 実装詳細

- **log/watch**: REST API (`GET /api/messages`) でメッセージを取得
- **say**: WebSocket (`ws://127.0.0.1:8300/ws?token=<TOKEN>`) で送信
  ```json
  {
    "type": "message",
    "text": "...",
    "channel": "general"
  }
  ```
- **status**: メッセージ履歴から sender を抽出して一覧化
- **start**: nohup で run.py と wrapper_api.py を起動

## 注記

- sender 名ごとに MD5 ハッシュで色を割り当てているため、実行環境で一貫した色分けが可能です
- watch モードで 2 秒ポーリングは低負荷・低遅延のバランスです
- say コマンドは `/home/user/agentchattr/venv/bin/python` の websockets ライブラリを使用します
