# Inventario Playwright — 2026-09-21 (sola lettura)

## 1. MCP Playwright — config
- `~/.claude/.mcp.json`: DUE entry.
  - `playwright`: `npx @playwright/mcp@0.0.79 --cdp-endpoint http://127.0.0.1:9333 --storage-state ~/.claude/.playwright-mcp/storage-state.json --shared-browser-context` — **enabled: False**.
  - `playwright-visible`: `npx @playwright/mcp@0.0.79 --user-data-dir ~/.claude/.playwright-mcp-visible --storage-state .../visible/storage-state.json` — **enabled: False**.
- `~/.claude.json`: NESSUNA entry mcpServers per playwright (solo `skillUsage.playwright` usageCount 8). Allineati: nessun duplicato attivo.
- Progetto ai-router-switch: `.mcp.json` assente; nessun riferimento in `.claude/settings*.json`.
- Entrambi disabilitati (nessuno avvia npx playwright MCP ora). Il servizio CDP `playwright-headless-browser.service` è comunque `active` (porta 9333).

## 2. Versioni pacchetto MCP
- Cache npx: `~/.npm/_npx/51691537fc71f2b0/node_modules/@playwright/mcp` = **0.0.79**; `~/.npm/_npx/9833c18b2d85bc59/...` = **0.0.80**.
- Ultima su npm (`npm view @playwright/mcp version`): **0.0.82**.
- → Config pinnata a 0.0.79, due revisioni indietro (0.0.80 e 0.0.82 disponibili).
- `npm ls -g`: nessun playwright globale.

## 3. Libreria Playwright
- npx CLI: `npx playwright --version` → **Version 1.60.0**.
- pip: `playwright 1.60.0` (stesso numero, ambiente utente).
- `npm ls -g`: nessun `playwright`/`@playwright/test` globale.
- package.json di progetto che referenziano playwright (non node_modules):
  - /mnt/nvme2/projects/gstack/package.json
  - /mnt/nvme2/projects/Progetti/CS_Bridge_360/control-plane/frontend/package.json
  - /mnt/nvme2/projects/Progetti/ChromePlugin/package.json (+ reviews)
  - /mnt/nvme2/projects/Progetti/Coinempire/coinempire-web/package.json
  - /mnt/nvme2/projects/Progetti/PaperClip/package.json
  - /mnt/nvme2/projects/Progetti/keyok/sviluppo/keyok-gestionale/package.json
  - /mnt/nvme2/projects/Progetti/local-ai-stack/infographics/package.json
- pyproject con playwright: omnia-studio/tools/mcp-video, osint/repos/theHarvester (+ copia /mnt/backup omnia-studio).

## 4. Browser installati
- `~/.cache/ms-playwright/`: chromium-1223/1228/1234, chromium_headless_shell-1223/1228/1234, firefox-1522/1532/1538, webkit-2287, ffmpeg-1011, mcp-chrome-7041c52. Totale **3,1 GB** (3 revisioni per browser = candidate alla pulizia, le vecchie).

## 5. Fork/patch locali
- NESSUN fork sorgente: le cartelle `*playwright-mcp*` trovate sono tutti profili dati runtime (`~/.claude/.playwright-mcp`, `.playwright-mcp-visible`, e `.playwright-mcp` in 5 progetti). Usiamo il pacchetto ufficiale `@playwright/mcp` via npx, pinnato.

## 6. Skill/agenti
- `~/.claude/skills/playwright/SKILL.md` esiste (attiva, usageCount 8).
- Agente `browser-automation-expert`: `~/.claude/agents/experts/browser_automation_expert.md`.
- Altri riferimenti (docs/routing): agents/docs/SYSTEM_ARCHITECTURE.md, agents/INDEX.md, agents/tests/routing-validation.md, agents/system/INDEX.md.

## 7. Vault (decisioni già prese)
- `playwright-mcp-improvements-research-20260829` (status: implementato-1-3-4-5): applicati `--storage-state`, `--shared-browser-context` (solo headless), tool nativi browser_find/browser_tabs; SALTATO allowlist domini. Status pagina dice v0.0.79 "ultima" al 2026-08-29.
- `playwright-mcp-multisession-isolation-fix` (risolto): conflitto SingletonLock fra N istanze → prima `--isolated`, poi (decisione utente, rischio accettato) switch a **CDP sul Chrome reale** via `playwright-headless-browser.service` :9333 — la config attuale.
- Ulteriori pagine: `playwright-mcp-orphan-cleanup.md`, `playwright-mcp-warm-cdp-speedup-20260829.md` (non lette in dettaglio).
