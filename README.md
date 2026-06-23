# 🧭 AI Router Switch

> A transparent, self-hosted routing proxy that sits in front of **Claude Code** (and any
> Anthropic-format client) and lets you switch between **Claude** and **MiniMax** —
> with **automatic failover**, **context compression**, and **cross-model verification**.

**One endpoint (`:8787`), four modes, zero app restarts.** Switch model on the fly while
your IDE keeps running.

---

## ✨ Why this exists

When your Claude usage limit runs out, Claude Code just **stops**. Existing routers
(like `claude-code-router`) let you pick a model, but **none** combine all of this:

| Feature | claude-code-router | LiteLLM | **AI Router Switch** |
|---|---|---|---|
| Transparent to Claude Code | ✅ | ⚠️ | ✅ |
| **Automatic failover** on 429 | ❌ (open issue) | ✅ | ✅ |
| **Context compression** | ❌ | ❌ | ✅ (headroom) |
| **Cross-model verification** | ❌ | ❌ | ✅ |
| Per-project / per-chat isolation | partial | ❌ | ✅ |
| Self-hosted, no cloud markup | ✅ | ✅ | ✅ |

---

## 🏗️ Architecture

```
App (VSCode/Claude Code, CLI, any Anthropic client)
        │  ANTHROPIC_BASE_URL = http://127.0.0.1:8787
        ▼
   ai-router (:8787)  ──► mode selects the backend
        ├─ anthropic → headroom #1 (:8791) → api.anthropic.com
        └─ minimax   → headroom #2 (:8790) → api.minimax.io/anthropic
```

**Golden rule — Non-interference:** the router *only* picks the backend. It never
touches your model's settings, skills, agents, MCP, tools, or system prompt.
Compression is byte-faithful on everything that matters.

---

## 🎛️ Four modes

| Mode | Behaviour |
|---|---|
| `anthropic` | Only Claude |
| `minimax`   | Only MiniMax-M3 (cheap, no weekly limit) |
| `mixed`     | Claude first → automatic fallback to MiniMax on 429/5xx (bidirectional) |
| `interactive` | MiniMax generates, Claude Opus verifies *critical* tasks (auto-detected) |

Switch instantly — **no IDE restart**:

```bash
ai-mode minimax      # or: mixed / anthropic / interactive
ai-mode status
ai-mode log
```

---

## 🚀 Quick start

1. **Requirements:** Python 3.12+, `aiohttp`, a running
   [headroom](https://github.com/headroomlabs-ai/headroom) instance per backend,
   and API access to Claude and/or MiniMax.

2. **Point your client at the router:**
   ```jsonc
   // ~/.claude/settings.json
   { "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787" } }
   ```

3. **Run the router:**
   ```bash
   python3 src/ai-router-proxy.py
   # or install the systemd unit in systemd/
   ```

4. **Switch modes:** `ai-mode mixed`

> The MiniMax API key is read at runtime from an encrypted secret store — it is
> **never** stored in this repo.

---

## 🛡️ Resilience (triple defense)

- **systemd** units with `Restart=always`, `OOMScoreAdjust=-900`, linger
- **cron watchdog** (`scripts/ai-stack-guard.sh`) — restarts anything down, every minute + `@reboot`
- **SessionStart hook** — ensures the stack is up when your IDE starts

Tested: `kill -9` on all services → fully restored in <10s.

---

## 🗺️ Roadmap

- [x] **Phase 1** — deterministic per-port isolation (one mode per port)
- [x] **Phase 2** — per-chat independence via conversation fingerprint (no session-id needed)
- [x] **Phase 3** — in-chat commands (`!router minimax` + natural language)
- [x] **Phase 4** — circuit breaker with cooldown (bidirectional)

**All phases complete & tested** ✅

See [`docs/PIANO.md`](docs/PIANO.md) for the full design (44 decisions, technical notes).

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

*Built to keep coding when Claude says "usage limit reached." 🛟*
