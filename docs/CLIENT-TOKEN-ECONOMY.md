# Client-side token economy — configuring the terminal, not just the router

`RELIABILITY-AND-PERFORMANCE.md` covers what the *router* does to avoid
wasting tokens once a request reaches it (cache-breakpoint stability,
per-mode tool trimming, correct context-window accounting). This document
covers the other half: what actually caused sessions to burn through a
weekly quota in hours even when the router side was healthy, and the
client-side (Claude Code terminal / CLI) configuration patterns that fixed
it. If you're only using this router as a passive proxy without controlling
what your client sends in the first place, you can still saturate a session
in minutes — the router can't compress or cache what it never receives cheap.

Every pattern below was measured on a real, long-running Claude Code
deployment, not designed in the abstract. Numbers are cited where they
exist; where a number is deployment-specific, it's presented as "one
measurement showed X," not as a universal constant — re-measure your own
setup before trusting any of these figures for capacity planning.

## What actually caused the waste

Four independent sources of avoidable token cost, found by decomposing a
real session's context byte-for-byte rather than guessing:

### 1. The subagent's final report, not its internal work

Delegating work to a sub-task is supposed to save tokens — its internal
exploration, false starts, and intermediate reasoning never come back to
the parent session. **What does come back, in full, and gets re-paid on
every subsequent turn, is the sub-task's final report.** In one real
session (a multi-hour trading-strategy analysis, ~4.3MB of transcript), the
single largest line item in the parent's context wasn't files read or
images — it was **12 sub-task reports at ~39KB each, roughly 117,000
tokens, about 30% of the session's live content**. Delegating didn't save
those tokens; it deferred them to the moment of consolidation, where they
got paid in full and then re-billed every turn afterward because they stay
in context.

**Fix:** cap what a sub-task is allowed to report back — not what it's
allowed to *do*. A sub-task can read, search, and explore as much as it
needs internally; its final message to the parent should be a short digest
(a hard ceiling, e.g. ~2000 characters), file *paths* instead of file
*contents*, what was done, what proves it worked, and what failed — never a
dump of everything it touched. Enforcing this as a wrapper around every
sub-task spawn (rather than trusting each prompt to remember to ask for a
short report) is what makes it reliable across a long session instead of
degrading as the session goes on.

### 2. Tool/skill catalog size, paid on every single turn regardless of use

Anything registered as an available tool, command, or skill has its
description (not its full body, just the *catalog entry* — name +
one-line description) sent in the prefix of every request, whether or not
that turn uses it. This is a fixed tax, and it scales with how many
capabilities you've registered, not with how many you use.

**Measured case:** a catalog of 272 available skills/commands added ~35.9KB
to every request's prefix — about 9,180 tokens re-paid on every single
turn. Cross-referencing 926 sessions and over 150,000 transcript lines
found **only 3 of those 272 were ever actually invoked.** The other 269
were pure prefix tax with zero realized benefit across nearly a thousand
sessions. Archiving everything with zero invocations, zero references from
still-active capabilities, and zero mentions in active configuration
brought the catalog from 35.9KB down to 18.1KB — roughly half the fixed
cost, with no loss of anything actually used.

**Fix:** measure real invocation rate over a large enough session sample
before deciding what stays loaded by default. Anything with a long track
record of zero use is a pure cost with no offsetting benefit — move it out
of the always-loaded catalog, but keep it *discoverable* (a search command
over the archived set, not deletion) so a genuinely rare need can still be
served without permanently registering the entry's cost on every turn in
between.

### 3. MCP servers that ignore per-project gating

Many MCP-capable clients support per-project or per-session server lists —
in principle, a server only loads its tool schemas where its project
actually needs it. **In practice, this gating does not apply uniformly
across every client surface**: a specific IDE-extension surface was found
to load every `enabled: true` server's full schema into *every* chat,
regardless of which project was open or whether per-project gating config
existed at all — only a separate CLI wrapper actually respected it. The
result was several rarely-used MCP servers' schemas being paid in full on
every chat in every project, all the time, for a benefit realized in maybe
one project out of many.

**Fix:** don't assume gating configuration works the same way across every
surface you use the same client from (terminal vs IDE extension vs any
third surface) — verify it per surface. For servers that turn out to load
unconditionally regardless of gating, the honest fix is disabling them by
default and substituting a direct, on-demand alternative for the rare case
they're needed: a plain HTTP call to the same backend the MCP server would
have wrapped, a direct read/write against the same storage the server would
have mediated, or a small CLI wrapper around the same API. None of these
alternatives cost anything when not in use, unlike an always-registered MCP
tool schema.

### 4. Boot-time instruction files that don't need to be there for every task

Global instruction files (whatever your client auto-loads at the start of
every session — a system-prompt-equivalent config) are paid in full on
every single session's first request, regardless of whether that session
ever touches the areas those instructions cover. A large security ruleset,
full testing conventions, full database conventions, and full git-workflow
conventions loaded unconditionally cost the same whether the session is a
one-line question or a multi-hour refactor.

**Fix:** split "core, always-relevant" content (a small, universally-true
subset) from "detailed, on-demand" content (the full ruleset, loaded only
when a task actually touches that area, via an explicit read triggered by
task content). One deployment's boot instructions went from roughly 13K
tokens to roughly 6K by keeping only a slimmed core auto-loaded and moving
full detail behind an index the model reads on demand — the full detail
didn't disappear, it just stopped being paid for by sessions that never
needed it.

## The cache-health indicator to actually watch

Independent of all four causes above, the single most expensive failure
mode is a prompt-cache breakpoint that never survives between turns — every
turn re-pays the *entire* context instead of the incremental cost of what
changed (this is covered in depth, with a real 670-million-token incident,
in `RELIABILITY-AND-PERFORMANCE.md`). The client-side habit that catches
this early: if your backend or proxy logs cache read/creation figures per
request, **`cache_read` should grow turn over turn within a session, and
`cache_creation` should stay small after the first turn.** A `cache_read`
that stays flat — constant byte-for-byte between different requests in the
same session — instead of growing means something upstream of the
breakpoint (a rewritten system prompt, a trimmed message list with a
shifting cut point, a timestamp or session ID embedded where it shouldn't
be) is changing on every single turn, and the cache is providing zero
benefit no matter how well everything else in this document is configured.
Check this number, specifically, before assuming a slow or expensive
session is caused by genuine content growth rather than a broken cache
breakpoint.

## Context-budget alerts, sized to the model's actual window

A fixed alert threshold ("warn at 100K tokens used") makes no sense across
models with different context windows — it fires far too early on a
1-million-token model and far too late on a 200K one. The pattern that
scales: **derive the alert threshold from the active model's actual window**
size, not a hardcoded number. For large-window models, staged alerts (e.g.
at 30%/50%/70% of the window) give room to checkpoint progress well before
the limit; for small-window models, a single alert close to saturation
(e.g. 90%) avoids nagging on every session when the window is inherently
tight and most of it needs to be usable. The specific percentages matter
less than the principle: the threshold is a function of the model actually
in use for that session, computed at session start, not a constant baked
into a config file that quietly stops making sense the moment you switch
models.

## Delegation as a token-saving strategy, done right

Spawning a sub-task to do exploration, search, or bulk generation is a
genuine token-saving strategy — its internal context truly does not return
to the parent — but only if pattern §1 above (capped, digest-only reports)
is enforced. Two corollaries worth stating explicitly:

- **Parallelize independent sub-tasks in a single batch rather than serially.**
  Sequential sub-task spawns each carry their own fixed report cost; batching
  independent work into one round of parallel sub-tasks doesn't reduce the
  per-report cost, but it avoids paying the *session's own* accumulated
  context growth once per sub-task round instead of once total.
- **Don't delegate trivial work.** A sub-task spawn has fixed overhead — its
  own setup, its final report, the round-trip. For a change small enough to
  make directly (a one-line fix, a single well-understood edit), delegating
  it costs more than doing it inline. The same principle used for the
  router's own delegation-governance layer (see
  `RELIABILITY-AND-PERFORMANCE.md`, "Delegation & context governance layer")
  applies here at the client level too: delegate work sized to be worth the
  overhead, not everything indiscriminately.

## Summary — what to actually configure

| Waste source | What to configure |
|---|---|
| Sub-task reports returning in full | Hard character cap + path-only detail on every sub-task's final report, enforced structurally, not just requested in the prompt |
| Tool/skill catalog size | Measure real invocation rate over a large session sample; archive (don't delete) anything with zero use; keep archived entries searchable on demand |
| MCP servers ignoring per-project gating | Verify gating behavior per client surface, not once for the client in general; disable servers that load unconditionally and substitute a direct on-demand alternative |
| Unconditional boot-time instructions | Split a small, always-true core from detailed, on-demand content loaded only when a task touches that area |
| Broken prompt-cache breakpoints | Watch `cache_read` growth vs `cache_creation` per session; a flat `cache_read` between turns means something in the prefix is changing every turn |
| Fixed context-alert thresholds | Derive the threshold from the active model's real context window, computed at session start |
| Indiscriminate delegation | Batch independent sub-tasks in parallel; don't delegate work small enough to do inline |
