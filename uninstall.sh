#!/usr/bin/env bash
# cc-zundamon uninstaller (macOS)
# - Removes the Stop hook entry from ~/.claude/settings.json (backed up)
# - Removes the copied hook file
set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
HOOK_DST="$CLAUDE_DIR/hooks/say-summary.py"

ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }

if [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
  HOOK_DST="$HOOK_DST" SETTINGS="$SETTINGS" python3 <<'PY'
import json, os

settings_path = os.environ["SETTINGS"]
cmd = os.environ["HOOK_DST"]
with open(settings_path, encoding="utf-8") as f:
    data = json.load(f)

stop = data.get("hooks", {}).get("Stop", [])
new_stop = []
for g in stop:
    hs = [h for h in g.get("hooks", []) if not (isinstance(h, dict) and h.get("command") == cmd)]
    if hs:
        g["hooks"] = hs
        new_stop.append(g)
    # hooks が空になったグループは丸ごと落とす
if "hooks" in data and "Stop" in data["hooks"]:
    if new_stop:
        data["hooks"]["Stop"] = new_stop
    else:
        del data["hooks"]["Stop"]
    if not data["hooks"]:
        del data["hooks"]

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("  settings.json から Stop フックを除去しました")
PY
  ok "settings.json を更新しました"
else
  warn "settings.json が見つかりません: $SETTINGS"
fi

if [ -f "$HOOK_DST" ]; then
  rm -f "$HOOK_DST"
  ok "フックを削除しました: $HOOK_DST"
fi

ok "アンインストール完了。"
