# AI Router Proxy — Operational Guide

> Document version: 2026-07-14 · Project: [ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)

---

## Overview

AI Router Proxy is a **self-hosted** proxy that sits in front of Claude Code (and any
Anthropic-format client) and routes traffic to **Claude**, **MiniMax**, or **GLM/z.ai**
depending on the active mode.

The router is a **single Python/aiohttp process** listening on 8 ports:

| Port | Role |
|------|------|
| `8787` | Dynamic — follows `ai-mode` |
| `8771` | Forced: `anthropic` |
| `8772` | Forced: `minimax` |
| `8773` | Forced: `mixed` |
| `8774` | Forced: `inverse` |
| `8775` | Forced: `glm` |
| `8776` | Forced: `glm-minimax` |
| `8777` | Forced: `anthropic-glm` |

**Golden rule:** the router selects the backend. It never touches model settings,
skills, agents, MCP, tools, or system prompt.

---

## The Seven Modes

### 1. `anthropic` — Pure Claude

All traffic goes to `api.anthropic.com`. No fallback. If the backend returns an
error, the error is passed through unchanged.

**Use when:** you need Claude and nothing else.

### 2. `minimax` — Pure MiniMax

All traffic goes to `api.minimaxi.chat/anthropic` (MiniMax **Anthropic-compatible**
endpoint). No fallback. MiniMax-M3 orchestrates, M2.7 executes.

**Use when:** simple tasks, limited budget, no weekly limit.

### 3. `mixed` — Bidirectional Fallback

Attempts the primary backend (`ai-mode` or env `AIROUTER_MIXED_PRIMARY`, default:
`anthropic`). If the response has a status in `FALLBACK_STATUSES`, automatically
retries on the secondary backend.

**Statuses triggering Anthropic→MiniMax fallback:**
`401, 403, 408, 409, 413, 429, 500, 502, 503, 504, 529`

**Statuses triggering MiniMax→Anthropic fallback:**
`401, 403, 408, 409, 413, 500, 502, 503, 504, 529`
*(429 is excluded because MiniMax handles its own rate limit internally)*

**Note:** status `400` and `404` do not trigger fallback (client errors, not backend).

**Use when:** production with service continuity even if a subscription expires.

### 4. `inverse` — MiniMax generates, Claude verifies

Generation always from MiniMax. For **T2** tasks (classified as critical/complex),
an additional verification pass by Claude Opus runs before delivering the response.

**T2 classification** (heuristic on system prompt):
keywords such as `"critically"`, `"security"`, `"audit"`, `"vulnerability"`,
`"analyze"`, `"explain in detail"`, `"find issues"`, `"architect"`, `"review"`.

If Claude Opus is unavailable, the MiniMax response is still returned with flag
`"unverified"`.

**Use when:** cost-saving with verification on critical tasks.

### 5. `glm` — GLM/z.ai with tiering

GLM-5.2 classifies task complexity → routes to the most appropriate tier:

| Tier | Model | Condition |
|------|-------|-----------|
| High | `glm-5-turbo` | Complex tasks, off-peak |
| Low | `glm-4.7` | Simple tasks |
| Top | `glm-5.2` | Off-peak, complex tasks not resolved |

**Peak cost control:** window `14:00–18:00 Asia/Shanghai` (~08:00–12:00 Italy summer).
In peak, `glm-5.2`/`glm-5-turbo` cost 3× and are blocked for complex tasks →
complex tasks fall back to Claude, simple ones use `glm-4.7`.
Off-peak: full tiering, 1× promo pricing until 2026-09-30.

**Fallback chain:** GLM → MiniMax → Claude.

**Use when:** you want GLM as the primary backend.

### 6. `glm-minimax` — GLM thinks, MiniMax acts

- **GLM-5.2** generates the reasoning (THINK)
- **MiniMax** executes (ACT, streaming)
- For complex/agentic tasks: **GLM verifies** the result

**Use when:** combining GLM's reasoning with MiniMax's speed and cost.

### 7. `anthropic-glm` — Claude orchestrates, GLM executes, Claude verifies T2

- **Claude** (user's model) orchestrates and classifies
- **GLM** executes with tiering (`glm-5-turbo` → `glm-4.7` → `glm-5.2`)
- **T2** (critical/complex) tasks: **Claude verifies** before delivery

**Fallback chain:** GLM → MiniMax → Claude.

**Use when:** Claude is the primary orchestrator but you want to leverage GLM for execution.

---

## Switching Modes

### Dynamic port 8787

Port `8787` reads `~/.claude/ai-router-mode` on every request.
To change mode on the fly:

```bash
ai-mode minimax         # most convenient way
ai-mode anthropic
ai-mode mixed
ai-mode inverse
ai-mode glm
ai-mode glm-minimax
ai-mode anthropic-glm
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
!router minimax        # switch to MiniMax for this chat only
!router anthropic      # switch to pure Claude for this chat
!router mixed          # fallback for this chat
!router inverse        # inverse mode for this chat
!router glm            # GLM for this chat
!router glm-minimax
!router anthropic-glm
!router status         # show current mode and backend status
!router reset          # restore global mode from ai-mode
!router help           # inline help
```

The proxy also understands natural phrases like `"usa solo claude"` or
`"passa a minimax"`.

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

# Mixed session
export ANTHROPIC_BASE_URL=http://127.0.0.1:8773

# Inverse session
export ANTHROPIC_BASE_URL=http://127.0.0.1:8774

# GLM sessions
export ANTHROPIC_BASE_URL=http://127.0.0.1:8775   # glm
export ANTHROPIC_BASE_URL=http://127.0.0.1:8776   # glm-minimax
export ANTHROPIC_BASE_URL=http://127.0.0.1:8777   # anthropic-glm
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

### Automatic failover

```bash
ai-mode mixed
```

If Claude is down, the subscription expired, or the rate limit is hit,
the router automatically switches to MiniMax.

### Smart saving with verification

```bash
ai-mode inverse
```

MiniMax generates for all tasks. Critical tasks (T2) are verified by Claude Opus
before delivery.

---

## GLM — API Key

Modes `glm`, `glm-minimax`, `anthropic-glm` require a z.ai key.

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
- **Don't change** mode in production without first testing in `mixed`
- **Don't ignore** watchdog alarms

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| All responses 401 | Anthropic key expired/absent | Use `mixed` or update secrets |
| Mode doesn't change | Persistent connections (~2s) | Wait 2 seconds |
| GLM mode returns 500 | `GLM_API_KEY` not set | `export GLM_API_KEY=...` |
| Proxy doesn't respond | Service not started | `systemctl --user start ai-router-proxy.service` |
| `mixed` doesn't fallback | Status is 400 or 404 (client errors, not backend) | Check the request |

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
| `AIROUTER_MIXED_PRIMARY` | `anthropic` | Primary backend in mixed |
| `AIROUTER_MINIMAX_MODEL` | `MiniMax-M3` | MiniMax model |
| `AIROUTER_VERIFY_MODEL` | `claude-opus-4-8` | Verify model in inverse |
| `GLM_API_KEY` | — | z.ai key for GLM modes |

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
