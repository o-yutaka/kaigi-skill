#!/bin/bash
# kaigi スキル インストールスクリプト（冪等）

set -e

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAIGI_SCRIPT="$SKILL_DIR/kaigi"
SKILL_MD="$SKILL_DIR/SKILL.md"

# 1. ~/.local/bin にシンボリックリンク作成
LOCAL_BIN="$HOME/.local/bin"
if [ ! -d "$LOCAL_BIN" ]; then
    mkdir -p "$LOCAL_BIN"
fi

LINK_PATH="$LOCAL_BIN/kaigi"
if [ -L "$LINK_PATH" ]; then
    # シンボリンク既存、古い場合は削除
    if [ "$(readlink "$LINK_PATH")" != "$KAIGI_SCRIPT" ]; then
        rm "$LINK_PATH"
        ln -s "$KAIGI_SCRIPT" "$LINK_PATH"
        echo "✓ $LINK_PATH を更新しました"
    else
        echo "✓ $LINK_PATH は既に最新です"
    fi
elif [ -f "$LINK_PATH" ]; then
    # 通常ファイルが存在する場合は削除してシンボリンク作成
    rm "$LINK_PATH"
    ln -s "$KAIGI_SCRIPT" "$LINK_PATH"
    echo "✓ $LINK_PATH を置き換えました"
else
    # 存在しない場合はシンボリンク作成
    ln -s "$KAIGI_SCRIPT" "$LINK_PATH"
    echo "✓ $LINK_PATH にシンボリンクを作成しました"
fi

# 2. ~/.claude/skills/kaigi にコピー
CLAUDE_SKILLS="$HOME/.claude/skills/kaigi"
if [ ! -d "$CLAUDE_SKILLS" ]; then
    mkdir -p "$CLAUDE_SKILLS"
fi

if [ ! -f "$CLAUDE_SKILLS/SKILL.md" ] || ! diff -q "$SKILL_MD" "$CLAUDE_SKILLS/SKILL.md" > /dev/null 2>&1; then
    cp "$SKILL_MD" "$CLAUDE_SKILLS/SKILL.md"
    echo "✓ $CLAUDE_SKILLS/SKILL.md をコピーしました"
else
    echo "✓ $CLAUDE_SKILLS/SKILL.md は既に最新です"
fi

# 3. ~/.hermes/skills/kaigi にコピー
HERMES_SKILLS="$HOME/.hermes/skills/kaigi"
if [ ! -d "$HERMES_SKILLS" ]; then
    mkdir -p "$HERMES_SKILLS"
fi

if [ ! -f "$HERMES_SKILLS/SKILL.md" ] || ! diff -q "$SKILL_MD" "$HERMES_SKILLS/SKILL.md" > /dev/null 2>&1; then
    cp "$SKILL_MD" "$HERMES_SKILLS/SKILL.md"
    echo "✓ $HERMES_SKILLS/SKILL.md をコピーしました"
else
    echo "✓ $HERMES_SKILLS/SKILL.md は既に最新です"
fi

echo ""
echo "インストール完了！"
echo ""
echo "使用方法:"
echo "  kaigi log          # 直近20件を表示"
echo "  kaigi watch        # リアルタイム表示"
echo "  kaigi say \"TEXT\"   # メッセージ送信"
echo "  kaigi status       # サーバー状態確認"
echo "  kaigi start        # サーバー起動"
echo "  kaigi help         # ヘルプ表示"
echo ""
