# Ricerca update Playwright + @playwright/mcp — 2026-09-21

Fonti: `npm view` (versioni/dati npm), `gh release view/list` (note release verbatim), GitHub API issues.
Versione attuale in cache npx locale: **^0.0.79** e **^0.0.80** (`~/.npm/_npx/*/package.json`).

## Versioni (verificate via npm/gh)
| Pacchetto | Ultima | Data |
|---|---|---|
| @playwright/mcp | 0.0.82 | 2026-09-18 |
| @playwright/mcp | 0.0.81 | 2026-09-14 |
| @playwright/mcp | 0.0.80 | 2026-09-01 |
| @playwright/mcp | 0.0.79 | 2026-08-06 |
| playwright / @playwright/test | 1.63.0 | 2026-09-04 |
| playwright 1.62.0 | | 2026-07-24 |

## Novità 0.0.80 → 0.0.82
### 0.0.80 (2026-09-01)
- `browser_start_recording` / `browser_stop_recording` (--caps=devtools): registra azioni umane e le restituisce come codice Playwright.
- `browser_take_screenshot` non downscala più in silenzio.
- codegen: linguaggio inferito dall'ambiente; extension mode onora `--user-data-dir`.

### 0.0.81 (2026-09-14)
- WebMCP: `browser_webmcp_list`/`browser_webmcp_call` (sperimentale).
- `--profile-dir-name <name>`: scelta profilo Chrome in extension mode multi-profilo.
- **Idle timeout**: browser headless chiusi dopo 1h senza tool call (`--idle-timeout <ms>`, 0 disabilita). Fix pertinente al leak di processi chromium.
- `--image-responses only`: risposte = sole parti immagine.
- Sicurezza: controllo file segue i symlink (anti-escape workspace); extension mode: `browser_close` sicuro con `--shared-browser-context`; recording separati per client; Chromium sandbox disabilitata di default su Linux per il Chromium bundle.
- Extension protocol v1 rimosso (in 0.0.79).

### 0.0.82 (2026-09-18)
- WebMCP evoluto: i tool della pagina compaiono direttamente come `webmcp_<tool>` in tools/list (sostituisce i due tool di 0.0.81, che NON sono più esposti). Opt-out `--no-webmcp`.
- `browser_start_video`: `fps: 60`, `cursor: true`, stile marker azioni (--caps=devtools).
- **`browser_emulate_media`**: colorScheme/reducedMotion/forcedColors/contrast/media a runtime.
- `--file-paths=absolute`: path assoluti nei risultati (snapshot/screenshot/download).
- Fix: `browser_file_upload` fallito mantiene il file chooser aperto; `browser_route` valida header; `browser_storage_state` bypassa service worker.

## 0.0.79 (2026-08-06)
- Screenshot WebP (`type`); `--codegen` python/java/csharp; `--timeout-settle` (default 500ms); `--snapshot-boxes` globale; rimosso `--output-mode`; reconnect al browser dopo disconnect.
- Extension mode: valida Host/Origin sul relay CDP; rimosso protocol v1.

## Flag CLI rilevanti oggi (2026-09-21)
`--isolated`, `--browser`, `--caps` (ora: devtools, network, storage), `--storage-state`, `--extension`, `--shared-browser-context`, `--profile-dir-name`, `--idle-timeout`, `--image-responses`, `--file-paths`, `--snapshot-boxes`, `--timeout-settle`, `--codegen`. headless/headed invariati.

## Breaking changes da ^0.0.79/^0.0.80 → 0.0.82
- Nessun cambio major; v0.0.x = pre-1.0, breaking minor possibili.
- 0.0.82: `browser_webmcp_list`/`browser_webmcp_call` RIMOSSI (sostituiti da tool dinamici `webmcp_*`). Solo se li usavi.
- 0.0.81: `browser_close` con `--shared-browser-context` ora restituisce errore invece di buttare il backend (cambio comportamento).
- 0.0.79: rimosso `--output-mode`, rimosso extension protocol v1.
- Da 0.0.81: headless auto-chiusi dopo 1h idle (cambio comportamento; disabilita con `--idle-timeout 0`).
- Screenshot: da 0.0.80 byte originali, non più downscalati (payload più grandi per i client).

## Playwright 1.63.0 (2026-09-04)
- Test locks (nome → serializzazione cross-worker/file/progetti).
- `page.frameLocator()` senza selector cerca in tutti i frame del subtree.
- `locator.visible()`.
- test.step con `subtitle`/`params`; trace con aria+screen snapshots (`snapshots: {dom,aria,screen}`), "Display Aria" nel trace viewer.

## Alternative
- browser-use/browser-use: ⭐115.545, push 2026-09-18 — attivissimo.
- browserbase/stagehand: ⭐24.648, push 2026-09-20 — attivo.
- executeautomation/mcp-playwright: ⭐5.650, push 2025-12-13 — STAGNANTE (9 mesi), non consigliato.
Nota: il repo microsoft/playwright-mcp rimanda a microsoft/playwright per le issue (#1664).

## Problemi aperti sentiti (GitHub API, sort updated/reactions)
- #1757: initializeServer si blocca 30s se un tab di background è discarded da Chrome Memory Saver con `--cdp-endpoint` su profilo reale.
- #1754: crash del server scaricando file con profilo persistente su Windows.
- Le issue "multi-session/profili concorrenti" aperte pesano poco: le race note sono state affrontate in 0.0.81 (recording per-client, browser_close protetto con shared context).
- Leak chromium: mitigato ufficialmente dall'idle-timeout di 0.0.81.

## Rischio/raccomandazione (nota per il caller)
- Aggiornare da ^0.0.79/^0.0.80 a 0.0.82 è low-risk: niente major, breaking limitati a WebMCP (probabilmente non usato) e `--output-mode` (rimosso prima).
- Utili per noi: `--idle-timeout` (leak chromium), `browser_emulate_media`, `--file-paths=absolute`, recording (--caps=devtools).
