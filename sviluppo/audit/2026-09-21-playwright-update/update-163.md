# Aggiornamento Playwright 1.63.0 — 2026-09-21

## Progetti npm (nessun commit: tutti i tree erano sporchi di modifiche estranee)

| Progetto | Package | Prima | Dopo | Gestore | Verifica |
|---|---|---|---|---|---|
| /mnt/nvme2/projects/gstack | playwright | ^1.62.1 | ^1.63.0 | bun (bun.lock, patch playwright-core@1.62.1 lasciata: punta ancora a 1.62.1) | bunx playwright --version = 1.63.0 |
| CS_Bridge_360/control-plane/frontend | @playwright/test | ^1.61.0 | ^1.63.0 | pnpm --no-frozen-lockfile (pnpm-lock.yaml + package-lock.json presentes; aggiornato il pnpm-lock) | npx playwright --version = 1.63.0 |
| ChromePlugin (root) | playwright | ^1.62.1 | ^1.63.0 | npm | 1.63.0 |
| ChromePlugin/sviluppo/reviews | playwright | ^1.60.0 | ^1.63.0 | npm | playwright-core 1.63.0 |
| ChromePlugin/sviluppo/autofix | playwright-core | ^1.61.1 | ^1.63.0 | npm | playwright-core 1.63.0 |
| Coinempire/coinempire-web | @playwright/test + playwright | ^1.61.1 (entrambi) | ^1.63.0 | npm | 1.63.0 |
| PaperClip | @playwright/test | ^1.58.2 | ^1.63.0 | pnpm --no-frozen-lockfile | 1.63.0 |
| keyok/sviluppo/keyok-gestionale | playwright | ^1.62.1 | ^1.63.0 | npm | 1.63.0 |
| local-ai-stack/infographics | playwright | ^1.61.0 | ^1.63.0 | npm | 1.63.0 |

Nota gstack: package.json contiene `"playwright-core@1.62.1": "patches/playwright-core@1.62.1.patch"` in patchedDependencies — NON aggiornato: il contenuto della patch è estraneo al mio mandato. Se la patch serve ancora a 1.63, va rinominata/riapplicata a mano.

## Python

- pip utente: playwright 1.60.0 → **1.63.0** (`pip install --user --break-system-packages -U playwright`; la PEP 668 del sistema richiedeva `--break-system-packages`). Verificato con `pip show playwright`.
- omnia-studio/tools/mcp-video (in /mnt/nvme2/projects/**SISTEMA**/omnia-studio, non Progetti): pyproject e uv.lock NON contengono playwright come dipendenza (solo la stringa ".playwright-mcp/"). Niente da fare.
- theHarvester (/mnt/nvme2/projects/Progetti/osint/repos/theHarvester): pin `playwright==1.60.0` → `playwright==1.63.0` aggiornato nel pyproject. Copie sotto /mnt/backup non toccate, come da istruzioni.

## Browsers.json di playwright-core 1.63.0 (revisioni richieste)

- chromium: 1243
- chromium_headless_shell: 1243 (nome interno `chromium-headless-shell`)
- firefox: 1543
- webkit: 2359
- ffmpeg: 1011

Fonte: /mnt/nvme2/projects/Progetti/keyok/sviluppo/keyok-gestionale/node_modules/playwright-core/browsers.json

## Cache ~/.cache/ms-playwright dopo `npx playwright@1.63.0 install chromium firefox`

Contenuto: b, chromium-1223, chromium-1228, chromium-1234, chromium-1243, chromium_headless_shell-1223/1228/1234/1243, ffmpeg-1011, firefox-1522, firefox-1532, firefox-1538, firefox-1543, mcp-chrome-7041c52, webkit-2287.
Dimensione totale: 4,1G.

Note: webkit-2359 NON scaricato (comando limitato a chromium firefox, come da istruzione). ffmpeg-1011 e chromium-1243/headless_shell-1243 erano già presenti. firefox-1543 scaricato in questa sessione.
