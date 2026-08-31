# Deployment rules — read this before setting up a fresh clone

This document is written as a set of rules, not a narrative, because it is
meant to be followed by an AI agent (or a human) deploying this repository
for the first time, with zero prior context. Every rule below exists because
skipping it, or doing it "the obvious way" instead of the way described
here, has broken a real deployment before. Where that's the case, the rule
says so.

If you're looking for *why the router behaves a certain way once it's
already running* (cache, context, rate limits, per-mode reliability), that's
[`RELIABILITY-AND-PERFORMANCE.md`](RELIABILITY-AND-PERFORMANCE.md), not this
file. This file is only about getting a correct, working deployment in the
first place.

## 1. Prerequisites — verify before doing anything else

- **Python 3.10 or later.** The installer checks this and refuses below it
  (`install.py`, `PYTHON_MIN = (3, 10)`). Don't try to make it work on 3.9;
  nothing in this codebase targets it.
- **Runtime dependencies**: `aiohttp`, `brotli`, `multidict`, `Pillow`
  (imports as `PIL` — case matters, `pil` lowercase fails). Listed in
  `requirements.txt`. Install with `python3 -m pip install -r
  requirements.txt`, or let `install.py` offer to do it.
- **Optional, not required for the proxy to run**: `PySide6` (GUI panel
  only), `pytest`/`pytest-asyncio`/`ruff` (dev/test only), `keyring`
  (only if you want OS-keyring as an API-key source instead of `.env`).
  Do not treat any of these as blocking — the proxy itself needs only the
  four packages above.
- **Linux, macOS, or Windows.** The installer branches per-OS
  (`setup_linux_service`/`setup_macos_service`/`setup_windows_service` in
  `install.py`). Windows gets a startup file plus manual instructions, not
  an auto-managed service — don't assume feature parity with Linux/macOS.

## 2. Install with `install.py` — do not hand-roll the steps

```bash
git clone <this repo>
cd ai-router-switch
python3 install.py
```

**Why not do it manually:** the installer performs five steps that each
have a failure mode if skipped or reordered — dependency check, config
directory creation, `.env` bootstrap (never overwrites an existing file),
service registration, and a final summary. Flags: `--dry-run` (show what
would happen, change nothing), `--no-service` (skip service registration),
`--start` (enable and start immediately), `--yes` (non-interactive).

**An AI agent doing this unattended should run:**
```bash
python3 install.py --yes --start
```
then verify the result with the checks in §9 — `--yes --start` does not
mean "trust it worked," it means "don't stop to ask a human who isn't
there."

### 2.1 This is not a pip-installable package — do not try to make it one

`pyproject.toml` declares `packages = []` on purpose. Two structural reasons
make `pip install .` or `pip install ai-router-switch` wrong for this repo:

1. Modules under `src/` import each other by flat name (`import
   router_constants`, not `from ai_router_switch import router_constants`).
   This works at runtime because the process's own script directory
   (`src/`) is added to `sys.path` automatically when the entry point is
   launched from inside it — not because it's an installed package.
2. `src/ai-router-proxy.py` has a hyphen in its filename. It is **not a
   valid Python module identifier** and cannot be `import`ed by name — it
   only works as a direct script entry point (`python3
   src/ai-router-proxy.py`), which is exactly how the generated systemd
   unit and wrapper invoke it.

If you find yourself writing an `__init__.py` under `src/`, adding an
`entry_points` table, or trying to `pip install -e .`, stop — none of that
is how this project runs, and it will not make the proxy start correctly.

## 3. Configuration directory — resolution order matters

The router resolves its config directory in this exact order (`src/paths.py`):

1. `AIROUTER_HOME` environment variable, if set.
2. `~/.claude`, **if it already exists** (kept for users who also run
   Claude Code and want one shared config location).
3. OS default: `~/.config/ai-router-switch` (Linux), `~/Library/Application
   Support/ai-router-switch` (macOS), `%APPDATA%\ai-router-switch`
   (Windows).

**Do not hardcode any of these paths anywhere** (scripts, systemd units,
documentation you generate) — always resolve through this order, or your
deployment will read the wrong `.env` the moment `AIROUTER_HOME` is set
differently than you assumed. `install.py` and the generated systemd unit
already do this correctly via `paths.config_home()`; if you write anything
new that needs the config directory, import and call that function instead
of reimplementing the logic.

## 4. Secrets — where they go, and the one mistake that has actually happened

- API keys live in `.env` inside the config directory (§3), copied from
  `.env.example` by the installer on first run and **never overwritten** on
  subsequent runs — your keys survive a reinstall.
- **Never commit `.env`, anything under a `secrets/` directory, or any file
  containing a real key**, to this repository or any fork of it. Not even
  in a throwaway commit you plan to remove later — assume it's permanent
  the moment it's staged.
- Anthropic needs **no key here** — Claude Code's own OAuth token
  (`.credentials.json` or OS Keychain) is used directly.
- Every other provider's key is only required for the modes that use it:
  `MINIMAX_API_KEY` (minimax, mix-am, mix-gm and their `-2`/`-am`
  variants), `GLM_API_KEY` / `ZAI_API_KEY` (glm, mix-ag, mix-gm),
  `QWEN_API_KEY` / `DASHSCOPE_API_KEY` plus `QWEN_WORKSPACE_ID` (qwen),
  `LOCAL_LLM_API_KEY`/`LOCAL_LLM_API_BASE` (local, mix-al). **Leaving a key
  blank does not crash the router** — the mode that needs it will fail at
  request time with an explicit error (not a silent fallback to another
  provider), which is correct: don't "fix" this by making a missing key
  fall back to a different provider, that would hide the misconfiguration
  instead of surfacing it.
- **On Linux, the generated systemd unit reads secrets from three optional
  files, in this order**: `$CONFIG_HOME/.env`, `~/.secrets/minimax.env`,
  `~/.secrets/glm.env` — every `EnvironmentFile=` line is prefixed with `-`,
  meaning "optional, don't fail the service if this file is missing." **A
  real incident (2026-08-09) happened when a service template only loaded
  the first of these three files**: regenerating the unit from that
  template silently dropped the other two, and the router restarted
  without API keys, with no error at startup — the missing keys only
  surfaced as failed requests later. **If you regenerate or hand-edit the
  systemd unit, preserve every `EnvironmentFile=` line from
  `systemd/ai-router.service.in`, not just the obvious one.** A clean
  installation that only uses `.env` (no `~/.secrets/*.env` files) works
  fine on its own — the extra files are additive, not required — but if a
  previous deployment relied on them and a regeneration drops them, keys
  vanish without an obvious error.

## 5. Ports — do not assign one you haven't verified is free

The router listens on one dynamic port (default **8787**, follows whichever
mode is currently globally active) plus one fixed port per mode, from
`FIXED_PORTS` in `install.py` / `PORT_MODE` in `src/router_constants.py`:

```
8771 anthropic · 8772 minimax · 8773 mix-am · 8774 mix-al · 8775 glm ·
8776 mix-gm · 8777 mix-ag · 8778 qwen · 8779 local · 8781 mix-am-2 ·
8784 mix-gm-2 · 8785 mix-ag-2 · 8786 gpt · 8788 ultra · 8789 opr
```

**Port 8780 is deliberately skipped and must stay skipped** — it's occupied
by an unrelated service on the reference deployment (a TTS server), and the
list is written as an explicit enumeration rather than a range specifically
so this gap doesn't get silently closed by a future refactor. **Ports 8782
and 8783 are also unusable** in that same reference environment (another
`uvicorn` instance) — the router does not fail loudly on a bind conflict,
it silently skips the port, so a mode assigned to an occupied port will
return 404 instead of erroring at startup, which makes the failure look
like a routing bug instead of a port conflict.

**Rule: before assigning any new mode to any port, verify it's free with an
actual bind test on the target machine** (e.g. a throwaway `socket.bind()`
or `ss -tlnp` / `netstat`), never by reading this list or the source and
concluding "it's not mentioned, so it must be free." Availability is a
property of the machine you're deploying to, not of the repository.

## 6. The systemd service (Linux) — what each non-obvious setting is for

If you use `install.py`, this is generated for you from
`systemd/ai-router.service.in` — read this section to understand *why* the
generated unit looks the way it does before changing it, not to write one
from scratch.

- **`WorkingDirectory` is `src/`, not the repo root.** Combined with flat
  imports (§2.1), running from the wrong directory breaks module resolution
  at startup.
- **`ExecStart` invokes a generated wrapper script**, not the Python
  interpreter directly on the proxy file — the wrapper sets `PYTHONPATH`
  and `cd`s into `src/` before exec'ing. Environment variables inside a
  systemd unit's `Environment=` directive get truncated at the first space
  in a path, which breaks on machines with spaces in the install path; the
  wrapper avoids this by doing it in shell instead.
- **`TimeoutStopSec=20`, not the systemd default of 90s and not the
  aiohttp default of 60s per runner.** This value exists because a 2026-08-08
  stress audit found the proxy creating ten separate `AppRunner`s with no
  explicit `shutdown_timeout`, cleaned up sequentially — a theoretical 600s
  worst case against whatever `TimeoutStopSec` was set to, which reliably
  produced a hard `SIGKILL` mid-request on every restart. The proxy code
  now sets an explicit per-runner `shutdown_timeout` and cleans up in
  parallel (real drain time under 4s); the 20s in the unit is *margin*, not
  the expected time. Don't lower it back toward 8s "to make restarts
  faster" — that's exactly the value that produced the original incident.
- **`ExecStartPre` force-frees all router ports** (`fuser -k`) before
  start, tolerating a missing `fuser` (`|| true`). This exists because a
  previous failed restart can leave an orphaned process holding a port,
  which would otherwise make the new instance fail to bind with no clear
  message pointing at the real cause.
- **`Restart=always` with `RestartSec=2`, and `StartLimitIntervalSec=600` /
  `StartLimitBurst=20`** — the service is expected to restart frequently
  under some failure modes (OAuth token refresh, transient network issues)
  and should not be allowed to hit systemd's default rate-limit and end up
  `failed` after a handful of restarts in a short window. Don't remove or
  tighten these thresholds without understanding you're trading resilience
  for noise suppression.
- **`MemoryHigh=512M` / `MemoryMax=1G` / `LimitNOFILE=65536` /
  `TasksMax=512`** — sized for multiple concurrent SSE streams across up to
  ten listening ports. If you're running many concurrent long streaming
  sessions and see the process throttled or killed by the OOM policy
  (`OOMPolicy=continue`, `OOMScoreAdjust=-900` — deprioritized for OOM-kill
  but not exempt), raise these rather than assuming it's a router bug.

**Do not write a systemd unit from scratch for this service.** If the
template is missing, `install.py` falls back to a minimal unit that
preserves only the two properties that matter most (`WorkingDirectory=src/`
and `Restart=always`) — it is explicitly a degraded fallback, not a
recommended configuration. Prefer fixing/restoring the template over
relying on that fallback.

## 7. Network exposure — this is a security boundary, not a convenience toggle

- **Default: `AIROUTER_LISTEN_HOST=127.0.0.1`** — reachable only from the
  machine it runs on. This is the correct default for almost every
  deployment; do not change it without a specific reason.
- **If you change it to a non-loopback address** (`0.0.0.0`, a LAN IP, a
  Tailscale IP), you are exposing two classes of route to anyone who can
  reach that address:
  - `/debug/*` routes, which return diagnostic content including **the full
    body of the last forwarded request** (`/debug/trace` — system prompt
    and conversation included) and up to 2000 characters of upstream error
    bodies (`/debug/errors`).
  - `/admin/*` routes, including `/admin/mode/<mode>`, which **rewrites the
    router's global mode** — an unauthenticated network-exposed router
    lets anyone on that network hijack which provider every chat on the
    machine talks to.
- **The mitigation is `AIROUTER_DEBUG_TOKEN`.** Without it, `/debug/*` and
  `/admin/*` respond `404` unconditionally — closed by default, not merely
  undocumented. If you open the host, you **must** set this token and
  require it (as `X-Airouter-Debug-Token`, an `Authorization: Bearer`
  header, or a `?token=` query parameter) before considering the
  deployment safe. `/__router_health` is intentionally not covered by this
  guard — it's meant to be reachable for monitoring without a token.
- **Never** open the listen host to a non-loopback address as a way to
  "make setup easier" or to test from another device without first setting
  the token. There is no scenario in this project where that trade-off is
  worth it.

## 8. Wire the client

Point your Anthropic-compatible client at the router instead of Anthropic's
API directly:

```json
{ "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787", "API_TIMEOUT_MS": "300000" } }
```

(Ready-to-use fragment: `config/settings.anthropic.example.json`, meant for
Claude Code's `settings.json`.) The `300000`ms (5-minute) timeout is not
arbitrary — several backends (local models especially, see
`RELIABILITY-AND-PERFORMANCE.md`'s tool-schema-bloat section) can take
well past the client default before the first byte arrives; a shorter
client-side timeout will produce spurious failures that look like router
bugs but are actually the client giving up too early.

## 9. Verify the deployment before declaring it done

Run these in order; each one gates the next.

```bash
# 1. Tests pass (930 tests as of this writing — check the actual current count,
#    don't hardcode a number you read once).
python3 -m pytest -q

# 2. Service is active (Linux)
systemctl --user is-active ai-router      # expect: active

# 3. Health endpoint responds
curl -s http://127.0.0.1:8787/__router_health

# 4. Every mode you actually intend to use resolves and reaches its provider —
#    a request against a mode with no API key configured should fail with an
#    explicit provider error, NOT hang, NOT silently return an Anthropic
#    response, and NOT crash the process. If it does any of the last three,
#    something in setup is wrong, not the router's routing logic (which has
#    no silent cross-provider fallback by design).

# 5. Log is clean after startup — look for tracebacks in the first ~100 lines,
#    not just a nonzero exit code.
```

**Do not consider the deployment done just because `install.py` exited 0.**
The installer verifies *setup* steps (directories exist, dependencies are
importable, a service file was written); it does not verify that your API
keys are valid, that your chosen ports are actually free on this specific
machine, or that the service is still running five minutes after `--start`.
Steps 1–5 above are what actually proves the thing works.

## 10. Updating an existing deployment

Use `ai-mode update`, not manual `git pull` / `git reset` / a hand-rolled
restart. It performs, in order: refuse if there are uncommitted local
changes (no automatic stash — it will not discard work for you), `fetch`,
fast-forward-only `pull` (refuses to update if history has diverged,
doesn't force it), run the test suite, restart the service, wait for
health, verify every fixed port. **On any step failing, it rolls back to
the previous commit and restarts** — the deployment is never left on a
partially-updated, untested commit. Options: `--check` (report only),
`--dry-run`, `--no-test`, `--no-restart`, `--yes`. A weekly update timer
exists in `systemd/` but is **not installed by default** — enable it
explicitly if you want unattended updates, and understand that means
letting the router update and restart itself without a human in the loop.

## 11. Optional: the THINK/ACT/VERIFY governance layer

The router itself has no opinion about *who* calls it or *why* — see
`RELIABILITY-AND-PERFORMANCE.md`'s "Delegation & context governance layer"
section for the full explanation of why mode-switching alone doesn't move
execution off your orchestrator model without a client-side delegation
discipline. A ready-to-install starter kit for Claude Code specifically
lives in `docs/claude-hierarchy/`: a `CLAUDE.md` snippet, two hooks
(`enforce_agent_model.py`, `enforce_delegate_write.py`), and the
`settings.json` entries to wire them in. This is optional — the proxy works
without it — but if your symptom after a correct deployment is "every mode
still seems to route everything through the same orchestrator model,"
this is almost certainly the missing piece, not a router bug.

## 12. Self-fixer — off by default, keep it that way unless you mean it

A component exists that watches for recurring errors and can have a model
write a proposed fix. It only activates if `AIROUTER_SELF_FIX_ENABLED=1` is
set, and even then it **never merges automatically** — it opens a branch,
and a pull request if `gh` is available. Automatic merging requires the
separate, explicit `--merge` flag. **Do not enable this as part of a
standard deployment.** If you do enable it, understand you are letting a
model write to your own repository, and `--merge` means letting it do so
without human review.

## Summary — do / do not

| Do | Do not |
|---|---|
| Run `install.py`, let it manage directories/service/`.env` | Hand-roll the systemd unit, the config directory, or the `.env` bootstrap |
| Verify port availability with a real bind test | Assume a port is free because it's absent from `FIXED_PORTS` |
| Keep `AIROUTER_LISTEN_HOST=127.0.0.1` unless you specifically need remote access | Open the host to `0.0.0.0`/LAN/Tailscale without setting `AIROUTER_DEBUG_TOKEN` first |
| Leave unused providers' API keys blank | "Fix" a blank key by making the router fall back silently to another provider |
| Preserve every `EnvironmentFile=` line when touching the systemd unit | Regenerate the unit from a template that only loads one secrets file |
| Use `ai-mode update` for updates | `git reset --hard` / force-push a running deployment to update it |
| Treat `install.py` exiting 0 as "setup ran," then run the §9 checklist | Treat `install.py` exiting 0 as "done" |
| Install the optional `docs/claude-hierarchy/` kit if you need the ACT role to actually engage | Assume switching modes alone moves execution off the orchestrator model |
| Leave the self-fixer off unless you specifically want it | Enable `AIROUTER_SELF_FIX_ENABLED` + `--merge` as part of a default setup |
