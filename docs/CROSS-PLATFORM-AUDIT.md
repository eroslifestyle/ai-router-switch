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

## Two real bugs: hardcoded `/tmp/` breaks on Windows

Two module-level constants use a Linux/macOS-only path with no fallback:

- `src/local_backend.py:146` — `SAVED_IMAGE_DIR = "/tmp/claude-local-images"`
- `src/ai-router-proxy.py:1240` — `_DEATH_LOG = "/tmp/ai-router-death.log"`

Neither goes through `src/paths.py` (which already resolves config
directories correctly per-OS: `AIROUTER_HOME` → `~/.claude` → XDG/APPDATA/
Application Support). On Windows, `/tmp` is not a valid absolute path, so
whichever of these two runs first fails outright the first time it's
touched — `SAVED_IMAGE_DIR` on the first local-mode image save,
`_DEATH_LOG` on the first unhandled crash it tries to record. The fix is
`tempfile.gettempdir()` in place of the literal string; not yet applied,
pending confirmation since it touches `src/`.

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
- `src/paths.py` and the rest of the config/path resolution use `pathlib`
  consistently; no other hardcoded POSIX path was found in `src/`.
- `scripts/ai-router-watchdog.sh` / `ai-router-freeze-watchdog.sh` are
  bash-only (curl, systemctl, `ss`) but are invoked exclusively from
  systemd timer units, never from `install.py` or any code path in `src/`
  — their absence doesn't break the base install on another OS, only the
  Linux-specific supervision layer they implement (tracked in
  `docs/WINDOWS-RESILIENCE-PLAN.md`).

## Status

Documented, not yet fixed. The two `/tmp/` constants are a small, low-risk
change (swap for `tempfile.gettempdir()`); everything else here is either
already fine or tracked separately in `docs/WINDOWS-RESILIENCE-PLAN.md`.
