---
tipo: progetto
stato: pianificazione-completa
creato: 2026-06-23
tags: [ai-router, proxy, claude-code, minimax, headroom, piano]
---

# AI Router v2 — Piano Definitivo

> Router proxy intelligente davanti a Claude Code / Pi / Antigravity e qualsiasi app
> formato Anthropic. Punto unico `:8787`, switching interno, fallback automatico,
> compressione, verifica incrociata, isolamento per progetto/chat.

## 🔴 REGOLA OBBLIGATORIA — NON-INTERFERENZA (precedenza assoluta)

> **Ogni modello/app segue i SUOI settings: skill, agent, MCP, tool, system prompt,
> context — TUTTO. Il router NON tocca, NON inietta, NON altera MAI nulla.**
> È solo un instradatore di backend. Precedenza su qualsiasi altra feature.

**Il router PUÒ solo:** scegliere backend (modalità) · fallback/cooldown ·
intercettare comandi `!router` espliciti · comprimere il TRASPORTO (headroom)
**preservando byte-per-byte** system/tool/skill/agent/MCP.

**Il router NON farà MAI:** ❌ alterare tool/skill/agent/MCP (no EXCLUDE_TOOLS) ·
❌ toccare il system prompt · ❌ modificare settings di alcuna app ·
❌ cambiare comportamento/decisioni del modello.

**Tecnica:** headroom byte-faithful (no exclude-tools) · body inoltrato identico
(tranne `model`→backend + header auth MiniMax) · verifica T2 = layer separato.

## 🎯 Differenziatori (da ricerca competitor)
Nessun prodotto fa **tutto** insieme. Confronto:
- **claude-code-router** (35k⭐): routing per scenario, `/model`, multi-provider — **MA no fallback automatico** (issue aperta), no compressione, no verifica.
- **LiteLLM** (51k⭐): ha fallback/cooldown — ma OpenAI-format, no compressione, no verifica.
- **Cline/Roo/Continue**: agenti VSCode, altra UX, no fallback cross-provider trasparente.
- **Noi**: unici con **fallback + compressione + verifica incrociata** trasparenti a Claude Code, self-hosted.

## 📋 Le 31 decisioni (Q&A con l'utente)

| # | Tema | Scelta |
|---|------|--------|
| 1 | Situazioni d'uso | tutte (multi-finestra/progetto/chat/app/terminale) |
| 2 | Modalità per progetto | isolo quando voglio (default condiviso) |
| 3 | Chat stessa finestra | **indipendenti** |
| 4 | Identificare chat | **fingerprint conversazione** (hash system+primo msg) |
| 5 | Marca chat | comando + auto |
| 6 | Persistenza marca | **sì, su file** |
| 7 | Comando in chat | `!router X` + **frasi naturali** ("usa solo claude") |
| 8 | Risposta al cambio | conferma breve |
| 9 | Comandi extra | status / reset / help |
| 10 | Auto-routing per scenario | **RIMOSSO** — il context lo gestisce ogni modello |
| 11 | Soglia longContext | **DECADE** — non è compito nostro |
| 12 | Gestione context | competenza di ogni modello (Claude/MiniMax), non del proxy |
| 13 | Verifica T2 | auto (task delicati) + on-demand |
| 14 | Controllore | Opus se disponibile + tieni bozza se occupato |
| 15 | Backend bloccato | cooldown + bidirezionale |
| 16 | App | tutte (VSCode, Pi, Antigravity, terminale) |
| 17 | Formati | Anthropic ora, OpenAI poi |
| 18 | Priorità | equilibrio qualità+costo+uptime |
| 20 | Gerarchia regole | "solo X" rigido; mixed/interactive ottimizzano |
| 21 | Verifica in solo-MiniMax | 2° passaggio MiniMax (self-check) |
| 22 | App che orchestrano (Pi) | proxy si fa da parte (solo compress+fallback) |
| 23 | Verifica vs streaming | ok perdere streaming sui task verificati |
| 24 | Frasi naturali | riconoscimento prudente |
| 25 | Osservabilità | `!router stats` (modalità+backend+token+fallback+costi) + log file |
| 35 | Task "delicati" da verificare | calcoli, sicurezza, decisioni, codice produzione |
| 36 | Context | NON nostro — routing SOLO di modello |
| 37 | Stats | tutto: modalità, backend, token risparmiati, fallback, costi |
| 26 | Pulizia fingerprint | auto dopo 7 giorni |
| 27 | Entrambi giù | messaggio chiaro (no Ollama) |
| 28 | Key MiniMax | vault cifrato |
| 29 | Aggancio app | io le aggancio + script per le future |
| 30 | OpenAI format | ignora finché non serve |
| 31 | Salva piano | sì (questo documento) |

## 🏗️ Architettura

```
App (VSCode/Pi/Antigravity/terminale) → :8787 (router, punto unico)
  ├─ identifica chat via FINGERPRINT (hash system+primo msg utente)
  ├─ intercetta comandi in-band (!router X / frasi naturali)
  ├─ applica gerarchia regole (solo-X rigido / mixed flessibile)
  ├─ auto-routing TRADUTTORE (rispetta model scelto dall'app; +longContext>100k→M3)
  ├─ verifica T2 (task delicati): Opus, o self-check MiniMax in solo-minimax
  ├─ circuit breaker + cooldown bidirezionale
  └─ backend:
       ├─ headroom#1 :8791 → api.anthropic.com (compressione)
       └─ headroom#2 :8790 → api.minimax.io/anthropic (compressione)
```

## 🔧 Soluzioni tecniche (da deep research SearXNG+MiniMax)

### Nodo 1 — Multi-porta affidabile
**Problema:** `get_extra_info("sockname")` inaffidabile con runner condiviso.
**Soluzione:** una `web.Application` SEPARATA per porta, modalità cablata in
`app["forced_mode"]`. Handler legge `request.app["forced_mode"]`. Deterministico.
Porte fisse `:8781-8784` + `:8787` dinamica.

### Nodo 2 — Fingerprint conversazione (TESTATO ✓)
`sha256(system + "||" + primo_messaggio_utente)[:12]`. Stabile su tutti i turni
della stessa chat, distinto tra chat. Persistito su file JSON con timestamp →
pulizia auto >7 giorni.

### Nodo 3 — Comandi in-band
Ispeziona ultimo messaggio user. Pattern prudente:
- esplicito: `^!router\s+(\w+)`
- naturale: `(usa|passa a|metti).*(solo claude|solo minimax|mod\w* mist|mixed)`
Se match → gestione locale + risposta sintetica (no inoltro al modello).
Conferma breve sempre (evita falsi positivi).

### Nodo 4 — Circuit breaker async
Custom leggero (no dipendenze): stato per backend
`{fails, open_until}`. `fail_max=3` → `open` per `cooldown=120s` → `half-open`
(1 tentativo) → chiude se ok. Ispirato a aiobreaker (`fail_max`/`reset_timeout`).
Bidirezionale (vale Claude↔MiniMax).

### Nodo 5 — SSE buffering per verifica
Task T2: forza `stream:false` interno, ottieni risposta completa, verifica Opus,
poi riemetti come **SSE sintetico** (`message_start`→`content_block_delta`→
`message_stop`). Già implementato `_sse_from_message()`. Task non-T2: passthrough
streaming diretto.

## 🚀 Deploy (aggiornato 2026-06-23)

### Symlink runtime → repo (anti-drift)
Il servizio systemd `ai-router.service` punta a `/home/mrxxx/.claude/scripts/ai-router-proxy.py`.
**Da oggi quel file è un symlink** a `src/ai-router-proxy.py` (repo). Vantaggi:
- Commit a `src/` si propagano al servizio live al prossimo restart (no drift).
- Una sola fonte di verità (`src/`).
Backup della copia precedente: `/tmp/ai-router-proxy.py.backup-20260623`.

### Porte (audit 2026-06-23)
Tutte le Description dei 3 servizi systemd ora indicano la porta reale:
- `:8787` ai-router (punto unico, dinamico)
- `:8790` headroom-minimax → MiniMax
- `:8791` headroom-proxy → Anthropic
Watchdog (`ai-stack-guard.sh`) copre crash/OOM ogni minuto, nohup come fallback.

## 📐 Fasi di build (ordine D19, aggiornato)
1. **Fix isolamento porte** (app separate per porta, deterministico)
2. **Fingerprint chat** (identificazione + persistenza file + pulizia 7gg)
3. **!router in chat** (comando IT + frasi naturali prudenti + status/reset/help)
4. **Circuit breaker + cooldown** (bidirezionale, fail_max=3, cooldown=120s)
+ **stats/log** (`!router stats`), **aggancio app** (script), **verifica T2** già presente.

> RIMOSSO "routing auto per scenario/longContext": il context lo gestisce ogni
> modello. Il proxy fa SOLO routing di MODELLO (quale backend).

## 🛡️ Conservato (già attivo)
4 modalità · fallback bidirezionale · compressione headroom×2 · verifica T2 ·
resilienza systemd+watchdog+linger · key vault cifrato.

## 🔧 Decisioni operative (round 11-14)
- **D32** frasi naturali in **italiano**
- **D33/D38** Pi su **porta dedicata** → solo compressione+fallback (no verifica/comandi)
- **D34** cambio modalità in chat vale **dal messaggio successivo** (non rompe task)
- **D39** comando/fingerprint guardano **solo i messaggi veri** dell'utente (ignora tool/agentic)
- **D40** conferma cambio = **risposta in chat** a costo zero (non consuma il modello)
- **D41** backup automatico **prima di ogni fase**
- **D42** **test automatici** dopo ogni fase (kill/switch/fingerprint/comandi) con prove
- **D43** **zero downtime**: VSCode resta attivo durante i lavori

## ⚖️ Gerarchia regole (D20)
1. Comando in-band utente (massima priorità)
2. Modalità "solo X" → rigida (no auto-routing che la viola)
3. Modalità mixed/interactive → auto-routing attivo
4. App che orchestrano (Pi) → proxy si fa da parte
