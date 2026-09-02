# Cross-platform audit — what actually breaks on Windows/macOS

Companion to `docs/WINDOWS-RESILIENCE-PLAN.md`, which covers the
process-supervision gap (auto-restart/watchdog/freeze-detect). This document
covers the rest: hardcoded paths, script portability, secrets, and external
binary dependencies. Every line below was checked against the source, not
inferred.

## macOS: better starting point than Windows, but not complete

`install.py`'s `setup_macos_service()` (around line 211) writes a real
launchd plist with `<key>KeepAlive</key><true/>` (verified: `install.py:249`)
— this **is** a working crash-restart, equivalent to systemd's
`Restart=always`. macOS is not in the same situation as Windows, where
`install.py` only writes a start-at-logon batch file with no restart logic
at all.

What macOS is still missing, same as Windows: the health-watchdog and
freeze-watchdog logic (`scripts/ai-router-watchdog.sh`,
`scripts/ai-router-freeze-watchdog.sh`) is systemd-timer-only. `launchd`
restarting a crashed process is not the same as detecting a process that's
alive but frozen (hung event loop, deadlock) — that needs the same kind of
heartbeat-file + health-endpoint polling described in
`docs/WINDOWS-RESILIENCE-PLAN.md`, just wired to a `launchd` periodic job
(`StartInterval`) instead of a Windows Scheduled Task. The mechanism-mapping
table in that document applies to macOS for the watchdog rows; the
"Restart=always" row does not apply since macOS already has `KeepAlive`.

## Three real bugs: hardcoded `/tmp/` breaks on Windows

Three places use a Linux/macOS-only path with no fallback, none going
through `src/paths.py` (which already resolves config directories
correctly per-OS: `AIROUTER_HOME` → `~/.claude` → XDG/APPDATA/Application
Support):

- `src/local_backend.py:163` — `SAVED_IMAGE_DIR = "/tmp/claude-local-images"`
- `src/ai-router-proxy.py:1240` — `_DEATH_LOG = "/tmp/ai-router-death.log"`
- `src/context_manager.py:33` — `self._db_path = db_path or "/tmp/ai-router-ctx.db"`,
  and the only call site, `CTX = ContextManager()` in
  `src/ai-router-proxy.py:39`, passes no `db_path` — so the default is not
  a theoretical fallback, it's what actually runs, unconditionally, on
  every proxy start. (An earlier pass classified this one as non-blocking
  on the assumption that a caller overrides it; that assumption was wrong —
  verified by grepping every `ContextManager(` call site, there is exactly
  one and it takes no arguments.)

On Windows, `/tmp` is not a valid absolute path, so each of these fails the
first time it's touched — `SAVED_IMAGE_DIR` on the first local-mode image
save, `_DEATH_LOG` on the first unhandled crash it tries to record,
`context_manager`'s SQLite file on the first attempt to open it, which
given the unconditional call site means at proxy startup itself. The fix is
`tempfile.gettempdir()` in place of the literal string in all three; not
yet applied, pending confirmation since it touches `src/`.

**Verified still present, unfixed, as of `HEAD 90ccb90`** (checked directly
against the working tree, not inferred from an earlier pass — a parallel
session committed twice to this repo in the meantime, which is why the
`local_backend.py` line number moved from an earlier count of 146 to the
163 above; the content of all three lines is unchanged).

## Confirmed OK, no action needed

- `src/secrets_provider.py` — the resolution chain (env var → `.env` →
  additional `.env` files → optional `secrets.sh` via `bash` if present →
  keyring) already skips the bash step gracefully via `shutil.which("bash")`
  and falls through to the `keyring` library, which is pure Python and
  reaches Keychain on macOS / Credential Manager on Windows without any
  D-Bus or `secret-tool` assumption.
- `src/updater.py` — `systemctl` calls are gated behind
  `shutil.which("systemctl")` and degrade to a logged no-op, not a crash.
- `notify-send` calls in `src/router_utils.py`, `src/glm_backend.py`,
  `src/qwen_backend.py` — best-effort desktop notifications, Linux-only
  binary, fail silently if absent. Cosmetic, not a portability blocker.
- `src/paths.py` itself uses `pathlib` consistently and resolves config
  directories correctly per-OS — the three hardcoded `/tmp/` paths above are
  the only ones bypassing it; a full `grep '"/tmp/' src/*.py` found no
  others.
- `scripts/ai-router-watchdog.sh` / `ai-router-freeze-watchdog.sh` are
  bash-only (curl, systemctl, `ss`) but are invoked exclusively from
  systemd timer units, never from `install.py` or any code path in `src/`
  — their absence doesn't break the base install on another OS, only the
  Linux-specific supervision layer they implement (tracked in
  `docs/WINDOWS-RESILIENCE-PLAN.md`).

## Status

**Fixed.** All three constants now build their path with
`os.path.join(tempfile.gettempdir(), ...)` instead of the literal `/tmp/...`
string — `tempfile.gettempdir()` resolves correctly per-OS (`/tmp` on
Linux/macOS unless `$TMPDIR` overrides it, `%TEMP%` on Windows). Verified:
`py_compile` on all three files, a runtime import confirming
`SAVED_IMAGE_DIR` resolves through `tempfile.gettempdir()`, a
`grep '"/tmp/' src/*.py` with zero remaining hits, and the full test suite
run before and after the change (`git stash`/`pop`) to confirm no test
depends on the literal path — same 907 passed / 28 failed either way, so
the 28 are a pre-existing, unrelated issue (see note below), not something
this change touched.

Everything else in this document is either already fine or tracked
separately in `docs/WINDOWS-RESILIENCE-PLAN.md`.

**Unrelated finding surfaced while verifying this fix**: the test suite
currently has 28 failing tests out of 935 (907 passed), pre-existing before
this change (confirmed via `git stash`). Past sessions recorded a "930
passed, 0 failed" baseline, so this looks like a regression introduced
since then — not investigated here, out of scope for this fix.
