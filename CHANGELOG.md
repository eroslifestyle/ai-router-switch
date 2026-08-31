# Changelog

## v1.1.0 (2026-08-30)

143 commits after v1.0.0. Highlights:

**New modes** (12 → 15): `ultra` (Anthropic THINK/VERIFY + GLM ACT + MiniMax
for code specifically via CLI, for tasks that exhaust Anthropic quota),
`gpt` (single resident local model), `opr` (OpenRouter, sandbox).

**Resilience**: automatic OAuth token renewal instead of waiting on the
CLI; correct exit from the DEGRADED state; the OAuth self-test now
actually runs; the loop-breaker no longer mistakes a retry for a loop;
removed the `_SyntheticResponse` twin that was producing real 502s on
MiniMax 429s.

**GLM/z.ai**: migration to glm-5.3; a model requested by name is honored
instead of being silently downgraded; empty-200 detection in streaming
without buffering the whole response; weekends are no longer treated as
peak hours; `max_tokens` ceiling is per-model; images are no longer routed
to a model that can't see them.

**Cache diagnostics**: cache-hit measurement used to cover only Anthropic —
now covers every mode; a GLM "total miss" that was actually a created cache
entry was corrected; `airouter-info tool` measures the real weight of tool
definitions.

**Router**: a failed port bind is no longer silent; automatic reroute to
THINK when the body exceeds the ACT model's window; removed the shared
`default` bucket for requests without a session header, falling back to a
hash of the user's first message instead.

Repository: https://github.com/eroslifestyle/ai-router-switch

## v1.0.0

First tagged release.
