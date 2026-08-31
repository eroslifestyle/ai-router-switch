# ai-router-switch

Self-hosted routing proxy for Claude Code and any client that speaks the Anthropic API format. One local endpoint at `http://127.0.0.1:8787`; switch the backend without restarting the application. MIT licensed.

The router only selects the backend. It does not touch the client's settings, skills, agents, MCP servers, tools, or system prompt.

```
client (VSCode / Claude Code / any Anthropic-format client)
        │  ANTHROPIC_BASE_URL = http://127.0.0.1:8787
        ▼
   ai-router  ──►  the active mode decides the backend
                        │
   ┌──────────┬─────────┼─────────┬──────────┐
   ▼          ▼         ▼         ▼          ▼
Anthropic  MiniMax   GLM/z.ai   Qwen    local model
```

## Modes (source: VALID_MODES in src/router_constants.py)

| Mode | THINK | execution |
|---|---|---|
| anthropic | Claude | Claude |
| minimax | MiniMax | MiniMax |
| glm | glm-5.3 | glm-4.7 |
| qwen | qwen3.8-max | qwen3-coder-plus |
| mix-am | Claude | MiniMax |
| mix-ag | Claude | GLM |
| mix-gm | GLM | MiniMax |
| mix-al | Claude | local model (code-max) |
| local | local model | local model |
| gpt | local model (code-max) | local model (code-max) |
| opr | OpenRouter/ox-alpha | OpenRouter/ox-alpha |
| ultra | Claude | GLM (MiniMax for code via CLI) |
| mix-am-2 | Claude | MiniMax (deny enforcement) |
| mix-ag-2 | Claude | GLM (deny enforcement) |
| mix-gm-2 | GLM | MiniMax (deny enforcement) |

Legacy aliases accepted: `mixed` = mix-am, `glm-minimax` = mix-gm, `anthropic-glm` = mix-ag, plus the short forms `mixam`, `mixag`, `mixgm`, `mixal`. The `-2` variants have identical routing but stricter delegation enforcement via hooks.

## Ports (source: PORT_MODE in src/router_constants.py)

8787 is dynamic and tracks the current mode. One fixed port per mode: 8771 anthropic, 8772 minimax, 8773 mix-am, 8774 mix-al, 8775 glm, 8776 mix-gm, 8777 mix-ag, 8778 qwen, 8779 local, 8781 mix-am-2, 8784 mix-gm-2, 8785 mix-ag-2, 8786 gpt, 8788 ultra, 8789 opr.

## Switching the backend

- Globally, for every application: `ai-mode <mode>`. Other subcommands: `ai-mode status`, `log`, `start`, `stop`, `restart`, `update`.
- Per-chat, scoped to the conversation: send `!router <mode>` inside the chat. Also available: `!router status`, `!router reset`, `!router help`. Natural-language routing was removed on 2026-07-26 because ordinary sentences were switching modes without the user asking; only the explicit `!router` form is intercepted.
- From the terminal, scoped to one session: `scripts/router <mode>` passes through the same parser as the proxy and sets the mode only for the current session (identified by `CLAUDE_CODE_SESSION_ID`); it exists because an initial `!` is eaten by the CLI shell so `!router …` never reaches the proxy, and unlike `ai-mode` it does not rewrite the global mode file, leaving other chats untouched. Install it like the other commands, by symlinking it into a directory on your `PATH`: `ln -s "$PWD/scripts/router" ~/.local/bin/router`.
- Per fixed port: point `ANTHROPIC_BASE_URL` at the port of the desired mode.

## Installation

Full rules for a correct, unattended deployment (ports, secrets, systemd,
network exposure): `docs/DEPLOYMENT-RULES.md`. This section is the quick
version.

Requirements: Python 3.10 or later. Runtime dependencies are `aiohttp`, `brotli`, `multidict`, and `Pillow` (see requirements.txt). `PySide6` is needed only for the optional GUI panel.

Steps:

1. Clone the repository.
2. Run `python3 install.py`.

The installer checks Python and the dependencies, creates the configuration directory, copies `.env.example` without ever overwriting an existing `.env`, and registers a service: a user systemd unit on Linux, a launchd plist on macOS, a startup-file plus instructions on Windows.

Options: `--dry-run`, `--no-service`, `--start`, `--yes`.

Then set `"ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"` in Claude Code's settings.json. A ready-to-use fragment is in `config/settings.anthropic.example.json`.

Optional: if you want Claude Code itself to respect the THINK/ACT role split the router is built for (never call a subagent without an explicit model, never let the planning model write project code directly in mixed modes), paste the ready-made prompt in `docs/claude-hierarchy/README.md` into a fresh Claude Code session.

## Where configuration lives

The router resolves its configuration directory in this order: the `AIROUTER_HOME` environment variable; then `~/.claude` if it exists (backwards compatibility); then `~/.config/ai-router-switch` on Linux, `~/Library/Application Support/ai-router-switch` on macOS, `%APPDATA%\ai-router-switch` on Windows.

API keys go into the `.env` file in that directory. The router looks for them in order across: environment variables, the `.env` file, additional `.env` files specified, the `secrets.sh` script if present and bash is available, and finally the system keyring.

For Anthropic no key is required: the OAuth token is handled by Claude Code, in `.credentials.json` or in the Keychain on macOS.

## Updating

`ai-mode update` runs the following steps in order: refuse if there are uncommitted local changes (no automatic stash), `fetch`, `pull` in fast-forward only, the test suite, restart the service, wait for health, and verify all fixed ports. If a step fails it rolls back to the previous commit and restarts. Options: `--check`, `--dry-run`, `--no-test`, `--no-restart`, `--yes`. A weekly timer exists; it is NOT installed by default.

## Local model

The `local` and `mix-al` modes talk to an OpenAI-compatible endpoint on port 4000. The `local-stack` directory contains a docker-compose file, example configuration, and a llama.cpp systemd unit template. Model weights are not included.

## Self-fixer (optional, off by default)

A component exists that analyzes recurring errors and tries to propose a fix by having a model write it. It is off unless `AIROUTER_SELF_FIX_ENABLED` is set to `1`, and by default it never merges: it opens a branch and, if `gh` is available, a pull request. Automatic merging is opt-in via `--merge`. Turning it on means letting a model write to your own repository.

## Resilience

The systemd service runs with `Restart=always`. A watchdog script lives in the `scripts` directory. A resilience module enters a degraded state when the OAuth token is missing or expired and exits it on its own as soon as the user logs in again.

## Network exposure and the /debug routes

By default the router binds to 127.0.0.1, which is the recommended configuration.

The routes prefixed with `/debug/` echo back the contents of requests passing through the router. `/debug/trace` includes the full body of the most recent request forwarded to the upstream, therefore the system prompt and the conversation, while `/debug/errors` includes up to 2000 characters of the error body returned by the upstream. On the loopback interface these routes are not reachable from the network and stay open, which makes local diagnostics convenient.

If `AIROUTER_LISTEN_HOST` is set to a non-loopback address those routes stop being local, and from that point on the router requires `AIROUTER_DEBUG_TOKEN`. Without a configured token every `/debug/` route responds with 404. With a token configured, the token has to be presented in the `X-Airouter-Debug-Token` header, or as `Authorization: Bearer <token>`, or as a `?token=` query parameter. The `/__router_health` route is unaffected, so monitoring keeps working. The same guard also covers routes prefixed with `/admin/`, including `/admin/mode/<mode>` which rewrites the router's global mode; without it, a network-exposed router would let anyone hijack the mode of all chats.

## Tests

930 tests. Run them with `python3 -m pytest -q` from the repository root.

## What is NOT included

Model weights. API keys. Provider accounts. The router itself does not grant access to any model: it routes to services the user is already subscribed to.

## Documentation

- Italian manual: `docs/MANUAL.md`
- English manual: `docs/MANUAL.en.md`
- Project notes: `docs/PIANO.md`
- Deployment rules — read before a fresh deploy: `docs/DEPLOYMENT-RULES.md`
- Reliability, cache & context — engineering history: `docs/RELIABILITY-AND-PERFORMANCE.md`
- Client-side token economy — configuring the terminal to avoid waste: `docs/CLIENT-TOKEN-ECONOMY.md`
- Release history: `CHANGELOG.md`
- Local stack: `local-stack/README.md`
- Claude Code hierarchy bootstrap (ready-to-paste prompt): `docs/claude-hierarchy/README.md`
