# AI Router Proxy — Operational Guide

> Document version: 2026-07-26 · Project: [ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)

---

## Overview

AI Router Proxy is a **self-hosted** proxy that sits in front of Claude Code (and any
Anthropic-format client) and routes traffic to **Claude**, **MiniMax**, or **GLM/z.ai**
depending on the active mode.

The router is a **single Python/aiohttp process** listening on 16 ports (1 dynamic + 15 fixed):

| Port | Role |
|------|------|
| `8787` | Dynamic — follows `ai-mode` |
| `8771` | Forced: `anthropic` |
| `8772` | Forced: `minimax` |
| `8773` | Forced: `mix-am` |
| `8774` | Forced: `mix-al` |
| `8775` | Forced: `glm` |
| `8776` | Forced: `mix-gm` |
| `8777` | Forced: `mix-ag` |
| `8778` | Forced: `qwen` |
| `8779` | Forced: `local` |
| `8781` | Forced: `mix-am-2` |
| `8784` | Forced: `mix-gm-2` |
| `8785` | Forced: `mix-ag-2` |
| `8786` | Forced: `gpt` |
| `8788` | Forced: `ultra` |
| `8789` | Forced: `opr` |

*(port `8774` served the `inverse` mode, removed on 2026-07-26; since 2026-08-04 it serves `mix-al`)*

**Golden rule:** the router selects the backend. It never touches model settings,
skills, agents, MCP, tools, or system prompt.

**The router is a transparent tunnel.** It orchestrates no phases and holds no state:
it looks at which model is requested and which mode is active, rewrites the `model`
field, and forwards. The THINK/ACT/VERIFY hierarchy and escalation live in the client
configuration, not here. The map is a data table in `src/role_routing.py`.

### Installation

Requirements: Python 3.10 or later. Runtime dependencies are `aiohttp`, `brotli`, `multidict`, and `Pillow` (see requirements.txt). `PySide6` is needed only for the optional GUI panel.

Steps:

1. Clone the repository.
2. Run `python3 install.py`.

The installer checks Python and the dependencies, creates the configuration directory, copies `.env.example` without ever overwriting an existing `.env`, and registers a service: a user systemd unit on Linux, a launchd plist on macOS, a startup-file plus instructions on Windows.

Options: `--dry-run`, `--no-service`, `--start`, `--yes`.

Then set `"ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"` in Claude Code's settings.json. A ready-to-use fragment is in `config/settings.anthropic.example.json`.

**Optional — hierarchy discipline for Claude Code:** if you want Claude Code itself to respect the THINK/ACT split the router is built for (never a subagent without explicit `model`, never the planning model writing project code directly in mixed modes), paste the ready-made prompt in `docs/claude-hierarchy/README.md` into a fresh Claude Code session.

---

## The Fifteen Modes

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
| `qwen` | qwen3.8-max | qwen3-coder-plus | — |
| `mix-al` | Anthropic | local (code-max) | — |
| `local` | local (code-max) | local (code-max) | — |
| `gpt` | local (code-max) | local (code-max) | — |
| `opr` | OpenRouter/ox-alpha | OpenRouter/ox-alpha | — |
| `ultra` | Anthropic | GLM (MiniMax for code via CLI) | — |
| `mix-am-2` | Anthropic | MiniMax-M2.7 | — |
| `mix-ag-2` | Anthropic | glm-4.7 | — |
| `mix-gm-2` | glm-5.2 | MiniMax-M2.7 | — |

**`-2` variants** (mix-am-2, mix-gm-2, mix-ag-2): identical routing to the base mode, but with stricter "deny" enforcement on delegation via the `enforce_hierarchy.py` hook. MiniMax is not reachable from the router in `ultra` mode — only direct CLI tools `m3-code`/`m3x` invoke it, bypassing the proxy.

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

### 8. `mix-al` — Claude thinks, the local model executes

- **Fable 5 / Opus 5 / Sonnet 5** do THINK and VERIFY
- **code-max**, local, executes

THINK and VERIFY stay on Anthropic (Fable 5, Opus 5 or Sonnet 5, chosen by the user); only the ACT goes to the local `code-max` model. The "local" provider has no THINK model of its own, so in `mix-al` the cognitive phase cannot leave Anthropic.

When local execution fails, the escalation climbs back up the Anthropic tiers: Sonnet → Opus → Fable.

Fixed port: `8774`. It was the port of `inverse`, a mode removed on 2026-07-26; on 2026-08-04 it was reassigned to `mix-al`.

The local model is exposed via LiteLLM, which speaks the native Anthropic protocol on `/v1/messages`. Key and base URL are read from `LOCAL_LLM_API_KEY` and `LOCAL_LLM_API_BASE`; if missing, the router loads `secrets/local-llm.env`. Timeout: `AIROUTER_LOCAL_TIMEOUT_SEC`, default 240 seconds. Retry: maximum 2.

**Use:** zero-cost execution with code that never leaves the machine, orchestration and reasoning on Claude.

---

### 9. `local` — pure local model

- **code-max** does THINK and VERIFY
- **code-max** executes

PURE mode: both THINK and ACT go to the local `code-max` model; neither Anthropic, nor MiniMax, nor GLM step in.

Fixed port: `8779`. Same keys (`LOCAL_LLM_API_KEY` / `LOCAL_LLM_API_BASE`, fallback `secrets/local-llm.env`), same timeout (`AIROUTER_LOCAL_TIMEOUT_SEC`, default 240 s) and same 2 retries as `mix-al`.

The router accepts only `code-max`: any other requested model is folded back to that one. Before forwarding, it appends a system hint to the prompt.

**Use:** work fully offline, with no data leaving the machine.

---

### 10. `gpt` — single local model

- **code-max** does THINK, VERIFY, and execution

Fixed port: `8786`. Like `local`, the router accepts only `code-max`; any other requested model is folded back. No separate THINK model: purely local wrapper.

**Use:** completely local sandbox, no external provider dependencies.

---

### 11. `opr` — OpenRouter/ox-alpha pure

- **OpenRouter/ox-alpha** does THINK, VERIFY, and execution

Fixed port: `8789`. Pure sandbox mode for experimenting with OpenRouter. Key: `secrets.sh get opr.api_key`.

**Use:** test OpenRouter models with the same Anthropic format.

---

### 12. `ultra` — THREE providers (ONLY mode with this feature)

- **Claude** (Fable 5 / Opus 5 / Sonnet 5) does THINK and VERIFY
- **glm-4.7** does ACT (exploration, reading, analysis)
- **MiniMax for CODE only via direct CLI** (`m3-code`/`m3x`, bypassing the proxy)

Fixed port: `8788`. Born for massive tasks that exhaust Anthropic quota: context re-sent each turn costs far more than code written. MiniMax is not reachable from the router in this mode — `resolve_route("ultra", "MiniMax...")` always returns GLM, by design.

**Use:** massive tasks with Anthropic thinking (1M window), GLM cheap execution, MiniMax code via CLI.

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

**Accepted arguments:** the 9 canonical names plus the aliases
`mixam`/`mixag`/`mixgm`. Any other argument — including the legacy `mixed`,
`glm-minimax`, `anthropic-glm` that `ai-mode` does accept — replies with the help
text and **changes nothing**.

**From the terminal, for a single session**

```bash
scripts/router qwen   # sets qwen only for this session
scripts/router        # lists the accepted modes
```

The command exists because a leading exclamation mark is intercepted by the CLI shell and `!router …` never reaches the proxy; the session is identified by the `CLAUDE_CODE_SESSION_ID` environment variable and, if it is missing, the command reports it and suggests `ai-mode` for the global switch. Unlike `ai-mode`, `scripts/router` does not touch the global mode file, so it does not shift the other chats.

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

Test suite: 930 tests collected by `python3 -m pytest -q --collect-only`. Run with `python3 -m pytest -q`.

### What NOT to Do

- **Don't kill** the service without an immediate recovery plan
- **Don't edit** systemd unit files manually without understanding the consequences
- **Direct endpoints**: do not point applications directly at the provider endpoints; always use port `8787` or one of the fixed per-mode ports, otherwise routing is bypassed.
- **Don't change** mode in production without first trying it on a fixed port
- **Don't ignore** watchdog alarms

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| All responses 401 | Anthropic key expired/absent | Switch to `minimax` or `glm`, or update secrets |
| Mode doesn't change | Persistent connections (~2s) | Wait 2 seconds |
| GLM mode returns 500 | `GLM_API_KEY` not set | `export GLM_API_KEY=...` |
| Proxy doesn't respond | Service not started | `systemctl --user start ai-router.service` |
| `!router <mode>` replies with help | Unrecognised argument (e.g. `inverse`, removed on 2026-07-26) | Use one of the nine canonical names, the `mixam`/`mixag`/`mixgm` aliases, or the historical `mixed`/`glm-minimax`/`anthropic-glm`, accepted and normalised since 2026-08-04 |
| Hand-written mode not applied | The state file only accepts the 9 canonical names | Use `ai-mode`, which normalises aliases |

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
| `AIROUTER_LISTEN_HOST` | `127.0.0.1` | Listen interface |
| `AIROUTER_ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Anthropic endpoint |
| `AIROUTER_MINIMAX_UPSTREAM` | `https://api.minimaxi.chat/anthropic` | MiniMax endpoint |
| `AIROUTER_MINIMAX_MODEL` | `MiniMax-M3` | Default MiniMax target model |
| `AIROUTER_TRANSITION_FILTERS` | `0` | MiniMax transition filters; the project systemd unit sets it to 1 |
| `GLM_API_KEY` | — | z.ai key for GLM modes |
| `AIROUTER_DEBUG_TOKEN` | — | Credentials for `/debug/` and `/admin/` routes, required only if not listening on loopback |
| `AIROUTER_MINIMAX_CONTEXT_LIMIT` | `750000` | Request context limit in BYTES, not tokens |
| `AIROUTER_NON_STREAM_SOCK_READ_SEC` | `600` | Read ceiling for non-streaming responses; streaming does not use it |
| `AIROUTER_MINIMAX_SEMAPHORE` | `8` | Max concurrent requests to MiniMax (see also `AIROUTER_GLM_SEMAPHORE`, `AIROUTER_QWEN_SEMAPHORE`) |
| `AIROUTER_LOCAL_TIMEOUT_SEC` | `240` | Local backend timeout, used by the `local` and `mix-al` modes |
| `AIROUTER_TOOLS_TELEMETRY` | `0` | Measures the weight of each request's `tools` block and records it in the sidecar |
| `AIROUTER_CATALOG_PATH` | — | Relocates `BUG-CATALOG.jsonl`; the test suite points it at a tmpdir so it never writes to the production one |
| `AIROUTER_DEEP_DEBUG` | `0` | Extended diagnostics on the hot path |

**The /debug/ routes and network exposure**: routes prefixed with `/debug/` return the contents of the requests passing through the router, and in particular `/debug/trace` includes the full body of the last request forwarded to the upstream, that is system prompt and conversation, while `/debug/errors` returns up to 2000 characters of the upstream error body. As long as `AIROUTER_LISTEN_HOST` stays on loopback those routes are not reachable from the network and stay open. As soon as a non-loopback address is set, the router requires `AIROUTER_DEBUG_TOKEN`: with no token configured every `/debug/` route answers 404, with the token configured it is presented in the `X-Airouter-Debug-Token` header, as `Authorization: Bearer <token>`, or as the `?token=` query parameter. The `/__router_health` route is not affected. The same guard also covers routes prefixed with `/admin/`, including `/admin/mode/<mode>` which rewrites the router's global mode; without it, a network-exposed router would let anyone hijack the mode of all chats. The guard is an aiohttp middleware in `src/router_debug.py`, covered by `sviluppo/tests/test_debug_auth.py`.

Checked one by one against the source on 2026-08-06, reading the values from the
module rather than from memory: three defaults were stale (both upstreams still
pointed at `127.0.0.1:8791` and `127.0.0.1:8790`, which no longer appear in the
code, and the MiniMax model was still `MiniMax-M2.7`). For
`AIROUTER_TRANSITION_FILTERS` the column said `1`, which is the value the systemd
unit forces, not the code default, which is `0`.

On 2026-08-07 `AIROUTER_MIXED_EXECUTOR` was dropped too, for the same reason: the
executor of the mixed modes is decided by `role_routing.resolve_route`, and the
constant reading that variable had been left with no readers. This table lists the
variables in common use, not all of them: the exhaustive list is the code itself,
reachable with `grep -rn AIROUTER_ src/`.

`AIROUTER_MIXED_PRIMARY`, `AIROUTER_VERIFY_MODEL` and, since 2026-08-06,
`AIROUTER_NEW_PIPELINE` were dropped from this table: **none of the three has a
reader in the codebase**, all were leftovers from pipelines that no longer exist.

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
