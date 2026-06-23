#!/usr/bin/env bash
# headroom #2 — istanza dedicata che comprime il context verso MiniMax.
# Claude Code -> ai-router(:8789) -> [minimax] -> headroom#2(:8790) -> api.minimax.io/anthropic
set -euo pipefail
DIR=/opt/headroom
HB="$DIR/.venv/bin/headroom"
PORT=8790
LOG="$HOME/.claude/logs/headroom-minimax.log"

if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "headroom#2 già attivo su :$PORT"; exit 0
fi

export ANTHROPIC_TARGET_API_URL="https://api.minimax.io/anthropic"
export HEADROOM_TELEMETRY=off
export HEADROOM_UPDATE_CHECK=off
export HEADROOM_SKIP_UPSTREAM_CHECK=1

nohup "$HB" proxy --host 127.0.0.1 --port "$PORT" --mode token --backend anthropic \
  >> "$LOG" 2>&1 &
echo $! > "$HOME/.claude/locks/headroom-minimax.pid"
echo "headroom#2 avviato (pid $(cat "$HOME/.claude/locks/headroom-minimax.pid")) su :$PORT"
