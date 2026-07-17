#!/usr/bin/env bash
# cc-zundamon installer (macOS)
# - Copies the Stop hook into ~/.claude/hooks/
# - Registers it as a Stop hook in ~/.claude/settings.json (idempotent, backed up)
# - Checks dependencies (python3 / VOICEVOX engine / afplay)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HOOKS_DIR="$CLAUDE_DIR/hooks"
SETTINGS="$CLAUDE_DIR/settings.json"
HOOK_SRC="$REPO_DIR/hooks/say-summary.py"
HOOK_DST="$HOOKS_DIR/say-summary.py"
ZUNDA_URL="${ZUNDA_URL:-http://localhost:50021}"

info()  { printf '\033[36m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m✓ %s\033[0m\n' "$*"; }
warn()  { printf '\033[33m! %s\033[0m\n' "$*"; }
die()   { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- 前提チェック -----------------------------------------------------------
[ "$(uname)" = "Darwin" ] || warn "macOS 以外を検出しました。本スクリプトは macOS 前提です（afplay/say に依存）。"
command -v python3 >/dev/null 2>&1 || die "python3 が見つかりません。先に Python 3 を入れてください。"
command -v afplay  >/dev/null 2>&1 || warn "afplay が見つかりません（通常 macOS 標準）。VOICEVOX 再生に必要です。"
[ -f "$HOOK_SRC" ] || die "フック本体が見つかりません: $HOOK_SRC"

# --- フック配置 -------------------------------------------------------------
mkdir -p "$HOOKS_DIR"
cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST"
ok "フックを配置: $HOOK_DST"

# --- settings.json に Stop フックを登録（冪等・バックアップ付き）-----------
if [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
  ok "settings.json をバックアップしました"
fi

HOOK_DST="$HOOK_DST" SETTINGS="$SETTINGS" python3 <<'PY'
import json, os

settings_path = os.environ["SETTINGS"]
cmd = os.environ["HOOK_DST"]

try:
    with open(settings_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
except (FileNotFoundError, json.JSONDecodeError, ValueError):
    data = {}

hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])

already = any(
    isinstance(g, dict)
    and any(
        isinstance(h, dict) and h.get("command") == cmd
        for h in g.get("hooks", [])
    )
    for g in stop
)

if already:
    print("  既に登録済み — settings.json は変更しません")
else:
    stop.append({"hooks": [{"type": "command", "command": cmd}]})
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("  Stop フックを登録しました")
PY
ok "settings.json を更新しました: $SETTINGS"

# --- VOICEVOX engine チェック ----------------------------------------------
if curl -s -m 3 "$ZUNDA_URL/version" >/dev/null 2>&1; then
  ok "VOICEVOX engine 応答あり ($ZUNDA_URL)"
else
  warn "VOICEVOX engine ($ZUNDA_URL) に繋がりません。"
  echo "    → VOICEVOX を起動してください（未導入なら https://voicevox.hiroshiba.jp/ からダウンロード）。"
  echo "    → engine 未起動でも macOS の say 音声にフォールバックします（ずんだもんの声にはなりません）。"
fi

echo
ok "インストール完了。"
echo "テスト:"
echo "  echo 'こんにちは、ずんだもんなのだ。' | curl -s -m 3 $ZUNDA_URL/version >/dev/null && echo ready"
echo "  次に Claude Code で何か応答させると読み上げられます。"
echo "  応答末尾を <speak>...</speak> で囲むと、その中身だけを読み上げます（README 参照）。"
