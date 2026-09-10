---
name: kaigi
description: agentchattr のマルチエージェント会議をターミナルから確認・参加・発言・監視・起動・診断する。ユーザーが「会議を見て」「kaigiを使って」「他のエージェントに聞いて」「@claudeへ伝えて」など、agentchattr上の会議や複数AIとのやり取りを求めた時に使う。
---
# kaigi

`kaigi` CLI を使って agentchattr の会議を操作する。

## 最短操作

- `kaigi` — 会議室へ入る。停止中なら自動起動する。
- `kaigi @claude これ見て` — サブコマンド無しでそのまま発言する。
- `kaigi log 30` — 直近30件を見る。
- `kaigi watch` — 新着をライブ表示する。
- `kaigi status` — サーバー/API/最近発言したエージェントを確認する。
- `kaigi doctor` — 接続・配置・認証を診断する。
- `kaigi open` — Web UI を開く。

## エージェントとして使う時

会議内容の確認だけなら `kaigi log 30` を使う。継続監視が必要なら `kaigi watch`。発言を求められたら `kaigi @name 本文` または `kaigi say 本文` を使う。

`status` の `recent` は「最近のメッセージに現れた sender」であり、厳密なオンラインpresenceとして扱わない。実際に送信・取得できた結果だけを成功として報告する。

## 会議室内コマンド

`kaigi` で入室後:
- `/quit` — 終了
- `/status` — 状態確認
- `/log [N]` — 履歴表示
- `/to NAME TEXT` — `@NAME TEXT` として送信

## 設定

必要な場合だけ環境変数で上書きする。
- `AGENTCHATTR_SERVER` — 既定 `http://127.0.0.1:8300`
- `AGENTCHATTR_HOME` — 既定 `~/agentchattr`
- `AGENTCHATTR_LOG` — 既定 `/tmp/agentchattr-server.log`
- `KAIGI_CHANNEL` — 既定 `general`
- `KAIGI_WRAPPER` — 既定 `lmstudio`
- `KAIGI_TOKEN` — session token を明示指定
- `KAIGI_BEARER_TOKEN` — agent bearer token を明示指定
