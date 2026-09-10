#!/usr/bin/env bash
# kaigi v7 installer — idempotent
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAIGI_SCRIPT="$SKILL_DIR/kaigi"
KAIGI_CORE="$SKILL_DIR/kaigi_core.py"
KAIGI_OPS="$SKILL_DIR/kaigi_ops.py"
KAIGI_V6="$SKILL_DIR/kaigi_v6.py"
KAIGI_V7="$SKILL_DIR/kaigi_v7.py"
SKILL_MD="$SKILL_DIR/SKILL.md"
LOCAL_BIN="$HOME/.local/bin"

for runtime in "$KAIGI_SCRIPT" "$KAIGI_CORE" "$KAIGI_OPS" "$KAIGI_V6" "$KAIGI_V7"; do
  [[ -f "$runtime" ]] || { echo "エラー: $runtime がありません。repository一式を更新してください" >&2; exit 1; }
done
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
  echo "注意: $LOCAL_BIN が PATH にありません。shell profileへ追加してください:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo
echo "kaigi v7 install complete"
echo "  kaigi \"議題\"                         # safe-auto→Council→proof"
echo "  kaigi @claude これ見て                # 個別送信"
echo "  kaigi verify --live --current-runtime # evidence + runtime provenance検証"
echo "  kaigi queue                           # verified DecisionをTO DOへ登録"
echo "  kaigi jobs --status open              # TO DO一覧"
echo "  kaigi recover                         # 未完了Councilを回復→proof"
echo "  kaigi handoff                         # advisory handoff"
echo "  kaigi launch claude codex             # CLI AI起動"
echo "  kaigi agents                          # AI状態"
echo "  kaigi doctor                          # 診断"
