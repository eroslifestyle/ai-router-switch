# PIANO FINALE — Sintesi Ricerca Universale 3-Modello 2026

**Data**: 2026-07-19
**Metodo**: Ricerca web indipendente (MiniMax web_search, 30+ fonti) + consolidamento di 4 output precedenti (P1/P2/P3/MiniMax) + evidence-gate Anthropic (Sonnet) su codice reale HEAD `b470dfc`
**Stato**: DEFINITIVO — sostituisce tutti i piani precedenti nella cartella

---

## 0. Executive Summary

Questo documento è la sintesi finale del prompt di ricerca `prompt-ricerca-universale-3-models.md`, eseguito su Anthropic (Sonnet, evidence-gated), MiniMax (ricerca web), e analizzato criticamente in questa sessione.

**Scoperta critica (Anthropic evidence-gate)**: dei 5 bug su cui si fonda la classifica dell'audit comparativo (P2 vince 8.65/10, trainata dal 30% del criterio "bug reali"), **solo 1 su 5 è ancora presente nel codice**. Gli altri 4 sono stati risolti da commit di redesign precedenti (2026-07-03 → 2026-07-13), e nessuno dei modelli se n'è accorto prima della verifica Anthropic.

**Conseguenza**: lo scope reale è molto più piccolo di quanto proposto da tutti i piani (1-10 file nuovi → 1 file modificato + 1 opzionale).

---

## 1. Fonti della Ricerca (consolidate da tutte le sessioni)

### 1.1 Protocolli Inter-Modello & Standard Emergenti

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 1 | Survey Agent Interoperability Protocols (MCP, ACP, A2A, UCP) | https://arxiv.org/html/2505.02279v1 | 2025-05 | Unica survey accademica che confronta 4 protocolli. ACP = "TCP/IP dell'agentic web" |
| 2 | ACP — Agent Communication Protocol (IBM) | https://agentcommunicationprotocol.dev | 2025-08 | REST-based, lightweight, open governance. Per agent↔agent esterni |
| 3 | MCP vs A2A: Complete Guide 2026 | https://dev.to/pockit_tools/mcp-vs-a2a-the-complete-guide-to-ai-agent-protocols-in-2026-30li | 2026-03 | MCP = agent→tool/dati. A2A = agent↔agent per task delegation esterna |
| 4 | AI Agent Protocol Ecosystem Map 2026 | https://www.digitalapplied.com/blog/ai-agent-protocol-ecosystem-map-2026-mcp-a2a-acp-ucp | 2026-03 | Mappa visuale: MCP+ACP+A2A coprono layer diversi, nessuno copre routing interno |
| 5 | Developer's Guide to AI Agent Protocols (Google) | https://developers.googleblog.com/developers-guide-to-ai-agent-protocols/ | 2026-03 | 6 protocolli confrontati. Conferma: MCP per tool, A2A per delegation |

**Verdetto unanime (tutti e 4 i documenti + questa sessione)**: MCP/A2A/ACP sono per orchestrazione esterna multi-agente. **Non applicabili al routing interno** tra fasi THINK/ACT/VERIFY di una singola richiesta in un proxy.

### 1.2 Anti-Allucinazione Cross-Model

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 6 | Chain-of-Verification (CoVe) — Meta | https://arxiv.org/abs/2309.11495 | 2023 | 1100 citazioni. 4-step: draft→verify→execute→revise. Gold standard anti-hallucination |
| 7 | HHEM 2.1 — Vectara | https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model | 2024-08 | Zero-token hallucination gate, outperforma GPT-4. Già in uso nel progetto (:4002) |
| 8 | HHEM 2.3 — Vectara | https://www.vectara.com/blog/hallucination-detection-commercial-vs-open-source-a-deep-dive | 2025-09 | HHEM-2.3 >> HHEM-2.1-Open. Score < 0.5 = probabile allucinazione |
| 9 | HHEM Leaderboard | https://github.com/vectara/hallucination-leaderboard | 2026 | Benchmark cross-modello, open source |

### 1.3 Compressione Token Avanzata

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 10 | ACON — Context Compression Optimization | https://arxiv.org/html/2510.00615v3 | 2026-06 | Compression guideline in natural language. −25% peak tokens, >95% accuracy |
| 11 | LLMLingua-2 — Microsoft | https://github.com/microsoft/LLMLingua | 2024-06 | BERT-level token classification, task-agnostic. **Richiede torch ~2GB** |
| 12 | LongLLMLingua — Microsoft | https://llmlingua.com/llmlingua2.html | 2024 | 4x compression, +17% performance. Training-time, non inference-time |
| 13 | Prompt Compression Caveats | https://wynandpieters.dev/posts/the-token-saving-cake-is-a-lie/ | 2026-04 | LLMLingua integrato in Claude Code: osservazioni pratiche, limiti reali |
| 14 | Top Prompt Compression 2026 | https://www.pointfive.co/guides/top-prompt-compression-solutions-2026 | 2026-06 | LLMLingua = baseline de facto, ma alternative più leggere emergono |
| 15 | Semantic Caching 2026 | https://llmtest.io/blog/llm-semantic-caching-approaches-2026 | 2026-05 | 3 approcci (embedding/hash/hybrid) con failure modes. −20-70% cost |
| 16 | Semantic Cache GPTCache | https://arxiv.org/pdf/2603.03301 | 2026 | Architettura LRU/LFU per cache embedding. Zero nuove dipendenze |

### 1.4 Anti-Loop & Robustezza Multi-Agent

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 17 | When Agents Do Not Stop | https://arxiv.org/pdf/2607.01641 | 2026-07 | 6549 repo analizzati, 74 con loop infiniti. 66% LangGraph/AutoGen |
| 18 | LoopTrap Attack | https://arxiv.org/abs/2605.05846 | 2026-05 | Loop poisoning: attacco che infetta termination guarantees |
| 19 | AgentTether Loop Detection | https://arxiv.org/pdf/2607.06273 | 2026-07 | Flag su tool+args identici. Pratico per bound di retry |
| 20 | Semantic Early-Stopping | https://arxiv.org/abs/2606.27009 | 2026-06 | Cosine-distance su embeddings per no-progress detection |

### 1.5 Architetture AI Proxy & Router

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 21 | LiteLLM Proxy | https://github.com/BerriAI/litellm | 2026 | 100+ provider, fallback chains, load balancing. OpenAI-compatible |
| 22 | LiteLLM Reliability & Fallbacks | https://docs.litellm.ai/docs/proxy/reliability | 2026 | Pattern: order-based fallback, cooldown su 429, typed error classes |
| 23 | Plano — AI-native proxy | https://github.com/katanemo/plano | 2026 | Proxy con orchestrazione, safety, routing built-in |
| 24 | kani — LLM smart router | https://github.com/tumf/kani | 2026 | Classifica prompt per complessità, auto-route al modello giusto |
| 25 | NVIDIA LLM Router Blueprint | https://github.com/NVIDIA-AI-Blueprints/llm-router | 2026 | Prompt analysis → optimal model selection |
| 26 | vLLM Semantic Router 2026-H2 | https://github.com/vllm-project/semantic-router/issues/2287 | 2026-06 | Production-grade middleware per model routing |
| 27 | Top 5 LiteLLM Alternatives 2026 | https://www.getmaxim.ai/articles/top-5-litellm-alternatives-in-2026/ | 2026-03 | Confronto: LiteLLM vs Portkey vs Helicone vs PromptLayer |
| 28 | Anthropic Multi-Agent Research System | https://www.anthropic.com/engineering/multi-agent-research-system | 2025 | Lead salva piano in memoria esterna, delega con 4 campi obbligatori |

### 1.6 Comunicazione Bilaterale Type-Safe

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 29 | Plan-and-Act (ICML 2025) | https://arxiv.org/abs/2503.09572 | 2025-04 | Planner→Executor con piano strutturato. 207 citazioni |
| 30 | Chain of Agents — Google (NeurIPS'24) | https://arxiv.org/abs/2406.02818 | 2024-06 | Communication Unit accumulata, +10% performance |
| 31 | Agents With Contracts (JSON Schema) | https://medium.com/@1nick1patel1/agents-with-contracts-json-schema-is-prompt-discipline-b6ebdec4a4d7 | 2026 | JSON Schema come disciplina per output agent |
| 32 | AI Agent Pipeline: 7 Architecture Patterns | https://ivern.ai/blog/ai-agent-pipeline-architecture-design-patterns | 2026-05 | 7 pattern produttivi con diagrammi e quando/non usarli |
| 33 | OpenAI Swarm (archiviato → Agents SDK) | https://github.com/openai/swarm | 2024 | Handoff esplicito con context_variables. Nessuno stato implicito |

### 1.7 Contesto & Compressione

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 34 | MemGPT (arXiv:2310.08560) | https://arxiv.org/abs/2310.08560 | 2023 | Summarization ricorsiva, memoria a due tier |
| 35 | Context Engineering Survey | https://arxiv.org/pdf/2507.13334 | 2025-07 | L'efficienza comunicazione inter-agente è il collo di bottiglia sistemico |
| 36 | Communication-Centric Survey LLM-MAS | https://arxiv.org/html/2502.14321v2 | 2025 | Survey comunicazione in multi-agent systems |
| 37 | Self-Governing Context for Long-Horizon Agents | https://arxiv.org/html/2607.00692v1 | 2026-07 | Compression-guideline optimization + Chroma Context-1 per agent long-running |
| 38 | Awesome LLM Token Optimization | https://github.com/pleasedodisturb/awesome-llm-token-optimization | 2026-07 | Curated list: caching, routing, compression per inference |

---

## 2. Analisi Critica dei 3 Piani Esistenti

### P1 — IMCP + CoVe + LLMLingua (275 righe)

| Pro | Contro |
|---|---|
| CoVe è il gold standard (1100 citazioni) | IMCP = protocollo inventato, zero adozione esterna |
| Trigger adattivo 500 token pragmatico | LLMLingua = torch ~2GB, training-time non inference-time |
| Adapter ABC pattern elegante | 8 file nuovi per un router da ~15 moduli |
| Semantic Cache concept valido | Semantic Cache richiede embedding API call → latenza |
| **Non affronta nessun bug reale** | CoVe 4-step = overhead 4x per ogni risposta |

**Verdetto**: le idee valide (CoVe, trigger adattivo) sopravvivono come opzioni future. Il resto è over-engineering.

### P2 — 5 Bug + HandoffPacket + ContextBudgeter (140 righe)

| Pro | Contro |
|---|---|
| **Unico piano basato su analisi codice reale** | 4/5 bug risultati non più presenti (evidence-gate) |
| Ricerca più solida (14 GitHub + 8 arXiv) | Scope limitato a mixed-mode |
| HandoffPacket è il pattern giusto (Plan-and-Act) | HandoffPacket non serve se il piano è già iniettato |
| Anti-loop completo (cap + no-progress + budget) | Anti-loop non serve se non ci sono loop (verificato) |
| Trade-off quantificati | Compressione ACON citata ma mai implementata |

**Verdetto**: il miglior punto di partenza metodologico, ma la motivazione primaria (5 bug) è crollata dopo evidence-gate.

### P3 — MCP/A2A + HHEM + 10 file (345 righe)

| Pro | Contro |
|---|---|
| HHEM zero-token tecnicamente eccellente | 10 nuovi file — scope creep puro |
| Standard emergenti reali citati | MCP/A2A non applicabili a routing interno |
| Schema Transformer idea pulita | KV-Cache = server-side, non fattibile |
| Evidence-Gate pattern interessante | **Zero bug affrontati** |

**Verdetto**: scartare quasi tutto. HHEM come gate è l'unica idea adottabile.

---

## 3. Evidence-Gate — Verifica dei 5 Bug su HEAD `b470dfc`

Questa è la sezione più critica del documento. L'Anthropic evidence-gate ha verificato ogni bug rivendicato contro il codice sorgente reale.

| # | Claim | Verifica | Verdetto |
|---|---|---|---|
| 1 | **CRITICO**: piano THINK non iniettato in ACT | `_build_act_body()` (riga 2102-2119) riceve `plan` e lo inserisce nel `system` come `"PIANO-GUIDA:\n{plan}"`. Commit `a2fcb228` del 2026-07-03. Chiamata da `_pipeline_think_act` riga 2772 con `plan` estratto riga 2743. | **FALSO — non presente** |
| 2 | Dual shrink inconsistente: path 400 salta shrink | Righe 2662-2664 (pre-check shrink) vs 2812-2828 (400 dentro loop ACT → rescue diretto). Ma è **dichiarato intenzionale** nel commento riga 2810-2811: "NON è bad-request: forza rescue verso Anthropic (context 1M)". | **PARZIALMENTE VERO, ma trade-off deliberato** |
| 3 | Race condition trim-state file | `_trim_context_after_response()` scrive con `Path.write_bytes()` — non atomico. `handle()` fa `exists()`→`read_bytes()`→`unlink()` senza lock. Due richieste concorrenti possono interlacciare. Zero `fcntl`/`tempfile` in tutto il file. | **VERO — unico bug ancora presente** |
| 4 | `mixed_fail_last_status` non impostato su eccezione | Righe 2779-2786: nel blocco `except`, `mixed_fail_last_status = None` viene impostato esplicitamente. Comportamento corretto: porta al ramo rescue generico. | **FALSO — già gestito** |
| 5 | Fast-path 400: shrink che torna 400 cade silenziosamente | Righe 4020-4030: su 400 verifica `is_ctx`, richiama `_shrink_and_retry_minimax`. Se falso, 400 rilanciato al client. Dentro shrink, se 400 di nuovo → `_mixed_haiku_rescue`. Non cade silenziosamente. | **FALSO — già gestito** |

**Impatto sulla classifica**: il criterio "bug reali" (peso 30%) va da 10/10 (P2) a 2/10 per tutti. La motivazione dei piani collassa.

---

## 4. Gap Non Anticipato (scoperto da evidence-gate)

### VERIFY non-enforcing in `mix-gm`

`_glm_minimax_think_act_verify` (righe 3438-3489): lo step VERIFY (GLM-5.2) logga il giudizio ma **non influenza la risposta** restituita al client — `act_raw` viene ritornato indipendentemente dall'esito. La "V" di THINK-ACT-VERIFY è **osservazionale, non enforcing**.

Questo è direttamente rilevante per l'obiettivo "zero allucinazioni cross-model" ed è un gap reale — nessun commit lo affronta.

### Anti-loop: non necessario (ma da confermare)

La pipeline `mix-am` non ha loop THINK↔ACT ripetuti. THINK gira una volta, ACT prova 2 executor fissi. L'escalation fast-path è a 2 round hardcoded (R1/R2 nel codice). Le fallback chain GLM→MiniMax→Anthropic (righe 3499-3572) sono lineari a 3 hop, mai ricorsive.

**Raccomandazione**: non creare `anti_loop_guard.py`. Serve solo un audit grep-first di `mix-ag`/`mix-gm`/`_glm_execute_with_chain` per confermare che ogni fallback chain termini in step finiti.

---

## 5. Il Piano Finale

### Principio Guida

**Non costruire infrastruttura per bug che non esistono.** Lo scope è dettato da: 1 bug reale + 1 gap scoperto + miglioramenti opzionali evidence-based.

### 5.1 Fix immediati (obbligatori)

#### A. Trim-state atomico (Bug 3 — l'unico bug reale)

**Problema**: `_trim_context_after_response()` scrive con `Path.write_bytes()` — non atomico. `handle()` legge/elimina senza lock. Concorrenza sullo stesso fingerprint = race.

**Fix** (~15 righe, solo `src/ai-router-proxy.py`):
```
1. In _trim_context_after_response():
   - Scrivi su tempfile.NamedTemporaryFile(dir=stessa_dir, delete=False, suffix='.tmp')
   - os.replace(tmp_path, target_path)  # atomico su POSIX

2. In handle() blocco read→unlink:
   - Dict globale: trim_locks = {}  # fingerprint → threading.Lock
   - Acquista lock prima di exists()/read_bytes()/unlink()
   - Usa contextmanager (with lock:)
```

Zero nuove dipendenze. Stdlib (`tempfile`, `os.replace`, `threading.Lock`).

#### B. VERIFY enforcing in `mix-gm` (gap scoperto)

**Problema**: VERIFY in `_glm_minimax_think_act_verify` è osservazionale. Se GLM segnala incongruenza, il client riceve comunque la risposta non verificata.

**Fix** (~30 righe, solo `src/ai-router-proxy.py`):
```
1. Nel prompt di VERIFY, chiedi marcatore testuale esplicito:
   "Se l'output ACT è incoerente, inizia con: INCOERENTE: [motivo]"

2. Dopo VERIFY:
   - Se verify_text.startswith("INCOERENTE"):
     - Retry ACT 1 volta con nota di correzione iniettata nel system
     - Se retry fallisce → ritorna act_raw con prefisso [VERIFY-WARNING]
   - Se verify_text non contiene incongruenza → ritorna act_raw (comportamento attuale)

3. Cap a 1 retry — coerente con il pattern R1/R2 già nel codice
```

### 5.2 Miglioramenti opzionali (evidence-based, non necessari per bug fix)

#### C. Marcatori testuali nel piano THINK (opzionale)

Il piano è testo libero iniettato in `system` — funziona, ma non dà all'executor modo strutturato per dichiarare step ineseguibili o boundaries.

**Fix**: aggiungere al prompt `_build_think_body` marcatori leggeri come testo:
```
OBIETTIVO: [cosa deve fare l'executor]
VINCOLI: [limiti contesto/timeout/modello]
NON FARE: [azioni proibite]
```

Zero rischio parse-fail. Coerente con la scelta "piano è testo libero" già nel codice (riga 2741).

**Quando implementare**: solo quando c'è un consumer reale (es. HHEM gate di 5.3) che beneficia di campi strutturati. Ora sarebbe over-engineering.

#### D. HHEM gate su ACT/VERIFY (opzionale)

Dopo VERIFY (`mix-gm`) o dopo ACT (`mix-am`, `mix-ag`) su risposte >300-500 caratteri:
```
1. Estrai frasi principali con split semplice
2. Chiama hhem-score locale (:4002) per ogni frase
3. Score < 0.5 → log + eventuale retry (cap 1, come 5.1.B)
```

**Prerequisito**: verificare che il servizio HHEM sia raggiungibile dal processo proxy in produzione.

### 5.3 Cosa NON fare (e perché)

| Azione | Motivo |
|---|---|
| Non creare `handoff_packet.py` con dataclass | Il bug che doveva risolvere (piano non iniettato) non esiste. Nessun consumer reale. |
| Non creare `anti_loop_guard.py` | Nessuna evidenza di loop non-bounded nel codice. Le chain sono lineari a 2-3 hop. |
| Non creare `semantic_cache.py` | Nessun dato di produzione che dimostri il bisogno. Da rivalutare solo dopo misurazione. |
| Non creare `context_budgeter.py` | Il dual shrink è un trade-off deliberato documentato nel codice, non un bug da unificare. |
| Non implementare LLMLingua | Richiede torch ~2GB. Training-time, non inference-time. |
| Non implementare MCP/A2A | Per orchestrazione esterna, non routing interno. |
| Non implementare KV-Cache | Server-side, il proxy è client-side. |
| Non implementare CoVe full (4-step) | Overhead 4x per ogni risposta. Trigger adattivo è meglio ma non serve ora. |

---

## 6. Architettura — Stato Attuale vs Proposto

### Stato attuale (verificato)

```
Client → [TokenCounter] → [ContextManager] → [Router]
       → [THINK: Anthropic/GLM] → piano testo libero
       → [ACT: MiniMax/GLM]    → riceve "PIANO-GUIDA:" in system ✅
       → [VERIFY: Anthropic/GLM] → logga, NON enforcing ❌ (solo mix-gm)
       → [StreamingRelay] → Client
       → [ContextShrink] → trim-state file NON atomico ❌
```

### Dopo fix (sezione 5.1)

```
Client → [TokenCounter] → [ContextManager] → [Router]
       → [THINK: Anthropic/GLM] → piano con marcatori (opzionale)
       → [ACT: MiniMax/GLM]    → riceve piano ✅
       → [VERIFY: Anthropic/GLM] → enforcing con retry 1x ✅ (mix-gm)
       → [HHEM gate] → opzionale, su risposte lunghe
       → [StreamingRelay] → Client
       → [ContextShrink] → trim-state atomico ✅
```

**Delta**: 2 fix reali nel main proxy + 2 opzioni future. Zero nuovi moduli.

---

## 7. File da Modificare

| File | Azione | Righe stima | Descrizione |
|---|---|---|---|
| `src/ai-router-proxy.py` | MODIFICA | +45 / −5 | Trim-state atomico (tempfile+os.replace+lock) + VERIFY enforcing mix-gm |
| `src/ai-router-proxy.py` | MODIFICA (opz.) | +10 | Marcatori testuali nel prompt THINK |
| `src/hhem_gate.py` | NUOVO (opz.) | ~40 | Wiring minimo HHEM su ACT/VERIFY |

**Totale obbligatorio: 1 file modificato, ~45 righe.**
**Totale con opzionali: 1 file modificato + 1 nuovo, ~95 righe.**

Confronto con piani precedenti:

| Piano | File nuovi | File modificati | Righe totali |
|---|---|---|---|
| P1 | 4 | 4 | ~600 |
| P2 | 0 | 4 | ~200 |
| P3 | 10 | 4 | ~1200 |
| MiniMax sintesi | 3 | 4 | ~365 |
| **Questo piano** | **0-1** | **1** | **~45-95** |

---

## 8. Trade-off & Rischi

| Decisione | Pro | Contro | Rischio residuo |
|---|---|---|---|
| Fix minimale, no infra | Zero scope creep | Se emergono nuovi bug, serve secondo giro | Basso — copre i 5 claim esistenti + 1 gap nuovo |
| tempfile+os.replace per trim | Standard POSIX, elimina race verificata | Lock in-process non copre multi-processo | Basso — proxy gira come singolo processo aiohttp |
| VERIFY enforcing via marcatore testuale | Coerente con "testo libero", zero parse-fail | Meno robusto di JSON schema | Medio-basso — mitigato da cap 1 retry |
| HHEM come opzionale | Non blocca il fix principale | Gap VERIFY parzialmente aperto senza | Accettabile — retry di 5.1.B mitiga |
| Nessun intervento compressione | Nessun rischio/dipendenza nuova | Non si sa se serve senza misura | Basso — la misura è il prossimo passo |

---

## 9. Timeline

| Fase | Giorni | Deliverable |
|---|---|---|
| Trim-state atomico (bug 3) | 0.5 | tempfile + os.replace + lock in `ai-router-proxy.py` |
| VERIFY enforcing mix-gm (gap) | 0.5 | 1 retry su incongruenza |
| Marcatori testuali THINK (opzionale) | 0.5 | Update prompt `_build_think_body` |
| HHEM gate wiring (opzionale) | 1 | `hhem_gate.py` + 2 call site |
| Audit fallback chain mix-ag/mix-gm (grep-first) | 0.5 | Conferma bound, o issue mirato |
| Test (concorrenza trim + retry VERIFY) | 1 | `test_trim_race.sh` + `test_mixgm_verify_retry.sh` |
| **Totale** | **2-4 giorni** | vs 7-17 giorni (tutti i piani precedenti) |

---

## 10. Cosa Scarto e Perché

| Da | Scarto | Perché |
|---|---|---|
| **Tutti e 4 i documenti** | I 5 bug come motivazione primaria | 4/5 non esistono più. Motivazione sostituita: 1 bug reale + 1 gap. |
| P1, P2, MiniMax | `HandoffPacket` / `IMCP` dataclass | Il bug che doveva risolvere (piano non iniettato) non esiste. |
| P1, P2, MiniMax | `anti_loop_guard.py` | Nessuna evidenza di loop non-bounded. Le chain sono già bounded. |
| P1 | IMCP, LLMLingua, Adapter ABC, Semantic Cache | Over-engineering. torch ~2GB per un proxy. Protocollo inventato. |
| P2 | `ContextBudgeter` | Il dual shrink è un trade-off deliberato, non un bug. |
| P3 | MCP/A2A, KV-Cache, 10 file, Unified Pipeline, Prompt Cache | MCP/A2A = esterno. KV-Cache = server-side. Scope creep puro. |
| MiniMax | Semantic Cache SQLite, Delta-Token, CoVe light generalizzato | Nessun dato produzione. Da rivalutare dopo misura. |
| Tutti | Classifica audit (8.65 vs 4.05 vs 3.80) | Il criterio "bug reali" (30%) è crollato. La classifica non è più valida. |

---

## 11. Lezione Metodologica

> **Prima di qualsiasi piano di fix, verificare il bug contro il codice reale.** Non fidarsi di documenti precedenti anche dettagliati con file:linea — il codice cambia. Qui, 5+ commit di redesign (2026-07-01 → 2026-07-13) hanno reso obsoleta l'analisi *prima ancora* che venisse riscritta indipendentemente da 3 modelli diversi.

> **Validità**: HEAD `b470dfc` (2026-07-19). Se sono passati giorni/commit, rileggere il codice prima di implementare qualsiasi punto della Sezione 5.

---

## 12. Metriche di Successo

| Metrica | Baseline | Target | Misura |
|---|---|---|---|
| Race condition trim-state | Concorrenza fallibile | 0 failures under load | 1000 req concorrenti, 0 errori |
| VERIFY enforcing mix-gm | Osservazionale solo | Incongruenza → retry 1x | Log `VERIFY_RETRY` + output test |
| Righe nuove | N/A | < 100 (obbligatorio) | `git diff --stat` |
| File nuovi | N/A | 0 (obbligatorio), max 1 (opzionale) | `git status` |
| Regressione | N/A | 0 su tutte le 7 modalità | `test_glm_modes.sh` + test mixed |

---

**Creato**: 2026-07-19
**Sessione**: Sonnet (ricerca web MiniMax + evidence-gate Anthropic + sintesi)
**File di riferimento**: `prompt-ricerca-universale-3-models.md` (template), `audit-comparativo-piani.md` (classifica originale), `comunicazione-multi-modello-2026.md` (P1), `mixed-mode-bilateral-redesign-2026-07-19.md` (P2), `piano-comunicazione-bilaterale-2026.md` (P3), `Minimax_piano-universale-3-models-2026.md` (esecuzione MiniMax), `Anthropic_PIANO-FINALE-verificato-2026-07-19.md` (evidence-gate Anthropic)
