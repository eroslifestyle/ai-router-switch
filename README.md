# 🧭 AI Router Switch

> A transparent, self-hosted routing proxy that sits in front of **Claude Code** (and any
> Anthropic-format client) and lets you switch between **Claude** and **MiniMax M3** —
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
```

**Golden rule — Non-interference:** the router *only* picks the backend. It never
touches your model's settings, skills, agents, MCP, tools, or system prompt.
Compression is byte-faithful on everything that matters.

---

## 🎛️ Modes

**Core (Claude + MiniMax):**

| Mode | Behaviour |
|---|---|
| `anthropic` | Only Claude |
| `minimax`   | Only MiniMax-M3 (cheap, no weekly limit) |
| `mixed`     | Claude first → automatic fallback to MiniMax on 429/5xx (bidirectional) |
| `inverse`   | MiniMax generates, Claude Opus verifies *critical* tasks (auto-detected) |

**GLM / z.ai** (Anthropic-compatible endpoint `api.z.ai/api/anthropic`, peak-cost aware):

| Mode | Behaviour |
|---|---|
| `glm`           | GLM-5.2 classifies task complexity → routes to the right GLM tier (`glm-5-turbo` → `glm-4.7` → `glm-5.2`), escalating on 2 failures |
| `glm-minimax`   | GLM-5.2 THINK → MiniMax ACT (streaming) → GLM verify on complex/agentic tasks |
| `anthropic-glm` | Claude (client's model) orchestrates → GLM tiered execution → Claude verifies critical (T2) tasks |

**Cost control (all GLM modes):** peak window `14:00–18:00 Asia/Shanghai` (≈ `08:00–12:00` Italy summer) — `glm-5.2`/`glm-5-turbo` cost 3× and are blocked in peak. Complex tasks fall back to Claude; simple ones use `glm-4.7` (not 3×). Off-peak: full tiering, 1× promo pricing until 2026-09-30. Fallback chain on error/quota: GLM → MiniMax → Claude.

Fixed-mode ports: `8771` anthropic · `8772` minimax · `8773` mixed · `8774` inverse · `8775` glm · `8776` glm-minimax · `8777` anthropic-glm. `8787` = dynamic (follows `ai-mode`).

Switch instantly — **no IDE restart**:

```bash
ai-mode glm          # or: glm-minimax / anthropic-glm / minimax / mixed / anthropic / inverse
ai-mode status
ai-mode log
```

GLM modes need a z.ai key: `export GLM_API_KEY=...` or `secrets.sh set glm.api_key ...`.

---

## 🚀 Quick start

1. **Requirements:** Python 3.12+, `aiohttp`, a running
   (one per upstream), and API access to Claude and/or MiniMax.

2. **Point your client at the router:**
   ```jsonc
   // ~/.claude/settings.json
   { "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787" } }
   ```

3. **Run the router:**
   ```bash
   python3 src/ai-router-proxy.py
   # or install the systemd unit in systemd/ai-router.service
   ```

4. **Switch modes:** `ai-mode mixed`

> The MiniMax API key is read at runtime from an encrypted secret store — it is
> **never** stored in this repo. The router also *does not inject* upstream
> credentials: it forwards whatever headers your client supplies (Anthropic
> bearer / `x-api-key`).

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

### 🔧 Hardening (audit 2026-06-23)
- Symlinked runtime proxy → `src/ai-router-proxy.py` (single source of truth, no drift).
- Aligned all systemd unit `Description=` fields with the real port (`:8787`).
- Triple-defense resilience verified: `kill -9` on every service → fully restored in <10s.

See [`docs/PIANO.md`](docs/PIANO.md) for the full design (44 decisions, technical notes).

---

## 💬 Community

- 💭 **[GitHub Discussions](https://github.com/eroslifestyle/ai-router-switch/discussions)** — ask questions, share setups, propose features
- 📚 **[Wiki](https://github.com/eroslifestyle/ai-router-switch/wiki)** — community-maintained guides, tutorials, deep-dives
- 🌐 **[GitHub Pages](https://eroslifestyle.github.io/ai-router-switch/)** — official documentation site (built from `docs/`)
- 🐛 **[Issues](https://github.com/eroslifestyle/ai-router-switch/issues)** — bug reports and feature requests

### 📖 Documentation

- 🇮🇹 [Manuale IT (HTML)](docs/manual-it.html) | [Markdown](docs/MANUAL.md)
- 🇬🇧 [English Manual (HTML)](docs/manual-en.html) | [Markdown](docs/MANUAL.en.md)
- 📊 [Competitor comparison](assets/competitor-comparison.svg)
- 🖼️ [Logo](assets/logo.svg) · [Square](assets/logo-square.svg) · [Banner](assets/banner.svg)

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

*Built to keep coding when Claude says "usage limit reached." 🛟*
