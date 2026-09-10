#!/usr/bin/env bash
# kaigi v8 installer — provider-neutral, capability-aware, relay-ready, idempotent
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAIGI_SCRIPT="$SKILL_DIR/kaigi"
KAIGI_CORE="$SKILL_DIR/kaigi_core.py"
KAIGI_OPS="$SKILL_DIR/kaigi_ops.py"
KAIGI_V6="$SKILL_DIR/kaigi_v6.py"
KAIGI_POLICY="$SKILL_DIR/kaigi_policy.py"
KAIGI_CAPS="$SKILL_DIR/kaigi_capabilities.py"
KAIGI_AUTH="$SKILL_DIR/kaigi_auth.py"
KAIGI_RELAY="$SKILL_DIR/kaigi_relay.py"
KAIGI_RELAY_V81="$SKILL_DIR/kaigi_relay_v81.py"
SKILL_MD="$SKILL_DIR/SKILL.md"
LOCAL_BIN="$HOME/.local/bin"
CANONICAL_SKILL="$HOME/.local/share/kaigi/skills/kaigi"
TARGETS="${KAIGI_SKILL_TARGETS:-auto}"

for file in "$KAIGI_SCRIPT" "$KAIGI_CORE" "$KAIGI_OPS" "$KAIGI_V6" "$KAIGI_POLICY" "$KAIGI_CAPS" "$KAIGI_AUTH" "$KAIGI_RELAY" "$KAIGI_RELAY_V81" "$SKILL_MD"; do
  [[ -f "$file" ]] || { echo "エラー: $file がありません。repository一式を更新してください" >&2; exit 1; }
done

chmod +x "$KAIGI_SCRIPT" "$KAIGI_RELAY" "$KAIGI_RELAY_V81"
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

csv_has() {
  local needle="$1"
  case ",${TARGETS}," in
    *,all,*|*,"$needle",*) return 0 ;;
    *) return 1 ;;
  esac
}

should_install_adapter() {
  local name="$1" root="$2" binary="$3" env_hint="${4:-}"
  if csv_has "$name"; then return 0; fi
  [[ "$TARGETS" == "auto" ]] || return 1
  [[ -d "$root" ]] && return 0
  [[ -n "$env_hint" ]] && return 0
  command -v "$binary" >/dev/null 2>&1 && return 0
  return 1
}

install_optional() {
  local name="$1" root="$2" binary="$3" target="$4" env_hint="${5:-}"
  if should_install_adapter "$name" "$root" "$binary" "$env_hint"; then
    install_skill "$target"
  else
    echo "- adapter skill: $name (not detected)"
  fi
}

# Canonical skill is mandatory. Provider/tool locations are optional adapters.
install_skill "$CANONICAL_SKILL"
install_optional "claude" "$HOME/.claude" "claude" "$HOME/.claude/skills/kaigi"
install_optional "hermes" "$HOME/.hermes" "hermes" "$HOME/.hermes/skills/kaigi"
install_optional "codex" "${CODEX_HOME:-$HOME/.codex}" "codex" "${CODEX_HOME:-$HOME/.codex}/skills/kaigi" "${CODEX_HOME:-}"
install_optional "opencode" "$HOME/.config/opencode" "opencode" "$HOME/.config/opencode/skills/kaigi"

if [[ ":${PATH}:" != *":${LOCAL_BIN}:"* ]]; then
  echo
  echo "注意: $LOCAL_BIN が PATH にありません。shell profileへ追加してください:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo
echo "kaigi v8 install complete — provider-neutral / capability-aware / relay-progress-ready"
echo "  kaigi \"議題\"                                      # capability cast→Council→proof"
echo "  kaigi cast \"議題\"                                 # 副作用なしでcast確認"
echo "  kaigi caps                                          # capability registry"
echo "  kaigi relay pair --url URL                         # single-use codeで端末pair"
echo "  kaigi relay start                                  # hardened outbound polling daemon開始"
echo "  kaigi relay status                                 # relay + active Council stage"
echo "  kaigi decide \"議題\" --need coding,red-team      # 必須能力を固定"
echo "  kaigi policy                                        # provider-neutral policy確認"
echo "  kaigi recover                                       # 再照合→再開→packet"
echo "  kaigi result                                        # 最新の最終結論"
echo "  kaigi verify --live                                 # proof検証"
echo
echo "Local agentchattr auth: explicit env -> live loopback session discovery -> legacy log"
echo "Capability registry: ${KAIGI_CAPABILITY_REGISTRY:-${KAIGI_CONFIG_DIR:-$HOME/.config/kaigi}/capabilities.json}"
echo "Provider-specific skill adapters are optional:"
echo "  KAIGI_SKILL_TARGETS=none bash install.sh"
echo "  KAIGI_SKILL_TARGETS=codex,opencode bash install.sh"
echo "  KAIGI_SKILL_TARGETS=all bash install.sh"
