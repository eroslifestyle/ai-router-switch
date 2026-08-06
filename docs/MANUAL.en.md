# AI Router Proxy — Operational Guide

> Document version: 2026-07-26 · Project: [ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)

---

## Overview

AI Router Proxy is a **self-hosted** proxy that sits in front of Claude Code (and any
Anthropic-format client) and routes traffic to **Claude**, **MiniMax**, or **GLM/z.ai**
depending on the active mode.

The router is a **single Python/aiohttp process** listening on 7 ports:

| Port | Role |
|------|------|
| `8787` | Dynamic — follows `ai-mode` |
| `8771` | Forced: `anthropic` |
| `8772` | Forced: `minimax` |
| `8773` | Forced: `mix-am` |
| `8775` | Forced: `glm` |
| `8776` | Forced: `mix-gm` |
| `8777` | Forced: `mix-ag` |

*(port `8774` served the `inverse` mode, removed on 2026-07-26)*

**Golden rule:** the router selects the backend. It never touches model settings,
skills, agents, MCP, tools, or system prompt.

**The router is a transparent tunnel.** It orchestrates no phases and holds no state:
it looks at which model is requested and which mode is active, rewrites the `model`
field, and forwards. The THINK/ACT/VERIFY hierarchy and escalation live in the client
configuration, not here. The map is a data table in `src/role_routing.py`.

---

## The Seven Modes

Each mode is a pair of destinations: one for the model that **thinks** (THINK) and
one for the model that **executes** (ACT). The router infers the role from the
incoming model name — `claude-opus`/`claude-sonnet`/`claude-fable` are THINK,
`claude-haiku` is ACT — and forwards to the matching provider.

**VERIFY has no route of its own**: it is always performed by the same model that did
the THINK, so the verification request carries that model's name and naturally lands
on the THINK route. In mixed modes this means *the one who verifies is never the one
who executed*.

| Mode | THINK | ACT | Legacy alias accepted |
|---|---|---|---|
| `anthropic` | Anthropic | Anthropic (Haiku) | — |
| `minimax` | MiniMax-M3 | MiniMax-M2.7 | — |
| `glm` | glm-5.2 | glm-4.7 | — |
| `mix-am` | Anthropic | MiniMax-M2.7 | `mixed` |
| `mix-ag` | Anthropic | glm-4.7 | `anthropic-glm` |
| `mix-gm` | glm-5.2 | MiniMax-M2.7 | `glm-minimax` |

Source: `ROUTING_TABLE` in `src/role_routing.py` (pure function, covered by 48 tests).
Legacy aliases are accepted by `ai-mode`, which **always** writes the canonical name
to the state file.

### 1. `anthropic` — Pure Claude

Everything goes to `api.anthropic.com`, both THINK and ACT. The router does not
rewrite the model name: Anthropic negotiates the version server-side.

**Use when:** you need Claude and nothing else.

### 2. `minimax` — Pure MiniMax

Everything goes to `api.minimaxi.chat/anthropic` (MiniMax **Anthropic-compatible**
endpoint). M3 thinks, M2.7 executes.

**Use when:** simple tasks, limited budget, no weekly limit.

### 3. `mix-am` — Claude thinks, MiniMax executes

THINK goes to Anthropic, ACT to MiniMax-M2.7. This is the everyday mixed mode:
planning and verification on Claude, low-cost execution on MiniMax.

Accepts the historical alias `mixed`.

**Use when:** production — quality on reasoning, contained cost on execution.

### 4. `glm` — GLM/z.ai with tiering

The GLM model is decided by role, not by a complexity classifier:

| Role | Model | Notes |
|------|-------|-------|
| THINK | `glm-5.2` | Orchestration: classifies, plans, verifies |
| ACT | `glm-4.7` | Execution |

**Peak cost control:** window `14:00–18:00 Asia/Shanghai` (~08:00–12:00 Italy summer).
In peak, `glm-5.2` and `glm-5-turbo` cost 3× and are automatically downgraded to `glm-4.7` by the router; in that window THINK also runs on `glm-4.7`. The downgrade affects the `glm` and `mix-gm` modes, the only ones routing `glm-5.2`. The downgrade is recorded in the log with the `GLM peak-cap` prefix.
Off-peak: no downgrade, 1× promo pricing until 2026-09-30.

**Escalation:** stays on the GLM ladder. Neither MiniMax nor Anthropic steps in here.

**Use when:** you want GLM as the primary backend.

### 5. `mix-gm` — GLM thinks, MiniMax executes

- **glm-5.2** does THINK and VERIFY
- **MiniMax-M2.7** executes

Accepts the historical alias `glm-minimax`.
**Execution escalation:** `M2.7 → M3 → GLM`, **never** Anthropic.

**Use when:** GLM reasoning with MiniMax speed and cost, keeping Claude out of the loop.

### 6. `mix-ag` — Claude thinks, GLM executes

- **Claude** does THINK and VERIFY
- **glm-4.7** executes

Accepts the historical alias `anthropic-glm`.

**Use when:** Claude as the orchestrator, GLM for low-cost execution.

---

### 7. `qwen` — Pure Qwen (Alibaba Model Studio)

- **qwen3.8-max** does THINK and VERIFY
- **qwen3-coder-plus** executes

PURE mode: both THINK and ACT stay on Qwen — Anthropic, MiniMax and GLM never step in.

Anthropic-compatible endpoint on the workspace-dedicated host, `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`, authenticating with `x-api-key`. The base URL ends with `/apps/anthropic` WITHOUT `/v1`. The native DashScope services, under `/api/v1/...`, require `Authorization: Bearer` instead.

Key: `secrets.sh get qwen.api_key`. Fixed port: `8778`. Dedicated guide: `docs/MODALITA-QWEN.md`.

**Use when:** very long contexts at low cost — `qwen3-coder-plus` declares 1,048,576 input tokens.

---

## Switching Modes

### Dynamic port 8787

Port `8787` reads `~/.claude/ai-router-mode` on every request.
To change mode on the fly:

```bash
ai-mode anthropic
ai-mode minimax
ai-mode mix-am          # aliases: mixam, mixed
ai-mode mix-ag          # aliases: mixag, anthropic-glm
ai-mode mix-gm          # aliases: mixgm, glm-minimax
ai-mode glm
ai-mode status
ai-mode log
```

Or manually:

```bash
echo "minimax" > ~/.claude/ai-router-mode
echo "anthropic" > ~/.claude/ai-router-mode
```

**Propagation:** takes ~2 seconds (aiohttp keeps persistent connections).

### In-Chat Commands

During a conversation you can send commands **isolated to that chat** (not global).
The proxy identifies the conversation from the Claude Code session fingerprint.

```
!router anthropic      # switch to pure Claude for this chat
!router minimax        # switch to MiniMax for this chat only
!router mixam          # Claude thinks + MiniMax executes
!router mixag          # Claude thinks + GLM executes
!router mixgm          # GLM thinks + MiniMax executes
!router glm            # GLM for this chat
!router status         # show current mode and backend status
!router reset          # restore global mode from ai-mode
!router help           # inline help
```

**Accepted arguments:** the 6 canonical names plus the aliases
`mixam`/`mixag`/`mixgm`. Any other argument — including the legacy `mixed`,
`glm-minimax`, `anthropic-glm` that `ai-mode` does accept — replies with the help
text and **changes nothing**.

> **Voice switching removed on 2026-07-26.** The proxy also recognised natural-language
> phrases (*"usa solo claude"*), but it switched modes **without authorisation**: a
> short message with a common verb plus a mode word anywhere in the text was enough,
> so ordinary working sentences silently switched the chat. Explicit `!router` is now
> the only switch available from chat.

**Scope:** the command changes mode only for that conversation.
**Important:** `!router` is handled by the proxy `:8787` — these messages travel
through to the proxy, which intercepts them. I do not respond to them.

### Fixed Ports

To force a mode without modifying files or using in-chat commands,
point directly to the fixed port:

```bash
# Pure Claude session
export ANTHROPIC_BASE_URL=http://127.0.0.1:8771

# Pure MiniMax session
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772

# Mixed session: Claude thinks, MiniMax executes
export ANTHROPIC_BASE_URL=http://127.0.0.1:8773

# GLM sessions
export ANTHROPIC_BASE_URL=http://127.0.0.1:8775   # glm
export ANTHROPIC_BASE_URL=http://127.0.0.1:8776   # mix-gm
export ANTHROPIC_BASE_URL=http://127.0.0.1:8777   # mix-ag
```

---

## Health Check

```bash
# Main endpoint
curl http://127.0.0.1:8787/__router_health

# Example response:
# {
#   "service": "ai-router-proxy",
#   "mode": "mixed",
#   "port_role": "dynamic",
#   "version": "...",
#   "backends": { "anthropic": "up", "minimax": "up" }
# }

# Prometheus metrics
curl http://127.0.0.1:8787/metrics
curl http://127.0.0.1:8787/stats

# Kubernetes-compatible endpoints
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/readyz
curl http://127.0.0.1:8787/livez
```

---

## Usage Examples

### Claude Code — basic

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
```

Claude Code automatically uses the mode set by `ai-mode`.

### Parallel sessions with different backends

```bash
# Terminal 1 (VSCode): always Claude
export ANTHROPIC_BASE_URL=http://127.0.0.1:8771

# Terminal 2: always MiniMax
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772

# Terminal 3: always GLM
export ANTHROPIC_BASE_URL=http://127.0.0.1:8775
```

Sessions operate independently without interference.

### Reasoning on Claude, cheap execution

```bash
ai-mode mix-am
```

THINK and VERIFY stay on Claude, execution goes to MiniMax-M2.7. If the executor
fails repeatedly, escalation climbs the Anthropic tiers (Sonnet → Opus → Fable) —
the thinking model never changes on its own.

### Keeping Claude out of the loop

```bash
ai-mode mix-gm
```

glm-5.2 thinks and verifies, MiniMax-M2.7 executes. Escalation stays on
`M2.7 → M3 → GLM`: Anthropic is never involved.

---

## GLM — API Key

Modes `glm`, `mix-gm`, `mix-ag` require a z.ai key.

```bash
export GLM_API_KEY=...
# or
secrets.sh set glm.api_key <value>
```

Without the key, GLM modes return a 500 error with an explicit message.
All other modes continue to work normally.

---

## Hardening and Resilience

### Triple Defense

1. **systemd** — `ai-router-proxy.service` with `Restart=always`,
   `OOMScoreAdjust=-900`, linger enabled.

2. **Cron watchdog** — `scripts/ai-stack-guard.sh` runs every 60 seconds to verify
   all 8 ports are listening. If one is down and systemd hasn't restarted it within
   4 seconds, it relaunches via nohup.

3. **SessionStart hook** — verifies the stack is up when the IDE starts.

Tested: `kill -9` on all services → full restore in <10 seconds.

### What NOT to Do

- **Don't kill** the service without an immediate recovery plan
- **Don't edit** systemd unit files manually without understanding the consequences
- **Don't point** directly to `:8790` or `:8791` — always use `:8787` or fixed ports
- **Don't change** mode in production without first trying it on a fixed port
- **Don't ignore** watchdog alarms

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| All responses 401 | Anthropic key expired/absent | Switch to `minimax` or `glm`, or update secrets |
| Mode doesn't change | Persistent connections (~2s) | Wait 2 seconds |
| GLM mode returns 500 | `GLM_API_KEY` not set | `export GLM_API_KEY=...` |
| `AIROUTER_DEBUG_TOKEN` | — | credentials for the `/debug/` routes, required only when not listening on loopback |

**The /debug/ routes and network exposure**: routes prefixed with `/debug/` return the contents of the requests passing through the router, and in particular `/debug/trace` includes the full body of the last request forwarded to the upstream, that is system prompt and conversation, while `/debug/errors` returns up to 2000 characters of the upstream error body. As long as `AIROUTER_LISTEN_HOST` stays on loopback those routes are not reachable from the network and stay open. As soon as a non-loopback address is set, the router requires `AIROUTER_DEBUG_TOKEN`: with no token configured every `/debug/` route answers 404, with the token configured it is presented in the `X-Airouter-Debug-Token` header, as `Authorization: Bearer <token>`, or as the `?token=` query parameter. The `/__router_health` route is not affected. The same guard also covers routes prefixed with `/admin/`, including `/admin/mode/<mode>` which rewrites the router's global mode; without it, a network-exposed router would let anyone hijack the mode of all chats. The guard is an aiohttp middleware in `src/router_debug.py`, covered by `sviluppo/tests/test_debug_auth.py`.
| Proxy doesn't respond | Service not started | `systemctl --user start ai-router.service` |
| `!router <mode>` replies with help | Unrecognised argument (e.g. `inverse`, removed on 2026-07-26) | Use one of the seven canonical names, the `mixam`/`mixag`/`mixgm` aliases, or the historical `mixed`/`glm-minimax`/`anthropic-glm`, accepted and normalised since 2026-08-04 |
| Hand-written mode not applied | The state file only accepts the 6 canonical names | Use `ai-mode`, which normalises aliases |

### Debug

```bash
# Service status
systemctl --user status ai-router-proxy.service

# Listening ports
ss -tlnp | grep -E '877[1-7]|8787'

# Recent logs
journalctl --user -u ai-router-proxy.service -n 50

# Health endpoint
curl http://127.0.0.1:8787/__router_health
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AIROUTER_PORT` | `8787` | Base port |
| `AIROUTER_ANTHROPIC_UPSTREAM` | `http://127.0.0.1:8791` | Anthropic backend |
| `AIROUTER_MINIMAX_UPSTREAM` | `http://127.0.0.1:8790` | MiniMax backend |
| `AIROUTER_LISTEN_HOST` | `127.0.0.1` | Listen interface |
| `AIROUTER_MINIMAX_MODEL` | `MiniMax-M2.7` | MiniMax model for ACT |
| `AIROUTER_NEW_PIPELINE` | `1` | Enables the current routing path |
| `AIROUTER_TRANSITION_FILTERS` | `1` | MiniMax transition filters (systemd drop-in) |
| `GLM_API_KEY` | — | z.ai key for GLM modes |

Checked one by one against the source on 2026-07-26. `AIROUTER_MIXED_PRIMARY` and
`AIROUTER_VERIFY_MODEL` were dropped from this table: **neither has a reader in the
codebase**, both were leftovers from pipelines that no longer exist.

---

## Relevant Files

```
src/
  ai-router-proxy.py     # Main proxy
  glm_backend.py         # GLM backend (defensive import)
  peak_scheduler.py      # Peak scheduler for GLM

scripts/
  ai-mode                # CLI helper for mode switching
  ai-stack-guard.sh      # Cron watchdog

sviluppo/
  tests/
    test_glm_modes.sh    # Isolation test for GLM modes
```

---

## Support

When reporting issues, include:

1. Output of `systemctl --user status ai-router-proxy.service`
2. Output of `curl http://127.0.0.1:8787/__router_health`
3. Last 50 lines of `journalctl --user -u ai-router-proxy.service`
4. Contents of `~/.claude/ai-router-mode`
5. Relevant environment variables (exclude API keys)
