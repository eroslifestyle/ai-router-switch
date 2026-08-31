# Reliability, cache & context — engineering history

This document exists because running `ai-router-switch` well is not just
"start the proxy and pick a mode": prompt-caching, per-model context
windows, and tool-schema bloat all interact in ways that are easy to get
wrong and expensive when you do. Every issue below actually happened on a
production instance of this router, was diagnosed with real telemetry, and
is fixed in the current codebase (verified against `git log` on this
checkout, commit `7e73398`, 2026-08-30). If your instance still shows any of
these symptoms, the first thing to check is whether you are running a
version old enough to predate the relevant fix.

None of this is theoretical: the numbers below (670M tokens, 67.5K tokens of
tool schemas per turn, 25.6% rejected requests, 5,215 uncached large
requests) are measurements from this project's own operation, cited with the
commit that fixed each one.

## Timeline of major incidents

### 2026-07-22 — Streaming TTFB regression (45s vs ~1s)

**Symptom:** time-to-first-byte on streaming responses jumped from ~1s to
45s. **Cause:** detecting an empty 200 response required buffering the
*entire* SSE stream before deciding whether to retry — a healthy response
paid the full buffering cost too. **Fix:** `src/stream_peek.py` — peek only
up to the first `content_block_start` event, then re-emit the bytes already
read and continue streaming normally. Caps: `AIROUTER_SSE_PEEK_CAP_SEC=20`,
`AIROUTER_SSE_PEEK_CAP_BYTES=65536`. Landed as part of the GLM-5.3 migration
below (commit `42d0cdc`).

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
independent bugs, all in the same subsystem:

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
class of bug gets caught by CI instead of by a production incident.

### 2026-08-03 — GLM peak-hour scheduling

**Symptom:** GLM models were being downgraded to the cheaper tier on
weekends. **Cause:** `is_peak_hour()` checked only the hour of day, not the
day of week — the intended peak window is Monday-Friday 14:00-18:00
(Asia/Shanghai). **Fix:** excludes `weekday() >= 5`.

### 2026-08-08 — Stress test: shutdown compatibility with systemd

A dedicated stress audit found that a clean shutdown of the proxy could not
complete within the time systemd allows for it: `aiohttp`'s default
`shutdown_timeout` is 60s, and serial cleanup across 10 listening ports could
theoretically take up to 600s, while systemd's `TimeoutStopSec` gives it 8.
Every restart risked a `SIGKILL` mid-request. **Fix:** explicit
`shutdown_timeout=3.0` on the `AppRunner` plus parallel cleanup via
`asyncio.gather()`. The same audit also found two under-provisioned modes
(one at 16% and one at 80% `429` rate) and added semantic validation so a
typo in a requested model name fails fast instead of triggering a full paid
generation. Full report:
[`sviluppo/audit/2026-08-08-stress-router/REPORT.md`](../sviluppo/audit/2026-08-08-stress-router/REPORT.md).

### 2026-08-16 — The 670M-token cache incident

**Symptom:** the usage sidecar showed `cache_read` staying flat between
turns instead of growing, and `cache_creation` paying the full prompt
repeatedly — the signature of a prompt-cache breakpoint that never survives
between requests. **Root cause:** the telemetry relay decoded the response
buffer as UTF-8 without decompressing the gzip payload first, so the parser
never found the `message_start` marker it needed — cache-read/creation were
being logged as zero and input was crudely estimated from character count.
This measurement bug had been silently misrepresenting cache health for
some time; once diagnosed, the underlying gzip decode was the actual
production issue costing real tokens. **Fix:** commits `5e29d75`
(decompress the buffer before extracting usage/cache tokens) and `d1ef093`
(the truncated-stream detector was also searching for its marker inside
still-compressed bytes). Full report:
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
| 3 | Logs blamed the wrong model | Same root cause as #2, in the logging path | Same fix | `74672c0` |
| 4 | Empty 200s in streaming stalled the whole turn | See "2026-07-22" above | `src/stream_peek.py` | `42d0cdc` |
| 5 | Small `max_tokens` requests came back empty | GLM spent the entire token budget on an internal `thinking` block; z.ai rejects `max_tokens` under 4096 with a 400 | `GLM_MIN_MAX_TOKENS = 4096` floor, applied before the request goes out | `2b5f06b` |
| 6 | GLM-5.3 is text-only but answers `200 OK` on an image block, silently ignoring it | The model gives no explicit error for unsupported input | `route_image_to_vision()` redirects image requests to `glm-4.6V` before the peak-hour cap is applied | `22c29cc` |
| 7 | The `local` backend lost `stop_reason` and diagnostic detail | SSE response headers were copied into a plain `dict` instead of a case-preserving `CIMultiDict`, breaking case-insensitive header lookup | Use `multidict.CIMultiDict`, as `aiohttp` does natively | `426ebfb` |

Verification: `sviluppo/tests/test_glm_modes.sh` (11/11) and a dedicated
stress suite (23/23). Full report:
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

**What the router does automatically:** `src/tool_isolation.py` and its
per-backend callers (`gpt_tool_trim.py`, `qwen_tool_trim.py`,
`strip_heavy_mcp_for_glm` in `glm_backend.py`) strip the tool definitions
that a given backend can't use well or can't afford, mode by mode, without
touching modes where the same tools are cheap (because they're cached or the
provider handles them fine). This is why the fix lives server-side in the
router instead of in client settings: client-side settings are static and
can't tell which mode is currently active.

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

## Verifying your instance has these fixes

```bash
git log -1 --format="%H %ci"          # compare against 2026-08-30 / 7e73398 or later

grep -n "_STICKY_DROP_COUNT"        src/context_rewrite.py
grep -n "strip_heavy_mcp_for_glm"   src/tool_isolation.py src/glm_backend.py
grep -n "GLM_MIN_MAX_TOKENS"        src/glm_backend.py
grep -n "CIMultiDict"               src/local_backend.py
grep -n '"glm-4.7"'                 src/model_context_map.py   # expect 200_000, not 128_000
grep -n "shutdown_timeout"          src/ai-router-proxy.py
```

If any of these come back empty, the corresponding fix from this document is
missing from your checkout — update to a current `main` rather than
reapplying it by hand, since several of these fixes touch the same files
and are easiest to get right together.

Related reading: [`POST-MORTEM-20260823-restart-loop-connection-lost.md`](../POST-MORTEM-20260823-restart-loop-connection-lost.md)
(a separate, unrelated incident: a synchronous log write blocking the event
loop, misread as an API-key problem) and `BUG-CATALOG.md` at the repository
root (auto-generated from the live error log, not hand-maintained).
