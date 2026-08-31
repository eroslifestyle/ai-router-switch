# Reliability, cache, context & governance — engineering history

This document exists because running `ai-router-switch` well is not just
"start the proxy and pick a mode": prompt-caching, per-model context
windows, per-provider rate limits, and tool-schema bloat all interact in
ways that are easy to get wrong and expensive when you do — and the proxy
itself is deliberately a "transparent tunnel" (it maps a requested model
name to a provider and forwards; it does not orchestrate, does not retry
across providers, does not enforce who calls it or how). That design choice
is correct for the proxy, but it means a piece of this system — the
discipline that decides *when* your agentic client should call the
orchestrator model versus the mode's execution model — has to live on the
client side. This document covers both halves: the router's own history of
incidents (all fixed, all verified against `git log` on this checkout,
commit `7e73398`, 2026-08-30) and a client-side governance pattern that
makes the router's mode-switching actually change where execution happens.

Everything below covers **all 15 modes** (`anthropic`, `minimax`, `glm`,
`qwen`, `local`, `mix-am`, `mix-am-2`, `mix-ag`, `mix-ag-2`, `mix-gm`,
`mix-gm-2`, `mix-al`, `ultra`, `gpt`, `opr` — see `VALID_MODES` in
`src/router_constants.py`, the only source of truth; this list has grown
over time and any document, including this one, can go stale — check the
constant, not a table), not just GLM. None of the numbers below are
estimates: every one is cited against its source file or its audit report.

## Mode reference

| Port | Mode | THINK | ACT (execution) | Notes |
|---|---:|---|---|---|
| 8771 | `anthropic` | Anthropic (Fable/Opus/Sonnet) | Anthropic (Haiku) | pure Anthropic |
| 8772 | `minimax` | MiniMax-M3 | MiniMax-M2.7 | pure MiniMax |
| 8773 | `mix-am` | Anthropic | MiniMax-M2.7 | mixed |
| 8781 | `mix-am-2` | Anthropic | MiniMax-M2.7 | mixed, aggressive delegation (see below) |
| 8774 | `mix-al` | Anthropic | local (`code-max`, via LiteLLM on :4000) | mixed |
| 8775 | `glm` | GLM-5.3 | GLM-4.7 | pure GLM (z.ai) |
| 8776 | `mix-gm` | GLM-5.3 | MiniMax-M2.7 | mixed |
| 8784 | `mix-gm-2` | GLM-5.3 | MiniMax-M2.7 | mixed, aggressive delegation |
| 8777 | `mix-ag` | Anthropic | GLM-4.7 | mixed — **known degraded, see incident below** |
| 8785 | `mix-ag-2` | Anthropic | GLM-4.7 | mixed, aggressive delegation |
| 8778 | `qwen` | qwen3.7-max | qwen3-coder-plus | pure Qwen (Alibaba Model Studio) — **known degraded, see incident below** |
| 8779 | `local` | local | local | pure local, same model both roles |
| 8786 | `gpt` | local (`code-max`) | local (`code-max`) | sandbox: pure local without touching the live `:8787` mode |
| 8788 | `ultra` | Anthropic | GLM-4.7, **plus MiniMax for code specifically via the `m3-code`/`m3x` CLIs (bypass the proxy)** | the only 3-provider mode |
| 8789 | `opr` | OpenRouter | OpenRouter | sandbox, pure OpenRouter |
| 8787 | — | — | — | the live port; follows whichever mode is currently active, not fixed to one |

Ports **8780, 8782, 8783 are not usable** — occupied by unrelated services on
the reference deployment (a TTS service and another `uvicorn` instance). The
router silently skips a failed bind on startup rather than crashing, which
means a mode assigned to an occupied port will 404 instead of erroring
loudly — if you add a mode, verify the port is actually free with a raw
`socket.bind()` before assigning it, not by reading this table.

## Model & rate-limit catalog

Context windows and output caps, from `src/model_context_map.py` and each
backend's own limiter module. Every value is either sourced from official
provider documentation (cited in-code) or flagged where it is not.

| Provider | Model | Context window | Max output | Source |
|---|---|---:|---:|---|
| Anthropic | Opus 5 / Sonnet 5 / Fable 5 | 1,000,000 | 128,000 | official docs |
| Anthropic | Haiku 4.5 | 200,000 | 64,000 | official docs; confirmed by a live 400 at 208,904 tokens |
| MiniMax | M3 | 1,000,000 | 512,000 | official docs |
| MiniMax | M2.7 / M2.5 / M2 | 204,800 | not specified | official docs |
| GLM (z.ai) | glm-5.3 / glm-5-turbo | 1,000,000 / 200,000 | 131,072 | official docs; runtime-confirmed `max_tokens=131072` returns 200 |
| GLM (z.ai) | glm-4.7 | 200,000 | 131,072 | official docs (previously mis-set to 128,000, a 36% undercount) |
| GLM (z.ai) | glm-4.6V / glm-5V-Turbo (vision) | 131,000 / 200,000 | 32,768 | text-only models are redirected here, see incident below |
| Qwen (Alibaba) | qwen3.7-max / qwen3.8-max | 983,616 | not specified | gateway-declared |
| Qwen (Alibaba) | qwen3-coder-plus | 1,048,576 | not specified | gateway-declared; confirmed by 1,000,009 accepted tokens |
| Qwen (Alibaba) | qwen3-coder-next | 204,800 | not specified | corrected from a prior 1,000,000 — a serious overestimate |
| Qwen (Alibaba) | qwen3-max | 258,048 | not specified | corrected from a prior 1,000,000 — same class of bug |
| Local (llama.cpp) | `code-max` (Qwen3-Coder-Next 80B MXFP4) | 262,144 | not specified | doubled from 131,072 on 2026-08-19 |

**Two of the "corrected from a prior 1,000,000" rows above are the same bug
class as the 2026-07-28 context-window incident described below** — a
model's declared window drifting away from its real one, in either
direction. Treat every number in this table as something to re-verify
against the provider's current documentation before relying on it for a
production decision; several were already wrong once.

### Rate limiters, per provider

All limiters share the same shape: a sliding window per model (RPM + TPM), a
safety factor applied to the nominal limit (headroom for gateway jitter and
concurrent requests), and exponential backoff on 429. Concurrency is capped
independently per provider (default 8 in-flight requests).

| Provider | Safety factor | Backoff steps (s) | Retry budget | Notes |
|---|---:|---|---:|---|
| Anthropic | 0.8 | 2, 5, 10, 20, 30 | 8s acquire budget | Limits are conservative defaults (50 RPM / 100K TPM for Opus/Sonnet, 100 RPM / 100K TPM for Haiku) — the real ceiling depends on your subscription plan and isn't exposed by the API; verify after deploying rather than trusting the default table. |
| MiniMax | 0.8 | 5, 10, 20, 40, 60 | 90s | 200 RPM / 10M TPM for M3, 500 RPM / 20M TPM for M2.x. 429 is deliberately excluded from the generic fallback-and-reroute logic — a rate limit is pacing, not an outage; the router waits `retry-after` and retries the *same* upstream instead of multiplying load by redirecting elsewhere while the provider is asking it to slow down. |
| GLM (z.ai) | 0.8 | 5, 10, 20, 40, 60 | 90s (8s for streaming acquire) | **The RPM/TPM numbers in the source are explicitly marked as placeholders** (`GLM_RATE_LIMITS`, `src/glm_backend.py`) — z.ai doesn't expose real quota headers, so these are conservative guesses pending confirmation from your actual plan. Don't treat them as authoritative. |
| Qwen (Alibaba) | 0.8 | 5, 10, 20, 40, 60 | 90s (45s streaming acquire) | Real limits, region-specific (measured against `ap-southeast-1`/Singapore — **do not carry these numbers over to a Beijing or Frankfurt deployment**, they're different there). Ranges from 600 RPM/1M TPM (max-tier models) up to 15,000 RPM/5M TPM (flash-tier). The 45s streaming acquire cap exists because an earlier 8s cap caused 25.6% of Qwen requests to be rejected by the router's *own* internal budget check — not by Alibaba — simply because the queue wait was cut off too early (fixed 2026-08-04). |
| Local (llama.cpp) | — | — | — | No upstream quota; concurrency is whatever the local llama.cpp/Ollama process can sustain. |

### GLM peak-hour cost control (the only provider with this mechanism)

`src/peak_scheduler.py` downgrades `glm-5.3` to the cheaper `glm-4.7` tier
Monday–Friday 14:00–18:00 Asia/Shanghai, because the GLM-5 series costs 3×
the 4.7 tier during that window. This is a **cost** optimization, not rate
limiting — it doesn't touch RPM/TPM, only which model tier gets requested.
Until 2026-08-19 the check only looked at the hour and treated weekends as
peak too, which they are not; fixed by excluding `weekday() >= 5`. No other
provider currently has an equivalent scheduled downgrade.

## Timeline of major incidents

### 2026-07-22 — Streaming TTFB regression (45s vs ~1s)

**Symptom:** time-to-first-byte on streaming responses jumped from ~1s to
45s. **Cause:** detecting an empty 200 response required buffering the
*entire* SSE stream before deciding whether to retry — a healthy response
paid the full buffering cost too. **Fix:** `src/stream_peek.py` — peek only
up to the first `content_block_start` event, then re-emit the bytes already
read and continue streaming normally. Caps: `AIROUTER_SSE_PEEK_CAP_SEC=20`,
`AIROUTER_SSE_PEEK_CAP_BYTES=65536`. Landed as part of the GLM-5.3 migration
below (commit `42d0cdc`), and applies to any mode with streaming empty-200
detection, not only GLM.

### 2026-07-26 — Per-chat mode isolation

**Symptom:** the in-chat command that switches router mode was recognized
from natural language ("use only minimax"), which meant it could fire
without the user explicitly intending it, and chats without a session
header collapsed onto a shared "default" override. **Fix:** natural-language
recognition removed; only the explicit form `!router <mode>` is intercepted,
scoped to the session fingerprint derived from the
`X-Claude-Code-Session-Id` header.

### 2026-07-28 — Context-window and buffer accounting (6 fixes)

A full audit of how the router estimates and manages context found six
independent bugs, all in the same subsystem, affecting **every mode**
(these are provider-agnostic accounting bugs, not GLM-specific):

| Bug | Fix commit |
|---|---|
| Context rewrite normalized `system` from a list to a string, destroying prompt-cache breakpoints on every request that needed rewriting — measured 5,215 requests over 20k tokens with zero cache hit, ~201M tokens paid in full | `a37f272` |
| The context-threshold gate measured the limit against a fixed per-provider map instead of the actual destination model (e.g. treating every Anthropic model as Haiku's 200K window) | `64e94dd` |
| Token estimation used a single heuristic (`chars // 4`) for three different tokenizers, undercounting Opus by ~36% | `064ccca` |
| Context windows for Anthropic models were hardcoded instead of sourced from official documentation | `68a0f61` |
| Context windows for MiniMax and GLM were likewise hardcoded and wrong (e.g. MiniMax-M3 treated as 200K when its real window is 1,000,000 — 80% of the window was being discarded) | `4079307` |
| The output-space reservation was a fixed 20% of context regardless of the actual `max_tokens` requested, wasting up to 72K tokens on 1M-window models and leaving too little margin on 200K models | `b9c52e0` |

Follow-up: `9fd5625` added an automated consistency check between
`src/model_context_map.py` and each provider's live Models API, so this
class of bug gets caught by CI instead of by a production incident. The same
class of bug recurred later for Qwen (`qwen3-max` and `qwen3-coder-next`
were both overestimated at 1,000,000 tokens; corrected to their real
258,048 and 204,800 — see the model catalog above) — the consistency check
does not yet cover every provider, so this remains a class of risk to watch
rather than a fully closed one.

### 2026-08-03 — GLM peak-hour scheduling

See "GLM peak-hour cost control" above.

### 2026-08-08 — Stress test across all modes

A dedicated stress audit sent real traffic through every mode over several
days and found issues ranging from critical to low severity. Full report:
[`sviluppo/audit/2026-08-08-stress-router/REPORT.md`](../sviluppo/audit/2026-08-08-stress-router/REPORT.md).

**F1 (critical) — shutdown incompatible with systemd, all modes.** A clean
shutdown of the proxy could not complete within the time systemd allows for
it: `aiohttp`'s default `shutdown_timeout` is 60s, and serial cleanup across
10 listening ports could theoretically take up to 600s, while systemd's
`TimeoutStopSec` gives it 8. Every restart risked a `SIGKILL` mid-request.
**Fixed**: explicit `shutdown_timeout=3.0` on the `AppRunner` plus parallel
cleanup via `asyncio.gather()`.

**F2/F3 (high) — two modes measured as functionally broken.** Real traffic
over 7 days, by mode:

| Mode | Requests | Error rate | Notes |
|---|---:|---:|---|
| `mix-am` | 17,483 | 0.0% | healthy on HTTP status, but 415 empty responses — 1 in 42 |
| `qwen` | 942 | **16.0%** | 429s, 20.5% empty responses, **59,000 avg input tokens with `cache_read = 0`** — every request pays the full prompt |
| `anthropic` | 322 | 1.6% | healthy |
| `local` | 195 | 0.0% | healthy |
| `mix-gm` | 37 | 0.0% | healthy (small sample) |
| `glm` | 33 | 0.0% | healthy (very small sample) |
| `mix-al` | 32 | 0.0% | healthy (very small sample) |
| `mix-ag` | 15 | **80.0%** | 12 of 15 requests were 429, median output 0 tokens — de facto non-functional at the time of this audit |
| `minimax` | 9 | 0.0% | healthy but a tiny sample |

TTFB on a minimal real request, by mode: `mix-al` 0.11s · `local` 0.68s ·
`anthropic` 0.88s · `mix-ag` 0.97s · `mix-am` 1.32s · `glm` 1.45s · `qwen`
1.62s · `minimax` 1.84s · `mix-gm` 2.69s.

**This F2/F3 finding was logged as an open action item (A5: "decide the
fate of `qwen` and `mix-ag` — repair or mark not recommended"), not as a
closed, fixed bug** — unlike everything else in this document. If you plan
to run `qwen` or `mix-ag` in production, re-measure their current error
rate before trusting them; nothing in the commit history after 2026-08-08
specifically targets these two modes' reliability the way the GLM and
cache-burn incidents below were targeted.

**F4 (high) — no semantic validation, all modes.** Requesting a
nonexistent model name (`model: "inesistente-xyz"`) returned `200 OK` and
generated 519 billed tokens instead of failing fast with a `400`. Still an
open item as of the audit (action A2: allowlist `model`, enforce
`max_tokens >= 1`, enforce `role ∈ {user, assistant}`).

**F5/F6/F7 (medium):** the response's `model` field didn't always match the
model actually used (open item, action A3); MiniMax silently raised any
`max_tokens < 1024` up to 1024 (`MINIMAX_MIN_MAX_TOKENS`), affecting 0.05%
of requests but never logged when it fired (open item, action A4); `mix-am`
had 415 empty responses out of 17,483 (1 in 42) — a known, recurring class
of bug that has been chased down multiple times and reduced, not
eliminated.

**Routing correctness check (positive result):** 198 mode×model
combinations, including malformed input, path traversal attempts, and CRLF
injection in the model field, produced **zero exceptions and zero
cross-provider leaks** — whatever else was wrong, the routing table itself
held.

### 2026-08-16 — The 670M-token cache incident

**Symptom:** the usage sidecar showed `cache_read` staying flat between
turns instead of growing, and `cache_creation` paying the full prompt
repeatedly — the signature of a prompt-cache breakpoint that never survives
between requests. **Root cause:** the telemetry relay decoded the response
buffer as UTF-8 without decompressing the gzip payload first, so the parser
never found the `message_start` marker it needed — cache-read/creation were
being logged as zero and input was crudely estimated from character count.
**Fix:** commits `5e29d75` (decompress the buffer before extracting
usage/cache tokens) and `d1ef093` (the truncated-stream detector was also
searching for its marker inside still-compressed bytes). Full report:
[`sviluppo/audit/2026-08-16-token-audit/REPORT.md`](../sviluppo/audit/2026-08-16-token-audit/REPORT.md).

**How to recognize this class of problem in your own logs:** a line like
`cache: OK bp=... read=N creation=M` where `read` stays constant byte-for-byte
across different requests — instead of growing turn over turn — means
something upstream of the cache breakpoint is changing on every request.

### 2026-08-18 — Tool-schema bloat in the local mode

**Symptom:** the local (`gpt`) mode's first turn regularly hit its own
timeout with no response. **Measurement:** a full VS Code session sends 304
tool definitions per request, 278 of them from MCP servers — about 67,500
tokens of schema, before a single word of actual conversation. Cloud modes
absorb this because it gets cached (`mix-am` measured 173K `cache_read` per
request) or because the provider prefills fast; the local backend prefills
at ~440 tok/s with `cache_read = 0` (nothing to cache against on a local
model), so 87K tokens of context cost ~200s against a 240s timeout budget —
the first turn was, in practice, unusable. **Fix:** `src/gpt_tool_trim.py`
strips MCP tool definitions server-side, only in `gpt` mode, leaving the
built-in tools (Read, Write, Edit, Bash, Glob, Grep, …) untouched. The strip
lives in the router rather than in client-side settings specifically because
client settings are static per project and can't see which mode is
currently active — disabling MCP tools at the client level would also
cripple `anthropic`/`mix-am`, which use and cache them fine.

### 2026-08-19 — GLM-5.3 migration (7 defects closed)

Upgrading from GLM-5.2 to GLM-5.3 required a full re-audit of the GLM
request/response path:

| # | Defect | Root cause | Fix | Commit |
|---|---|---|---|---|
| 1 | Requesting `glm-5.3` by name silently returned `glm-4.7` | An `override=None` from the router's route resolver (meaning "don't rewrite") was read downstream as "use the mode default" | `canonical_glm_model()` recognizes explicitly-requested GLM models case-insensitively | `74672c0` |
| 2 | The empty-200-response check never ran for GLM-5.3 | It tested the model name the *client* requested, not the actual upstream model | Use the upstream model name in the check | `74672c0` |
| 3 | Logs blamed the wrong model | Same root cause as #2, in the logging path — also affected the `local` backend, which logged `claude-opus-5` instead of `code-max` | Use `upstream_model`/`lim_model` everywhere instead of the client-requested name | `9e354c8` |
| 4 | Empty 200s in streaming stalled the whole turn | See "2026-07-22" above | `src/stream_peek.py` | `42d0cdc` |
| 5 | Small `max_tokens` requests came back empty | GLM spent the entire token budget on an internal `thinking` block; z.ai rejects `max_tokens` under 4096 with a 400 | `GLM_MIN_MAX_TOKENS = 4096` floor, applied before the request goes out | `2b5f06b` |
| 6 | GLM-5.3 is text-only but answers `200 OK` on an image block, silently ignoring it | The model gives no explicit error for unsupported input | `route_image_to_vision()` redirects image requests to `glm-4.6V` before the peak-hour cap is applied | `22c29cc` |
| 7 | The `local` backend lost `stop_reason` and diagnostic detail, and had no tool isolation at all (`filter_tools_for_backend(body, "local")` was a silent no-op) | SSE response headers were copied into a plain `dict` instead of a case-preserving `CIMultiDict`, breaking case-insensitive header lookup; `local` was simply missing from the brand-check table | Use `multidict.CIMultiDict`, as `aiohttp` does natively; added `local` to the tool-isolation brand check | `426ebfb` |

Verification: `sviluppo/tests/test_glm_modes.sh` (11/11) and a dedicated
stress suite (23/23 — role mapping, requested model honored, 131K
`max_tokens` without clamping, vision redirect, streaming TTFB, local tool
isolation, 10-way concurrency). Full report:
[`sviluppo/audit/2026-08-19-glm-53/REPORT.md`](../sviluppo/audit/2026-08-19-glm-53/REPORT.md).

### 2026-08-22 — Cache-burn on MiniMax and GLM

**Measurement** (`airouter-info`, 7-day window, tokens per request):

| provider | context/req | % re-paid as "new" | % is tool schema |
|---|---|---|---|
| anthropic | 195,842 | 0.4% | 8% |
| minimax | 63,838 | 67% | 11% |
| glm | 117,244 | 9%* | 48% |

*z.ai never reports `cache_creation`, so this number is a floor, not a
result — the true figure is worse.

**MiniMax root cause:** the context-trimming function recalculated, from
scratch, how many old messages to keep on every single turn above the
threshold — the cut point moved slightly each time even with a quantized
grid, so the `messages` prefix was never byte-identical between turns and
the provider's prompt cache never had a chance to hit.

**GLM root cause:** nothing filtered the heavy personal-productivity MCP
connectors (Gmail/Calendar/Drive/Canva, ~40KB per request) out of the tool
list sent to GLM — they were re-sent, unfiltered, on every single turn.

**Fixes**, both in commit `52e4f08`, both verified present in this checkout:

- `src/context_rewrite.py` — a `_STICKY_DROP_COUNT` map (session fingerprint
  → cut point) makes the cut point sticky: it's only recalculated when the
  existing one no longer fits the budget, so the message prefix stays
  identical across turns whenever possible. In-memory state, reset on
  process restart — a known and accepted limitation.
- `src/tool_isolation.py` — `strip_heavy_mcp_for_glm()`, called from
  `src/glm_backend.py`, removes the heavy MCP connectors from GLM requests
  specifically (opt-out: `AIROUTER_GLM_MCP_FILTER=0`), while preserving the
  `cache_control` breakpoint on what remains.

853 tests passed after this change (848 before + 5 new).

## Tool-schema bloat: measured, and what to do about it

Two of the incidents above (2026-08-04 for Qwen, 2026-08-18 for local mode)
trace back to the same root cause: **every tool and MCP server you have
enabled sends its full JSON schema on every single request**, whether or not
that turn uses it. This is not specific to this router — it's how the
underlying API works — but it interacts badly with providers that don't
cache well or have a small context/rate budget:

- A full VS Code session with several MCP servers enabled measured **304
  tool definitions, 278 of them MCP, ~67,500 tokens of pure schema** before
  any actual conversation content (2026-08-18 measurement, see above).
- Three personal-productivity connectors (Gmail/Calendar/Drive) alone
  measured **64KB of the 137KB** of tool definitions sent to Qwen, and by
  themselves caused a **25.6% request-rejection rate** against Qwen's TPM
  budget (2026-08-04 measurement, fixed in `src/qwen_tool_trim.py`).
- This is worst on modes with no cache to absorb the cost (`local`/`gpt`,
  `cache_read = 0` by construction) or on providers with tight TPM budgets
  (`qwen`), and least visible on modes that cache well (`mix-am` measured
  173K `cache_read` per request on the same tool set that broke `gpt`'s
  timeout).

**What the router does automatically:** `src/tool_isolation.py` and its
per-backend callers (`gpt_tool_trim.py`, `qwen_tool_trim.py`,
`strip_heavy_mcp_for_glm` in `glm_backend.py`) strip the tool definitions
that a given backend can't use well or can't afford, mode by mode, without
touching modes where the same tools are cheap. This is why the fix lives
server-side in the router instead of in client settings: client-side
settings are static and can't tell which mode is currently active.

**What you can additionally do, independent of this router:**

- **Prefer a CLI/script invocation over an always-on MCP server** when one
  exists for the task. An MCP server's tool schemas are paid on *every*
  turn for the rest of the session regardless of whether you use them
  again; a CLI command's cost is paid once, when you actually run it.
- **Enable only the MCP servers a given project or session actually needs**,
  rather than leaving a large, universal set enabled everywhere. Most
  MCP clients support per-project or per-session server lists — use them.
  The 67,500-token measurement above was not from a session using all those
  servers; it was the fixed cost of merely having them enabled.
- If your client supports **deferred/lazy tool loading** (only the tool
  *name* is sent upfront, and the full schema loads on first use), prefer it
  over eager loading for anything used occasionally. `tool_isolation.py`'s
  `sanitize_defer_loading()` exists specifically to preserve this signal
  through the router instead of silently discarding it.

None of this requires touching the router's code — it's client-side
configuration — but the router's own telemetry (`tools_count`,
`tools_bytes`, `tools_mcp_count`, `tools_mcp_bytes` in the usage log) is
exactly what you'd use to check whether it's worth doing for your own
setup.

## Delegation & context governance layer (reference pattern)

This section explains something the router itself cannot fix, because it's
deliberately not the router's job: **the proxy has no memory and no
opinion about who calls it or why** ("the router is a transparent tunnel" —
`src/role_routing.py`'s `resolve_route(mode, model)` is a pure function; the
THINK/ACT/VERIFY pipeline the proxy used to run internally was removed,
~1,900 lines, because keeping orchestration state in the proxy created a
second source of truth that drifted from the client's own state). The
router only does one thing: it looks at the model name your client
requests, decides whether that name means "orchestrator" or "executor" for
the active mode (see `role_routing.py`'s convention: Fable/Opus/Sonnet-named
requests are THINK, Haiku-named requests are ACT), and forwards to the
right provider.

**The consequence, if you skip this section:** if your agentic client
always sends every request under one identity (e.g. it never spawns a
sub-task under a different model name), then in a *mixed* mode only the
THINK role is ever exercised. The router has nothing wrong with it — there
is simply no request that ever asks for the ACT role. From the outside this
looks exactly like "the router always routes to the orchestrator's
provider regardless of mode," which is a client-side behavior, not a router
bug, and it's the single most common cause of "why does everything still go
through my orchestrator model even though I switched modes."

Making mode-switching actually change *where execution happens* requires a
governance layer on the client side that (a) knows the difference between
"thinking/planning" work and "writing project code" work, and (b) actively
routes the second kind to a sub-task under the executor's model name instead
of letting the orchestrator just do it inline. The pattern below is
implemented as Claude Code hooks in the reference deployment, but it
generalizes to any agentic client with an extensibility/plugin mechanism —
none of the file paths or specifics below are required, only the shape of
the rules.

### 1. Require an explicit role on every delegated sub-task

Before anything else can work, every spawned sub-task must declare which
role/model it's running as — never "inherit" or "default." Otherwise a
sub-task silently runs as the orchestrator, which defeats delegation
without producing any visible error.

```python
# PreToolUse guard on whatever tool your client uses to spawn sub-tasks.
INVALID_MODELS = {"", "inherit", "default", "none", "null"}

def check(tool_name, tool_input):
    if tool_name not in SUBTASK_TOOLS:
        return APPROVE
    model = str(tool_input.get("model") or "").strip().lower()
    if model and model not in INVALID_MODELS:
        return APPROVE
    return BLOCK("sub-task spawned without an explicit model/role")
```

### 2. Gate direct code writes by the orchestrator behind a size threshold

The orchestrator is allowed to make small, surgical edits directly (the
round-trip cost of delegating a one-line fix exceeds the benefit), but
anything past a threshold must go through the executor instead of being
written inline. A few properties matter more than the exact threshold:

- **The threshold should be mode-aware, not global.** In a mode where the
  orchestrator and executor are the same provider, there's nothing to gain
  by forcing delegation. In a mode where they're different providers (the
  entire reason mixed modes exist), the threshold should be tight — one
  reference deployment measured that a 15-line exemption let through 344
  full edits in a week (175 Python files, 98 JS, 71 PHP) — essentially all
  of the real development work — while a 5-line exemption let through only
  genuinely surgical corrections (a value, a typo, a condition).
- **A partial block is worse than no block.** Blocking only *some* of the
  write paths (say, structured edits but not shell redirection like
  `cat > file.py <<EOF`) doesn't move work to the executor — it moves it to
  whichever channel is still open. One measurement of exactly this mistake
  showed a partial block produce a **+524% increase in the channel that was
  still open** within three days, with delegation to the intended executor
  staying flat. Close every write path together, or don't bother — a half
  measure just relocates the symptom.
- **Never let the block become an invisible infinite loop.** If a hook
  blocks the same (session, target) repeatedly in a short window with no
  escape, an agent that doesn't know how to delegate will just keep
  retrying the same blocked action instead of ever learning to route around
  it — burning turns with no error visible to the user. A circuit-breaker
  that auto-allows after N denials within a time window, logged under a
  distinct reason so it's still measurable, turns a silent stall into a
  visible, debuggable signal instead.
- **The hook must be able to tell the orchestrator apart from the sub-task
  it just spawned, or it blocks both.** This sounds obvious written down,
  but it's the single most consequential detail in this whole pattern, and
  it's easy to omit by accident: if the guard has no way to check who is
  calling it (e.g. an `agent_id`/session-origin field present on sub-task
  invocations and absent on the orchestrator's own), it will deny the
  orchestrator's direct write *and* deny the sub-task's legitimate write
  with the identical message — nothing can ever write more than the
  micro-edit threshold, from either side, and the mode's execution role
  never actually engages. A hand-rolled version of this pattern missing
  exactly this check was the confirmed root cause of a real "the executor
  never executes, it just plans" report: a sub-task spawned specifically to
  write a 25-line file was refused by the same rule meant to redirect the
  orchestrator *toward* it. Adding the origin check and re-running the same
  controlled test (a sub-task writing a file past the micro-edit threshold)
  fixed it immediately. Test this specific case explicitly before trusting
  any implementation of this pattern: spawn a sub-task, have it write
  something past your threshold, and confirm it's allowed — don't just
  confirm the orchestrator is blocked.

### 3. Derive the "legal provider chain" from the same routing table the proxy uses

If your governance layer maintains its own separate idea of "which provider
is legitimate in mode X," it will drift from the router's actual routing
table the first time either one changes. Derive it instead by calling the
router's own route-resolution function for both roles:

```python
def legal_chain(mode):
    think_provider, _ = resolve_route(mode, "orchestrator-model-name")
    act_provider, _ = resolve_route(mode, "executor-model-name")
    return {think_provider, act_provider}
```

Then block any tool call that targets a provider outside that set — e.g. in
a GLM-only mode, a tool that talks to MiniMax specifically should be
refused, not silently allowed to leak outside the mode's intended provider
set. This is also what stops delegation from accidentally using a provider
you're not paying for in that mode.

### 4. Keep reminders throttled and non-blocking where blocking isn't warranted

Not every governance signal should be a hard block. A lightweight, one-time-
per-session reminder injected on non-trivial requests (below some length or
complexity threshold, skip it) can encode a plan → confirm → execute →
verify discipline without re-injecting the same text on every single turn
and bloating context for no benefit. Fire it once, mark that it fired, and
move on.

### 5. A sub-task's context grows from what *it* reads, not just from what the parent hands it

It's tempting to assume that once a sub-task has its own scoped context
(pattern 1), the token cost of delegation is under control. It isn't
automatically: a sub-task that's told to "edit this file" and then reads
the *entire* file itself — repeatedly, across several of its own internal
turns, as it iterates — can accumulate a large context independent of
anything the parent passed it. In one measurement, the heaviest individual
delegations (up to ~100K tokens, tens of seconds of added latency) weren't
caused by dragged-along conversation history at all — every one of them
was a sub-task reading a large source file in full to make a change to it,
turn after turn, inside its own context. The fix isn't in the governance
layer, it's in how the delegation is worded: **point the sub-task at the
specific function or line range that needs to change, and require it to
use targeted, offset-based reads instead of reading whole files**,
mirroring the same discipline a careful human reviewer would use. This
matters independent of provider — it affects the executor regardless of
which mode routes to it.

### 6. Watch out for delegation overhead exceeding delegation benefit

Spawning a sub-task has a fixed cost (the round trip, the sub-task's own
context setup). For genuinely small or repetitive generation tasks — many
similar files, boilerplate — spawning one sub-task per file can cost more
than it saves. A soft threshold ("after N sub-task spawns for the same kind
of generation work in one session, prefer a single batched call to the
executor instead") avoids trading one inefficiency for another.

None of this layer needs to be complex to be effective — the six patterns
above are each a few dozen lines. What matters is that they exist at all:
without *some* client-side mechanism enforcing "the orchestrator plans, the
executor writes," switching this router's mode changes which provider your
orchestrator's own reasoning goes through, but changes nothing about where
your code gets written — because nothing ever asked for the ACT role.

## Verifying your instance has these fixes

```bash
git log -1 --format="%H %ci"          # compare against 2026-08-30 / 7e73398 or later

grep -n "_STICKY_DROP_COUNT"        src/context_rewrite.py
grep -n "strip_heavy_mcp_for_glm"   src/tool_isolation.py src/glm_backend.py
grep -n "GLM_MIN_MAX_TOKENS"        src/glm_backend.py
grep -n "CIMultiDict"               src/local_backend.py
grep -n '"glm-4.7"'                 src/model_context_map.py   # expect 200_000, not 128_000
grep -n "shutdown_timeout"          src/ai-router-proxy.py
python3 -c "from router_constants import VALID_MODES; print(len(VALID_MODES), VALID_MODES)"  # from src/, expect 15 modes
```

If any of these come back empty, the corresponding fix from this document is
missing from your checkout — update to a current `main` rather than
reapplying it by hand, since several of these fixes touch the same files
and are easiest to get right together. If you're specifically troubleshooting
`qwen` or `mix-ag`, re-measure their current error rate first (see the
2026-08-08 stress test above) — those two are documented as a known
weakness with an open action item, not as a closed bug you can grep for.

Related reading:
[`POST-MORTEM-20260823-restart-loop-connection-lost.md`](../POST-MORTEM-20260823-restart-loop-connection-lost.md)
(a separate, unrelated incident: a synchronous log write blocking the event
loop, misread as an API-key problem) and `BUG-CATALOG.md` at the repository
root (auto-generated from the live error log, not hand-maintained).
