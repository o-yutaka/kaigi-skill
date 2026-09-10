#!/usr/bin/env bash
# kaigi v6 installer — idempotent
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAIGI_SCRIPT="$SKILL_DIR/kaigi"
KAIGI_CORE="$SKILL_DIR/kaigi_core.py"
KAIGI_OPS="$SKILL_DIR/kaigi_ops.py"
KAIGI_V6="$SKILL_DIR/kaigi_v6.py"
SKILL_MD="$SKILL_DIR/SKILL.md"
LOCAL_BIN="$HOME/.local/bin"

[[ -f "$KAIGI_SCRIPT" ]] || { echo "エラー: $KAIGI_SCRIPT がありません" >&2; exit 1; }
[[ -f "$KAIGI_CORE" ]] || { echo "エラー: $KAIGI_CORE がありません。repository一式を更新してください" >&2; exit 1; }
[[ -f "$KAIGI_OPS" ]] || { echo "エラー: $KAIGI_OPS がありません。repository一式を更新してください" >&2; exit 1; }
[[ -f "$KAIGI_V6" ]] || { echo "エラー: $KAIGI_V6 がありません。repository一式を更新してください" >&2; exit 1; }
[[ -f "$SKILL_MD" ]] || { echo "エラー: $SKILL_MD がありません" >&2; exit 1; }

chmod +x "$KAIGI_SCRIPT"
mkdir -p "$LOCAL_BIN"
LINK_PATH="$LOCAL_BIN/kaigi"

if [[ -e "$LINK_PATH" && ! -L "$LINK_PATH" ]]; then
  BACKUP="$LINK_PATH.backup.$(date +%Y%m%d%H%M%S)"
  mv "$LINK_PATH" "$BACKUP"
  echo "既存CLIを退避: $BACKUP"
fi
ln -sfn "$KAIGI_SCRIPT" "$LINK_PATH"
echo "✓ CLI: $LINK_PATH"

install_skill() {
  local target="$1"
  mkdir -p "$target"
  if [[ ! -f "$target/SKILL.md" ]] || ! cmp -s "$SKILL_MD" "$target/SKILL.md"; then
    cp "$SKILL_MD" "$target/SKILL.md"
    echo "✓ skill: $target"
  else
    echo "✓ skill: $target (latest)"
  fi
}

install_skill "$HOME/.claude/skills/kaigi"
install_skill "$HOME/.hermes/skills/kaigi"
install_skill "${CODEX_HOME:-$HOME/.codex}/skills/kaigi"
install_skill "$HOME/.config/opencode/skills/kaigi"

if [[ ":${PATH}:" != *":${LOCAL_BIN}:"* ]]; then
  echo
  echo "注意: $LOCAL_BIN が PATH にありません。以下をshell profileへ追加してください:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo
echo "kaigi v6 install complete"
echo "  kaigi \"議題\"                           # safe-auto→Council→proof packet"
echo "  kaigi @claude これ見て                  # 個別agentへ即送信"
echo "  kaigi decide \"議題\" --dry-run          # 自動選定だけ確認"
echo "  kaigi decide \"議題\" --allow-cloud      # cloud APIも候補に許可"
echo "  kaigi recover                           # 最新未完了runを再照合→再開→packet"
echo "  kaigi result                            # 最新の最終結論"
echo "  kaigi verify --live                     # packet/ledger/live transcript検証"
echo "  kaigi handoff                           # 非権限advisory packet生成"
echo "  kaigi audit                             # 保存run/proof監査"
echo "  kaigi launch claude codex               # CLI AIを新terminalへ起動"
echo "  kaigi agents                            # online + 設定AI"
echo "  kaigi doctor                            # 診断"
echo
echo "ChatGPT bridge (API):"
echo "  export OPENAI_API_KEY=..."
echo "  kaigi chatgpt setup"
echo "  # agentchattr再起動後: kaigi chatgpt start"
