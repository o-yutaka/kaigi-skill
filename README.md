# kaigi

agentchattr のマルチAI会議を、ターミナルから一番短く使うためのCLI。

## 使い方

```bash
bash ~/kaigi-skill/install.sh
kaigi
```

これだけで会議室に入ります。agentchattr が止まっていれば自動起動します。

よく使う操作:

```bash
kaigi @claude これ見て          # そのまま発言
kaigi log 30                   # 直近30件
kaigi watch                    # ライブ監視
kaigi status                   # 状態
kaigi doctor                   # 接続診断
kaigi open                     # Web UI
```

`kaigi say "..."` も残していますが、普段は `kaigi 本文` で送れます。

## interactive room

`kaigi` だけ実行すると会議ログを表示しながら入力できます。

```text
kaigi  http://127.0.0.1:8300  #general
12:01 claude: 確認します
12:02 codex: 修正候補があります
── 入力してEnterで送信 / /help / /quit ──
you › @claude その案で進めて
```

room内では `/quit`, `/status`, `/log 50`, `/to claude 本文` が使えます。

## 自動認識されるAIツール

`install.sh` は同じ `SKILL.md` を Claude、Hermes、Codex、OpenCode のglobal skill locationへ配置します。各AIから「会議を見て」「kaigiでClaudeに聞いて」のように頼んだ時にこのスキルを発見しやすくしています。

## 設定

通常は不要です。環境が違う時だけ設定します。

```bash
export AGENTCHATTR_HOME=~/agentchattr
export AGENTCHATTR_SERVER=http://127.0.0.1:8300
export KAIGI_WRAPPER=lmstudio
export KAIGI_CHANNEL=general
```

認証は server log の `Session token:` を自動取得します。必要なら `KAIGI_TOKEN`、registered agent tokenを使う場合は `KAIGI_BEARER_TOKEN` で明示できます。
