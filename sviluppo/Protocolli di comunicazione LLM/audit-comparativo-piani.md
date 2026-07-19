# Audit Comparativo — 3 Piani Comunicazione Multi-Modello 2026

**Data**: 2026-07-19 · **Cartella**: `sviluppo/Protocolli di comunicazione LLM/`

---

## Panoramica Rapida

| Piano | Nome | Righe | Autore | Focus Principale |
|---|---|---|---|---|
| **P1** | `comunicazione-multi-modello-2026.md` | 275 | MiniMax session | IMCP protocollo custom + CoVe + LLMLingua + Semantic Cache |
| **P2** | `mixed-mode-bilateral-redesign-2026-07-19.md` | 140 | MiniMax session | 5 bug reali + HandoffPacket + ContextBudgeter + anti-loop |
| **P3** | `piano-comunicazione-bilaterale-2026.md` | 345 | Sonnet session | MCP/A2A standard + HHEM + Evidence-Gate + 10 nuovi file |

---

## Analisi Dettagliata per Piano

### P1 — IMCP + CoVe + LLMLingua

**Approccio**: Protocollo custom IMCP (Inter-Model Communication Protocol) con schema JSON strutturato per comunicazione bilaterale, verifica CoVe (Chain-of-Verification), compressione LLMLingua e Semantic Cache.

| Pro | Contro |
|---|---|
| IMCP schema semplice e pragmatico (3 JSON schema: PLAN/RESULT/VERIFY) | Protocollo custom — non segue standard emergenti (MCP/A2A) |
| CoVe con trigger policy adattivo (500 token → bilaterale, <500 → self-consistency) | LLMLingua = dipendenza pesante (torch + transformers, ~2GB) |
| Semantic Cache con embeddings MiniMax per deduplicazione 100% | Semantic Cache richiede embedding API call per ogni lookup → latenza + costo |
| Adapter pattern (ABC) per generalizzare provider conversion | Non affronta bug reali del codice esistente |
| Failed Approaches / Do NOT ben documentati | Nessuna analisi del codice esistente (linee, funzioni reali) |
| Dipendenze chiare (`llmlingua`, `sentence-transformers`) | 8 file nuovi/modify — ambito non banale |
| Target risparmio token realistico (30-50%) | Nessuna strategia anti-loop (iterazioni non bounded) |

**Ricerca citata**: MCP, A2A, ACP, RouteLLM, Redis Blog 2026, LLMLingua (Microsoft)

---

### P2 — 5 Bug Reali + HandoffPacket + ContextBudgeter

**Approccio**: Analisi forense del codice esistente (`src/ai-router-proxy.py`), identificazione 5 bug reali con severità/linea, fix architetturali minimali.

| Pro | Contro |
|---|---|
| **5 bug reali diagnosticati** con file:linea precisa | Ambito ristretto: solo mixed-mode (mix-am), non copre le altre 6 modalità |
| Ricerca fonti **molto solida** (14 fonti GitHub + 8 arXiv verificate) | Nessun protocollo standard (MCP/A2A) menzionato |
| HandoffPacket risolve il **bug critico #1** (piano non iniettato da THINK→ACT) | Manca HHEM o altro layer anti-allucinazione |
| ContextBudgeter unifica shrink dual-path (fix bug #2 e #5) | Compressione solo summarization + LLMLingua opzionale |
| Anti-loop con cap iterazioni + no-progress detection + budget token | Nessuna strategia caching avanzata (prompt cache, semantic cache) |
| Error recovery coerente: tabella error→policy unica | Nessun compatibility layer type-safe |
| Trim state atomico (fix bug #3: race condition) | Non estende ad altre modalità (mix-ag, mix-gm, glm) |
| `mixed_fail_last_status` fix in finally/except (fix bug #4) | Solo 6 prossimi passi, nessuna timeline dettagliata |
| **Trade-off quantificati** (token +200-600/req ma −25÷50% contesto) | Nessun test plan strutturato |
| Anchored recursive summarization (MemGPT-style) | Nessun success metrics |
| **Complessità codice netto negativa** (dedup, non nuovi file) | |

**Ricerca citata**: OpenAI Swarm, LangGraph, AutoGen, crewAI, LiteLLM, Anthropic multi-agent, Plan-and-Act, Chain of Agents, MemGPT, ACON, When Agents Do Not Stop, Semantic Early-Stopping, CoVe, Context Engineering Survey

---

### P3 — MCP/A2A + HHEM + Schema Transformer

**Approccio**: Allineamento completo agli standard emergenti 2026 (MCP, A2A), con 5 fasi implementative e 10 nuovi file.

| Pro | Contro |
|---|---|
| **Standard emergenti** (MCP + A2A) — allineato con l'ecosistema 2026 | **10 nuovi file** — ambito enorme, rischio scope creep |
| HHEM (zero-token hallucination detection) già disponibile a :4002 | KV-Cache Compression non fattibile per proxy (è server-side, non client-side) |
| Schema Transformer generalizza `remap_body_for_minimax()` a tutti i modelli | MCP è per agent→tool, NON per routing interno — P1 ha ragione ("non applicabile") |
| Prompt Caching con Anthropic API `cache_control` headers | A2A è per orchestrazione esterna agent↔agent — overkill per proxy interno |
| Architettura attuale analizzata a fondo (10+ layer documentati) | **Nessun bug reale identificato** — puro design from-scratch |
| 5 fasi implementative con timeline (9-14 giorni) | Nessuna intersezione con bug reali di P2 |
| Verification plan strutturato (unit, integration, performance, regression) | Success metrics irrealistici ("zero allucinazioni 95%+" con HHEM score) |
| Success metrics quantificati | Nessuna fonte arXiv/paper — solo web search generiche |
| Rischi & mitigazioni documentati | Prompt Cache Layer ridondante: Anthropic API lo fa già nativamente |
| Delta-Token Correction per accuratezza rate limiting | Nessun meccanismo anti-loop |
| Evidence-Gate Pattern per sub-model output | evidence_gate.py = file intermedio, HHEM già copre la validazione |

**Ricerca citata**: MCP Anthropic, A2A HuggingFace, Plano, Proxima, C3PO, HHEM, Stanford AI Index 2026, Redis, Obvious Works

---

## Confronto Diretto per Dimensione

### Copertura Bug Reali

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Bug codice identificati | 0 | **5 (con file:linea)** | 0 |
| Bug critici | 0 | **1 (THINK→ACT handoff)** | 0 |
| Fix proposti | 0 | **5 (tutti quantificati)** | 0 |

**Verdetto**: P2 vince nettamente — è l'unico basato su analisi del codice reale.

### Protocolli & Standard

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Standard esterni (MCP/A2A) | Cita ma scarta A2A | 0 | **MCP + A2A implementati** |
| Protocollo custom | **IMCP (3 schema)** | **HandoffPacket** | **A2A protocol** |
| Type-safe validation | JSON Schema | Implicita | **SchemaTransformer** |
| Compatibility layer | **Adapter ABC** | HandoffPacket fields | **SchemaTransformer** |

**Verdetto**: P3 più ambizioso sugli standard, ma MCP/A2A non sono applicabili al routing interno. P1 IMCP pragmatico. P2 HandoffPacket più mirato.

### Anti-Allucinazione

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Validation layer | **CoVe (cross-model verify)** | **CoVe (verify dal piano)** | **HHEM + Evidence-Gate** |
| Zero-token detection | 0 | 0 | **HHEM (:4002)** |
| Self-consistency | **< 500 token** | 0 | **Self-consistency sampling** |
| Trigger policy | **Adattivo (threshold 500 tok)** | **Verify indipendente dal draft** | **Post-ACT/VERIFY pipeline** |

**Verdetto**: P3 più completo (HHEM + Evidence-Gate), ma P2 CoVe è più integrato col design. P1 ha trigger policy adattivo pratico.

### Token Optimization

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Compressione LLM | **LLMLingua (2-5x)** | **Summarization ricorsiva + LLMLingua opzionale** | **Summarization (esistente)** |
| Semantic cache | **Embedding-based (0.95 threshold)** | 0 | 0 |
| Prompt caching | **Anthropic cache_control** | 0 | **PromptCache layer** |
| KV-Cache | 0 | 0 | **KV-Cache compression** |
| Delta-token correction | 0 | 0 | **Token correction** |
| Unified component | 0 | **ContextBudgeter (single point)** | 0 |
| Risparmio stimato | **30-50%** | **−26÷54% (ACON range)** | **60-80%** |

**Verdetto**: P2 ContextBudgeter (single compression point) è il design migliore architetturalmente. P1 più vario. P3 cita 60-80% ma irrealistico per un proxy.

### Anti-Loop & Robustezza

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Cap iterazioni | 0 | **MAX_MIXED_ITERATIONS + budget token** | 0 |
| No-progress detection | 0 | **Hash/similarità output** | 0 |
| Guard su rescue path | 0 | **Bound copre tutti feedback path** | 0 |
| Race condition fix | 0 | **Atomic write + versioning** | 0 |
| Error policy tabulare | 0 | **error_class → policy unica** | 0 |

**Verdetto**: P2 è l'unico che affronta il problema loop/rescue. P1 e P3 non lo menzionano.

### Ricerca & Fonti

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Fonti GitHub | 3 (MCP, LLMLingua, Redis) | **7 (Swarm, LangGraph, AutoGen, crewAI, LiteLLM, Anthropic, AG2)** | 4 (MCP, Plano, Proxima, llm-router-proxy) |
| Fonti arXiv/Paper | 0 | **8 (Plan-and-Act, Chain of Agents, MemGPT, ACON, Early-Stopping, CoVe, Context Eng Survey, When Agents Not Stop)** | 0 |
| Fonti web generiche | 4 (Redis Blog, RouteLLM, A2A, ACP) | 0 | 7 (MCP blog, HHEM, Stanford, Anthropic, ObviousWorks, AWS, Edgee) |
| **Totale fonti** | **7** | **15** | **11** |
| Qualità fonti | Media | **Alta (arXiv peer-reviewed)** | Media-Bassa |

**Verdetto**: P2 ha la ricerca più solida e accademica. P3 ha buone fonti web ma nessun paper.

### Complessità & Scope

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Nuovi file | 4 | **0 (solo fix moduli esistenti)** | **10** |
| File modificati | 4 | **4** | 4 |
| Timeline | Non specificata | **6 step** | **9-14 giorni (5 fasi)** |
| Complessità codice | Media (new files) | **Netto negativa (dedup)** | Alta (10 new files) |
| Risk di scope creep | Media | **Basso** | **Alto** |

**Verdetto**: P2 è il più pragmatico (0 nuovi file, dedup). P3 è il più ambizioso (e rischioso).

### Testing & Verification

| Dimensione | P1 | P2 | P3 |
|---|---|---|---|
| Unit test | pytest compression | **Test isolato (ref: test_glm_modes.sh)** | **Unit + Integration + Performance + Regression** |
| E2E test | ai-mode mix-am | **test_mixed_mode.sh (dedicato)** | Client → MCP → A2A → Model → Validation |
| Benchmark | Token savings 10 richieste | Non specificato | **Latency + token + HHEM throughput** |
| Regression | Non specificato | Non specificato | **Legacy mode compatibility** |

**Verdetto**: P3 ha il verification plan più strutturato. P1 pragmatico. P2 minimale.

---

## Classifica Finale

| Criterio (peso) | P1 | P2 | P3 | Vincitore |
|---|---|---|---|---|
| **Bug reali** (30%) | 1/10 | **10/10** | 0/10 | **P2** |
| **Ricerca solidità** (15%) | 5/10 | **10/10** | 4/10 | **P2** |
| **Anti-allucinazione** (15%) | 6/10 | 7/10 | **8/10** | **P3** |
| **Token optimization** (15%) | 7/10 | **8/10** | 5/10 | **P2** |
| **Anti-loop/robustezza** (10%) | 0/10 | **10/10** | 0/10 | **P2** |
| **Standard emergenti** (5%) | 4/10 | 0/10 | **8/10** | **P3** |
| **Pragmatismo/scope** (5%) | 6/10 | **10/10** | 2/10 | **P2** |
| **Testing** (5%) | 5/10 | 4/10 | **8/10** | **P3** |
| **TOTALE** | **4.05** | **8.65** | **3.80** | **P2** |

---

## Raccomandazione

**P2 è il piano vincitore** per una ragione fondamentale: è l'unico basato su **analisi forense del codice reale** con 5 bug diagnosticati e fonti arXiv verificate. Il suo approccio "fix prima, architettura dopo" produce complessità netta negativa (dedup, non nuovi file).

### Piano Ibrido Consigliato

Prendi il **meglio di ciascun piano**:

| Da | Prendi | Perché |
|---|---|---|
| **P2** (75%) | HandoffPacket, ContextBudgeter, anti-loop, error policy, trim atomico, 5 bug fix | Fix reali, fondazione solida, complessità negativa |
| **P1** (15%) | IMCP schema + CoVe trigger policy adattivo + Adapter pattern | Complementa P2 con protocollo strutturato e trigger policy |
| **P3** (10%) | HHEM validator + Evidence-Gate + Schema Transformer | Anti-allucinazione zero-token + type-safe compatibility |

**Scarta da P3**: MCP/A2A (non applicabili a routing interno), KV-Cache Compression (server-side), Prompt Cache Layer (Anthropic API nativa), Unified Pipeline (over-engineering), 6 dei 10 nuovi file.

### Priorità Implementativa Ibrida

1. **Fix bug #1** (HandoffPacket THINK→ACT) — P2
2. **Unificare shrink** (ContextBudgeter) — P2
3. **Trim state atomico** — P2
4. **mixed_fail_last_status fix** — P2
5. **Anti-loop guard** (cap + no-progress) — P2
6. **HHEM validator** — P3
7. **CoVe trigger policy** — P1
8. **Schema Transformer** (generalizza adapter) — P3

---

**Audit creato**: 2026-07-19 · Sonnet session
