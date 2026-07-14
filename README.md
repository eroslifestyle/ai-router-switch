# 🧭 AI Router Switch

> Self-hosted routing proxy for Claude Code (and any Anthropic-format client).
> Switch between **Claude**, **MiniMax**, and **GLM/z.ai** — with automatic failover,
> context compression, per-chat isolation, and cross-model verification.
>
> **One endpoint (`:8787`), seven modes, zero app restarts.**

---

## What's in the box

```
App (VSCode / Claude Code / any Anthropic client)
         │
         │  ANTHROPIC_BASE_URL = http://127.0.0.1:8787
         ▼
    ai-router (:8787)  ──► mode selects the backend
                                    │
                     ┌──────────────┼──────────────┐
                     ▼              ▼              ▼
               Anthropic        MiniMax        GLM/z.ai
              (api.anthropic)  (api.minimaxi)  (api.z.ai)
```

**Golden rule — Non-interference:** the router picks the backend. It never touches
your model's settings, skills, agents, MCP, tools, or system prompt.

---

## Modes (7)

### Core (Claude + MiniMax)

| Mode | Behaviour |
|---|---|
| `anthropic` | Claude only — no fallback |
| `minimax` | MiniMax-M3 only (no weekly limit, cheap) |
| `mixed` | Claude first → MiniMax on 429 / 5xx (bidirectional) |
| `inverse` | MiniMax generates → Claude Opus verifies critical tasks (auto-detected) |

### GLM / z.ai (Anthropic-compatible endpoint `api.z.ai/api/anthropic`)

| Mode | Behaviour |
|---|---|
| `glm` | GLM-5.2 classifies complexity → routes `glm-5-turbo` → `glm-4.7` → `glm-5.2` |
| `glm-minimax` | GLM-5.2 THINK → MiniMax ACT (streaming) → GLM verify on complex tasks |
| `anthropic-glm` | Claude orchestrates → GLM tiered execution → Claude verifies T2 tasks |

**GLM cost control:** peak window `14:00–18:00 Asia/Shanghai` (~08:00–12:00 Italy summer).
`glm-5.2` / `glm-5-turbo` cost 3× in peak → blocked for complex tasks, simple ones
use `glm-4.7`. Off-peak: full tiering, 1× promo pricing until 2026-09-30.
Fallback chain on error/quota: GLM → MiniMax → Claude.

---

## Ports (8)

| Port | Role |
|---|---|
| `8787` | Dynamic — follows `ai-mode` (default) |
| `8771` | Forced: `anthropic` |
| `8772` | Forced: `minimax` |
| `8773` | Forced: `mixed` |
| `8774` | Forced: `inverse` |
| `8775` | Forced: `glm` |
| `8776` | Forced: `glm-minimax` |
| `8777` | Forced: `anthropic-glm` |

---

## Switching modes

**Global** (all apps connected to `:8787`):

```bash
ai-mode anthropic      # or: minimax / mixed / inverse / glm / glm-minimax / anthropic-glm
ai-mode status
ai-mode log
```

**Per-chat** (isolated to this conversation, does NOT affect other chats):

```
!router minimax        # switch to minimax for this chat only
!router anthropic      # back to Claude for this chat
!router status         # show current mode + backends
!router reset          # restore global mode from ai-mode
```

Natural phrases like "usa solo minimax" or "passa a Claude" also work.
Per-chat commands are confined to the conversation fingerprint — no cross-talk between chats.

**Fixed-port** (explicit, no file writes):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772   # always minimax
export ANTHROPIC_BASE_URL=http://127.0.0.1:8777   # always anthropic-glm
```

---

## Quick start

```bash
# 1. Point your client at the router
#    ~/.claude/settings.json
{ "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787" } }

# 2. Run the router
python3 src/ai-router-proxy.py
#  or: systemctl --user start ai-router-proxy.service  (after setup)

# 3. Switch mode
ai-mode mixed
```

GLM modes need a z.ai key: `export GLM_API_KEY=...` or `secrets.sh set glm.api_key ...`.

---

## Resilience (triple defense)

- **systemd** — `Restart=always`, `OOMScoreAdjust=-900`, linger enabled
- **cron watchdog** (`scripts/ai-stack-guard.sh`) — restarts anything down every minute + `@reboot`
- **SessionStart hook** — ensures the stack is up when your IDE starts

Tested: `kill -9` on all services → fully restored in <10 s.

---

## Roadmap

| Phase | Status |
|---|---|
| Phase 1 — deterministic per-port isolation | ✅ Complete |
| Phase 2 — per-chat independence via conversation fingerprint | ✅ Complete |
| Phase 3 — in-chat commands (`!router`) + natural language | ✅ Complete |
| Phase 4 — circuit breaker with cooldown | ✅ Complete |

---

## Documentation

- 🇮🇹 [Manuale IT](docs/MANUAL.md) · [HTML](docs/manual-it.html)
- 🇬🇧 [English Manual](docs/MANUAL.en.md) · [HTML](docs/manual-en.html)
- 🗺️ [PIANO.md](docs/PIANO.md) — 44 design decisions & technical notes

---

## Community

- 💬 [GitHub Discussions](https://github.com/eroslifestyle/ai-router-switch/discussions)
- 📚 [Wiki](https://github.com/eroslifestyle/ai-router-switch/wiki)
- 🌐 [GitHub Pages](https://eroslifestyle.github.io/ai-router-switch/) — official docs site
- 🐛 [Issues](https://github.com/eroslifestyle/ai-router-switch/issues)

---

## License

MIT — see [LICENSE](LICENSE).
