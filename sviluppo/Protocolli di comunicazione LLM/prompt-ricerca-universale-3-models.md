# Prompt Universale — Analisi Comunicazione Multi-Modello 2026

**Istruzioni**: Incolla questo prompt su 3 chat separate (Anthropic, MiniMax, GLM). Ogni modello eseguirà ricerche indipendenti e proporrà soluzioni. Alla fine, confronta i 3 output e redige un piano unico.

---

## COPIA DA QUI IN GIÙ

```
# TASK: Analisi Approfondita Comunicazione Multi-Modello 2026 per AI Router Proxy

Sei un architect senior specializzato in sistemi multi-LLM. Il tuo compito è:
1. Fare ricerche web approfondite su GitHub, HuggingFace, e arXiv per le best practice 2026
2. Analizzare l'architettura del router proxy fornita di seguito
3. Valutare i 3 piani esistenti e l'audit comparativo
4. Proporre la TUA soluzione ottimale

## CONTESTO — Il Progetto

`ai-router-switch` è un proxy reverse che intercetta le chiamate API verso Anthropic e le ridirige verso modelli multipli (Anthropic, MiniMax, GLM) con 7 modalità operative.

### Architettura Attuale

**File principale**: `src/ai-router-proxy.py` (4.294 righe) — router proxy async (aiohttp)
**7 modalità operative**: `anthropic` | `minimax` | `mix-am` | `mix-ag` | `mix-gm` | `glm` | `inverse`

**Pipeline collaborative** (la core feature):
- `mix-am`: Anthropic THINK → MiniMax ACT → (opzionale) Haiku rescue
- `mix-ag`: Anthropic THINK → GLM ACT (tiered: 5.2→4.7→4-turbo) → Anthropic VERIFY
- `mix-gm`: GLM THINK → MiniMax ACT → GLM VERIFY

**Moduli supporto**:
| File | Righe | Ruolo |
|---|---|---|
| `src/glm_backend.py` | 846 | Zhipu AI integration con tiering + peak scheduler |
| `src/providers/base.py` | 153 | Utility condivise, T2 classification |
| `src/pipelines/primitives.py` | ~83 | Costruzione body THINK/ACT/FINALIZE |
| `src/fail_tracker.py` | — | Fail tracking per-chat per escalation |
| `src/streaming_relay.py` | — | SSE streaming con riscrittura model |
| `src/context_manager.py` | — | Soglie 80%/90%/100% + tracking SQLite |
| `src/context_rewrite.py` | — | Tool pruning + head+tail (6+6 messaggi) |
| `src/context_shrink.py` | — | Compressione adattiva con HHEM learn loop |
| `src/summarizer.py` | — | LLM summarization con cache 24h |
| `src/model_context_map.py` | — | Context window per-model + 20% buffer |
| `src/token_counter.py` | — | Cache 30s + stima byte//4 |

**Sistema ottimizzazione token GIÀ implementato** (10+ layer):
Token Counter (30s cache) → Context Rewrite (tool pruning) → Context Shrink (HHEM adaptive) → Context Manager (soglie) → Summarizer (LLM compression 24h cache) → Model Context Map (limits) → Caching multi-livello (token 30s, keys 60s, chat 5s, summaries 24h)

**Error handling**: Multi-layer classification (400 context / 429 rate-limit / 500 server) + fallback chain (executor → Haiku → Anthropic → 502) + exponential backoff (5/10/20/40/60s) + per-model sliding windows (RPM/TPM)

### 5 Bug Reali Diagnosticati

| # | Severità | Problema |
|---|---|---|
| 1 | CRITICA | Il piano generato da Anthropic THINK non viene iniettato nei messaggi passati a MiniMax ACT. MiniMax opera senza contesto delle decisioni Anthropic. |
| 2 | MEDIA | Dual shrink inconsistente: pre-check chiama `_shrink_and_retry_minimax` ma path 400 dentro executor loop salta shrink e va diretto a rescue. |
| 3 | MEDIA | Race condition sul trim-state file: richieste concorrenti con stesso fingerprint leggono trim stale. |
| 4 | BASSA | `mixed_fail_last_status` non impostato quando `forward_minimax` lancia eccezione → check 429 successivo sempre False. |
| 5 | MEDIA | Fast-path 400: se shrink ritorna 400 di nuovo, cade silenziosamente senza handling. |

## I 3 PIANI ESISTENTI

### Piano 1 (P1): IMCP + CoVe + LLMLingua
**Approccio**: Protocollo custom IMCP (Inter-Model Communication Protocol) con 3 JSON schema (PLAN/RESULT/VERIFY), Chain-of-Verification cross-model, compressione LLMLingua, Semantic Cache con embeddings.
**Pro**: IMCP pragmatico, CoVe con trigger adattivo (500 token threshold), Adapter ABC pattern, Failed Approaches documentati.
**Contro**: Protocollo custom non-standard, LLMLingua = dipendenza pesante (torch ~2GB), 8 file nuovi, nessun bug reale affrontato, nessun anti-loop.

### Piano 2 (P2): 5 Bug Reali + HandoffPacket + ContextBudgeter
**Approccio**: Analisi forense codice con 5 bug + fix architetturali minimali. HandoffPacket strutturato (JSON con objective/plan/context_digest/ground_facts/boundaries), ContextBudgeter unificato, anti-loop guard (cap iterazioni + no-progress detection + budget token), error policy tabulare, trim state atomico.
**Pro**: 5 bug reali con file:linea, ricerca solida (14 GitHub + 8 arXiv), complessità netta negativa (dedup non nuovi file), trade-off quantificati (−25÷54% contesto), anti-loop completo.
**Contro**: Ambito solo mixed-mode, nessun protocollo standard, nessun HHEM, nessun caching avanzato, nessuna estensione ad altre modalità.

### Piano 3 (P3): MCP/A2A + HHEM + 10 Nuovi File
**Approccio**: Allineamento standard emergenti 2026. MCP server/client layer, A2A protocol, HHEM validator, Evidence-Gate, Schema Transformer, Prompt Caching, KV-Cache Compression, Unified Pipeline.
**Pro**: Standard emergenti MCP/A2A, HHEM zero-token, Schema Transformer type-safe, verification plan strutturato, 5 fasi con timeline 9-14gg.
**Contro**: 10 nuovi file (scope enormecreep), MCP non applicabile a routing interno, A2A overkill, KV-Cache server-side non fattibile, nessun bug reale, success metrics irrealistici.

### Audit Comparativo — Classifica
| Criterio | P1 | P2 | P3 |
|---|---|---|---|
| Bug reali | 1/10 | **10/10** | 0/10 |
| Ricerca solidità | 5/10 | **10/10** | 4/10 |
| Anti-allucinazione | 6/10 | 7/10 | **8/10** |
| Token optimization | 7/10 | **8/10** | 5/10 |
| Anti-loop/robustezza | 0/10 | **10/10** | 0/10 |
| Standard emergenti | 4/10 | 0/10 | **8/10** |
| Pragmatismo | 6/10 | **10/10** | 2/10 |
| **TOTALE** | 4.05 | **8.65** | 3.80 |

## IL TUO COMPITO

### FASE 1 — Ricerca Web Approfondita (OBBLIGATORIA)

Cerca su GitHub, HuggingFace, arXiv, e blog tecnici 2026 per:

1. **Protocolli comunicazione inter-modello 2026**: MCP, A2A, ACP, e qualsiasi nuovo standard emergente. Valuta se sono applicabili a un router proxy interno (NON agent orchestrazione esterna).

2. **Anti-allucinazione cross-model**: Framework, tecniche, paper 2026. Cerca specificamente: Chain-of-Verification (CoVe), self-consistency, HHEM, factual grounding per output che passano tra modelli diversi.

3. **Compressione token avanzata 2026**: LLMLingua-2, ACON, prompt caching, semantic caching, KV-cache, delta-token, contextual compression. Concentrati su tecniche applicabili a un proxy (lato client, non lato training/server).

4. **Anti-loop e robustezza multi-agent**: Pattern per bounded iteration, no-progress detection, graceful degradation. Cerca paper arXiv su "When Agents Do Not Stop", loop detection, termination guarantees.

5. **Architetture AI proxy esistenti 2026**: Cerca su GitHub progetti come LiteLLM, Plano, Proxima, router-proxy, multi-model gateway. Analizza come gestiscono comunicazione bilaterale, fallback, error recovery.

6. **Comunicazione bilaterale type-safe**: Schema validation, structured intermediate representation, plan-as-contract pattern. Cerca paper su "Plan-and-Act", "Chain of Agents", structured communication.

**Per ogni fonte trovata, cita: nome, URL, data, e perché è rilevante.**

### FASE 2 — Analisi Critica dei 3 Piani

Per ciascun piano (P1, P2, P3):
- Cosa fa bene
- Cosa manca o sbaglia
- Cosa è over-engineered
- Quali idee sono valide e dovrebbero essere nel piano finale

### FASE 3 — La Tua Soluzione

Proponi il TUO piano ottimale che combina il meglio dei 3 piani esistenti con le tue ricerche. Il piano deve:

1. **Fixare tutti e 5 i bug reali** (non opzionale — sono CRITICI)
2. **Implementare comunicazione bilaterale type-safe** (non test libero tra modelli)
3. **Anti-allucinazione cross-model** (almeno 2 tecniche diverse)
4. **Anti-loop con guard deterministici** (cap iterazioni + no-progress + budget)
5. **Compressione token avanzata** (almeno 2 tecniche nuove oltre l'esistente)
6. **Massimo pragmatismo** (minimi nuovi file, massima dedup)
7. **Zero over-engineering** (niente che non serve al routing interno)

### FASE 4 — Output Strutturato

Il tuo output DEVE avere questa struttura esatta:

```
# PIANO — [Nome Modello che ha prodotto questo piano]

## 1. Fonti della Ricerca
[Tabella con tutte le fonti trovate: nome, URL, data, rilevanza]

## 2. Analisi Critica Piani Esistenti
[P1 pro/contro, P2 pro/contro, P3 pro/contro]

## 3. Il Mio Piano
### 3.1 Architettura Proposta
[Diagramma ASCII o tabella del flusso proposto]

### 3.2 Comunicazione Bilaterale
[Protocollo/struttura per comunicazione THINK↔ACT↔VERIFY]

### 3.3 Anti-Allucinazione
[Tecniche proposte con threshold e trigger]

### 3.4 Anti-Loop
[Guard deterministici]

### 3.5 Compressione Token
[Tecniche nuove]

### 3.6 Fix Bug
[Come fixa ogni bug, con pseudocodice]

### 3.7 File da Creare/Modificare
[Tabella: file, azione, righe stimate, descrizione]

## 4. Trade-off & Rischi
[Tabella: decisione, pro, contro, rischio residuo]

## 5. Timeline
[Step implementativi con giorni stimati]

## 6. Cosa Scarto e Perché
[Lista di cose proposte nei piani 1-3 che NON adotti e perché]
```

## VINCOLI

- Il piano deve funzionare con il router proxy ESISTENTE (non riscrivere da zero)
- Massimo 6 nuovi file (sei un proxy, non un framework)
- Ogni nuovo file deve giustificarsi con un "senza questo non si può"
- Le dipendenze esterne devono essere minime (no torch/pytorch se possibile)
- Il piano deve coprire TUTTE le 7 modalità, non solo mixed-mode
- Non proporre MCP/A2A per routing interno (sono per orchestrazione esterna)
- Non proporre KV-Cache compression (è server-side, il proxy è client-side)

## MODELLO SPECIFICO: [Anthropic / MiniMax / GLM]

Se sei **Anthropic**: Pensa come architect primario. Focus su robustezza, safety, alignment. Considera che Anthropic è il THINKER e VERIFIER nella maggior parte delle modalità.

Se sei **MiniMax**: Pensa come executor pragmatico. Focus su performance, costo, compressione. MiniMax è l'ACT principale in mix-am e mix-gm. Considera limiti di context window (200K token).

Se sei **GLM**: Pensa come modello tiered alternativo. Focus su compatibilità, fallback, tiering (5.2→4.7→4-turbo). GLM è usato in modalità glm, mix-ag, mix-gm. Considera le differenze API con Anthropic-compatible endpoint z.ai.
```

---

## ISTRUZIONI PER L'USO

1. **Copia il blocco tra i backtick** (```) sopra
2. **Apri 3 chat separate**, una per ciascun provider:
   - **Chat Anthropic**: incolla il prompt così com'è, sostituendo `[Anthropic]` in fondo
   - **Chat MiniMax**: incolla il prompt, sostituendo `[MiniMax]` in fondo
   - **Chat GLM**: incolla il prompt, sostituendo `[GLM]` in fondo
3. **Ogni modello farà ricerche indipendenti** usando le proprie capacità web
4. **Raccogli i 3 output** e confrontali
5. **Redige il piano finale universale** prendendo il meglio da ciascuno

### Attese per Modello

| Modello | Attesa Focus | Punti di Forza Attesi |
|---|---|---|
| **Anthropic** | Robustezza, safety, alignment | Anti-allucinazione, schema design, architettura |
| **MiniMax** | Performance, costo, compressione | Token optimization, pragmatic code, benchmark |
| **GLM** | Compatibilità, fallback, tiering | Error recovery, API compatibility, graceful degradation |

---

**Creato**: 2026-07-19
**Scopo**: Ricerca universale 3-provider per piano globale comunicazione multi-modello
