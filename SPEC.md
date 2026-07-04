# kaigi — agentchattr会議をターミナルで見る/発言するCLI

bash + python3標準ライブラリのみ（外部pipはwebsocket-client可: ~/agentchattr/venv/bin/python を使えばwebsockets導入済みのはず。venvのpythonを使うこと）。

サーバー: http://127.0.0.1:8300 (agentchattr)
- メッセージ取得: GET /api/messages?since_id=N （認証不要、JSON配列想定。フィールドは id, sender, text, channel, timestamp 相当。実際のフィールド名は ~/agentchattr/app.py を読んで正確に合わせること）
- 発言: WebSocket ws://127.0.0.1:8300/ws?token=<TOKEN> へ {"type":"message","text":"...","channel":"general"} を送る。senderは指定しない。
- TOKENは /tmp/agentchattr-server.log の "Session token:" 行から抽出。

## 成果物: ~/kaigi-skill/kaigi （実行可能bashスクリプト1本）
サブコマンド:
- kaigi log [N]     : 直近N件(既定20)を「HH:MM sender: text」形式で色付き表示（senderごとに色分け）
- kaigi watch       : 2秒ポーリングで新着を流し続ける（Ctrl-Cで終了）
- kaigi say "TEXT"  : general チャンネルに発言（@メンション可）
- kaigi status      : サーバー生死、参加エージェント一覧（APIがあれば）を表示
- kaigi start       : サーバー未起動なら起動:
    cd ~/agentchattr && env -u TMUX nohup ./venv/bin/python run.py > /tmp/agentchattr-server.log 2>&1 &
    続けて wrapper_api.py lmstudio も同様にnohup起動
- kaigi help        : 使い方表示（日本語）

## 追加成果物
- ~/kaigi-skill/SKILL.md : スキル説明（各エージェントが読む用。name: kaigi, description, 使用例）
- ~/kaigi-skill/install.sh : kaigi を ~/.local/bin/kaigi にsymlinkし、SKILL.mdを ~/.claude/skills/kaigi/ と ~/.hermes/skills/kaigi/ にコピーする冪等スクリプト

## 制約
- app.py を読んでAPIレスポンスの実フィールド名に合わせること（推測で書かない）
- エラー時は日本語で分かりやすく（サーバー停止中なら「kaigi start を実行」と案内）
