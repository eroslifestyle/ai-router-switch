# AI Router Mode Panel — Documentation

## Overview

Standalone control panel for the AI Router (`:8787`) with 5 orchestration modes. Provides both CLI and GUI interfaces to switch modes and monitor router health in real-time.

**Location**: `~/.claude/router-mode/`  
**Modes**: anthropic · minimax · mixed · inverse  
**Proxy**: `:9988` (CORS bypass for browser fetch)

---

## Components

### 1. CLI: `routestats`

Standalone Python script for mode management and status queries.

**Commands**:
```bash
routestats status              # Show current mode + health OK/OFFLINE
routestats modes               # List all 5 modes with executors
routestats switch <mode>       # Switch mode via proxy POST /admin/mode/{mode}
routestats card                # Launch card.py GUI
routestats json                # Output state as JSON
```

**Example**:
```bash
$ routestats status
anthropic (Opus / Sonnet) — HEALTH OK
```

### 2. GUI: `card.py`

PySide6 frameless card with 5 mode buttons, hero display, and system tray integration.

**Design**:
- **Hero**: large mode name + executor info + health badge (blinking dot when healthy)
- **Grid**: 5 mode cards (icon + label + executor + ON/ATTIVO button)
- **Titlebar**: minimize (─) · restart router (↻) · close (✕)
- **Tray**: click to restore, context menu with 5 modes + restart
- **Polling**: updates every 5 seconds via `http://localhost:9988/mode`

**Theme** (dark, OKLCH-conscious):
- Accent: `#4ade80` (verde caldo, ~10% surface)
- Info: `#38bdf8` (blue)
- Surfaces: `#0f1419` → `#1a2029` → `#222736`
- Active card: green border + bg tint + `✓ ATTIVO` badge

**Launch**:
```bash
~/.claude/router-mode/routestats card
```

Or from GNOME Applications menu (icon 🔀).

### 3. Proxy Server: `router-mode-panel.py`

Python HTTP server on `:9988` that proxies router control to `:8787` and handles CORS.

**Routes**:
- `GET /mode` — read current mode from `~/.claude/ai-router-mode` file
- `POST /admin/mode/{mode}` — write mode file + return JSON `{"ok": true, "mode": "mixed", ...}`
- `GET /health` — proxy to `:8787/health` for router liveness

**Why**: Browser `fetch()` from `file://` protocol can't reach `localhost:8787` (sandbox). Proxy on `:9988` satisfies same-origin policy.

**Start**:
```bash
python3 ~/.claude/scripts/router-mode-panel.py
```

Runs in background; auto-killed if already running.

---

## Architecture

### Mode Switching Flow

```
User clicks "ON" button on card
  ↓
card.py sends POST /admin/mode/{mode} to localhost:9988
  ↓
Proxy reads mode, writes ~/.claude/ai-router-mode file
  ↓
Router picks up mode file at next request
  ↓
card.py polls GET /mode every 5s → updates UI
```

### State Storage

**Mode file**: `~/.claude/ai-router-mode` (1 line: mode name)

Example:
```
mixed
```

Read by:
- `routestats status` (fallback if proxy down)
- `card.py` (polling)
- Router `:8787` (every request)

### Polling Cycle

**card.py refresh** (every 5 seconds):
1. `GET http://localhost:9988/mode` → current mode
2. `GET http://localhost:8787/health` → `ok: true/false`
3. Update hero + card buttons + dot blink + health badge

**Offline handling**:
- If proxy unreachable → `"?"` mode, `OFFLINE` badge (red), buttons grayed
- If router unhealthy → health badge red, but mode still switchable (safety: can restart)

---

## Desktop Integration

### GNOME Applications

**Desktop entry**: `~/.local/share/applications/routestats.desktop`

Exec: `/home/mrxxx/.claude/router-mode/routestats card`

Appears in GNOME applications menu as "AI Router Mode Panel" with 🔀 icon.

### Dock Icon

**Icon**: `~/.claude/scripts/router-mode-icon.png` (48×48 PNG, gradient green/blue/orange/yellow switch symbol)

### Token-Ledger Extension (future)

Planned: badge in GNOME topbar showing `MODE: mixed` with quick-switch dropdown.

---

## Modes Reference

| Mode | Icon | Orchestrator | Executor | Use Case |
|------|------|--------------|----------|----------|
| **anthropic** | 🔵 | Opus / Sonnet | Opus / Sonnet | Default, highest quality |
| **minimax** | 🟠 | MiniMax M3 | MiniMax M2.7 | Cost-optimized |
| **mixed** | 🔷 | Anthropic (Opus) | MiniMax M2.7 | Balanced: smart planning + fast exec |
| **inverse** | 🔶 | MiniMax M3 (THINK) + Opus (OPPOSE) | MiniMax M2.7 (ACT) | Adversarial verify (slow, thorough) |

---

## Troubleshooting

### "OFFLINE" badge, buttons unresponsive

1. **Check proxy**:
   ```bash
   curl -s http://localhost:9988/mode
   # Should return: {"mode": "anthropic"} or similar
   ```

2. **Check router**:
   ```bash
   curl -s http://localhost:8787/health
   # Should return: {"ok": true}
   ```

3. **Restart proxy**:
   ```bash
   pkill -f "router-mode-panel.py"
   python3 ~/.claude/scripts/router-mode-panel.py &
   ```

### Mode doesn't switch

1. Check `~/.claude/ai-router-mode` exists and is writable:
   ```bash
   cat ~/.claude/ai-router-mode
   # Should show current mode (1 line)
   ```

2. Check proxy logs (if running in foreground):
   ```bash
   python3 ~/.claude/scripts/router-mode-panel.py
   # Watch for POST /admin/mode/... responses
   ```

3. Restart router after mode change (if needed):
   Click ↻ button in titlebar, or:
   ```bash
   systemctl --user restart ai-router
   ```

### Card doesn't launch from Applications menu

1. Verify desktop entry:
   ```bash
   cat ~/.local/share/applications/routestats.desktop
   # Icon= should point to router-mode-icon.png
   ```

2. Clear GNOME cache:
   ```bash
   rm -rf ~/.cache/gnome-software/
   ```

3. Launch manually:
   ```bash
   ~/.claude/router-mode/routestats card
   ```

---

## Development

### Dependencies

- **PySide6** (`pip install PySide6`)
- **Python 3.9+**

### File Structure

```
~/.claude/router-mode/
├── card.py                      # PySide6 GUI (280 LOC)
├── routestats                   # CLI script (executable)
├── README.md                    # This file
├── widget_state.json            # (auto-generated) card state cache
└── ~/.local/share/
    └── applications/
        └── routestats.desktop   # GNOME Applications entry
```

### Styling Notes

- **Palette**: OKLCH-conscious (hex for PySide6 compatibility)
- **Layout**: widget-based (QLabel/QPushButton), NOT manual painting
- **ObjectName selectors**: `QWidget#hero`, `QWidget#modecard` to prevent style propagation
- **No Qt5 APIs**: PercentSpacing removed (incompatible)

### Future Enhancements

- [ ] Token-ledger topbar badge integration
- [ ] Persistent window position (save in widget_state.json)
- [ ] Keyboard shortcuts (Ctrl+M to minimize, Ctrl+Q to quit)
- [ ] Mode indicators in CLI output (color + emoji)

---

## License & Attribution

Standalone component of ai-router-switch project. Uses PySide6 (LGPL). Dark theme inspired by editor defaults.

**Version**: 1.0  
**Last updated**: 2026-07-02
