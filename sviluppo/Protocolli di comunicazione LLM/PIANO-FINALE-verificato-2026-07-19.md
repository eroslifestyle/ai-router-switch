# PIANO FINALE — Comunicazione Multi-Modello AI Router Proxy (verificato contro il codice reale)

**Creato**: 2026-07-19 · **Eseguito da**: Claude (Sonnet), ruolo THINKER/VERIFIER come da `prompt-ricerca-universale-3-models.md`
**HEAD verificato**: `b470dfc` · **Metodo**: evidence-gate (R2 anti-allucinazione) su tutti i claim dei 3 piani + della sintesi MiniMax, non solo nuova ricerca

---

## 0. Executive Summary — la scoperta critica

Invece di ripetere da zero la ricerca web (già fatta 4 volte, ~30+ fonti raccolte), ho applicato la regola evidence-gate: **ho letto il codice sorgente reale riga per riga** per ogni bug rivendicato dai documenti esistenti (P1, P2, P3, e `Minimax_piano-universale-3-models-2026.md` — quest'ultimo è l'esecuzione MiniMax di questo stesso prompt).

**Risultato**: dei "5 bug reali" su cui si fonda la vittoria di P2 nell'audit comparativo (bug reali 10/10, criterio con peso 30%), **solo 1 su 5 è ancora presente nel codice attuale**. Gli altri 4 sono stati risolti da commit di redesign precedenti alla stesura dei documenti stessi (tutti datati 2026-07-19), e nessuno dei 3 modelli — incluso quello che ha eseguito letteralmente questo prompt — se n'è accorto.

Questo cambia lo scope corretto dell'intero progetto: non serve l'infrastruttura pesante (HandoffPacket dataclass, anti-loop guard, semantic cache, HHEM validator come nuovo layer) proposta da tutti e 4 i documenti per "fixare i bug" — perché 4 di quei bug non esistono. Il piano finale (Sezione 7) è quindi molto più piccolo, e le sezioni di ricerca (3-4) restano come base di conoscenza consolidata per decisioni future, anche se lo scope implementativo immediato non le richiede tutte.

---

## 1. Verifica dei 5 "bug reali" (evidence-gate, file:linea su HEAD `b470dfc`)

| # | Claim originale (P1/P2/P3/MiniMax) | Verifica sul codice reale | Verdetto |
|---|---|---|---|
| 1 | **CRITICO**: il piano THINK non viene iniettato nei messaggi ACT — MiniMax opera senza contesto | `_build_act_body()` ([ai-router-proxy.py:2102-2119](src/ai-router-proxy.py#L2102-L2119)) riceve `plan` come parametro e lo inserisce nel `system` come `"PIANO-GUIDA:\n{plan}"` (riga 2108-2113). Chiamata da `_pipeline_think_act` alla riga 2772 con `plan` estratto dal THINK alla riga 2743. L'iniezione (`PIANO-GUIDA`) è stata introdotta nel commit `a2fcb228` del **2026-07-03**, 16 giorni prima della stesura dei 4 documenti che rivendicano il bug. | **FALSO** — non presente. Contraddice tutti e 4 i documenti. |
| 2 | Dual shrink inconsistente: il pre-check chiama shrink, ma il path 400 nel loop ACT lo salta e va diretto a rescue | Confermato in parte: righe 2662-2664/2766-2768 (`_is_context_too_large_for_minimax` → shrink proattivo) vs riga 2812-2828 (400 context-exceed dentro il loop ACT → NON richiama shrink, passa all'executor successivo e poi a `_mixed_haiku_rescue`). Ma è **dichiarato intenzionale** nel commento inline riga 2810-2811: "NON è bad-request: forza rescue verso il modello utente (context 1M)" — il rescue Anthropic gestisce nativamente contesti più larghi di un altro giro di shrink su MiniMax. | **PARZIALMENTE VERO, ma trade-off deliberato** documentato nel codice, non una svista. Impatto: escalation più aggressiva del necessario in alcuni casi limite, nessuna perdita dati/errore. |
| 3 | Race condition sul trim-state file: richieste concorrenti con stesso fingerprint leggono stato stale | `_trim_context_after_response()` ([riga 2252-2279](src/ai-router-proxy.py#L2252-L2279)) scrive con `Path.write_bytes()` (riga 2276) — non atomico, nessun lock. `handle()` ([riga 3632-3648](src/ai-router-proxy.py#L3632-L3648)) fa `exists()` → `read_bytes()` → `unlink()` senza lock: due richieste concorrenti sullo stesso fingerprint possono interlacciare read/write/unlink. Nessun `fcntl`, nessun `tempfile`+`os.replace()` in tutto il file (verificato via grep, zero occorrenze). | **VERO** — unico bug dei 5 ancora realmente presente. |
| 4 | `mixed_fail_last_status` non impostato quando `forward_minimax` lancia eccezione → check 429 successivo sempre `False` | Riga 2779-2786: nel blocco `except`, `mixed_fail_last_status = None` viene impostato **esplicitamente** (non lasciato stale dal ciclo precedente). Il check successivo (riga 2841) `if mixed_fail_last_status == 429` risulta correttamente `False` per un'eccezione — comportamento corretto, porta al ramo rescue generico (riga 2850). | **FALSO** — già gestito (in modo diverso da quanto proposto: reset esplicito a `None` invece di un valore reale, ma funzionalmente corretto). |
| 5 | Fast-path 400: se lo shrink ritorna 400 di nuovo, cade silenziosamente senza handling | Riga 4020-4030 (FAST-PATH mix-am): su 400 verifica `is_ctx` (riga 4024), se vero richiama `_shrink_and_retry_minimax` (riga 4027); se falso, il 400 viene comunque rilanciato al client via `relay(up)`, non perso. Dentro `_shrink_and_retry_minimax`, se anche il body compresso torna 400 context-exceed, va a `_mixed_haiku_rescue` (riga 2375-2377) — non cade silenziosamente. | **FALSO** — già gestito correttamente in entrambi i path. |

**Conseguenza diretta**: la classifica dell'audit comparativo (P2 vince 8.65 vs 4.05/3.80, trainata per il 30% dal criterio "bug reali") va riletta sapendo che quel criterio, oggi, vale 2/10 non 10/10 per nessuno dei tre piani — perché il problema che dovevano risolvere in gran parte non c'è più.

---

## 2. Scoperta non anticipata da nessuno dei 3 piani

Verificando il codice ho trovato un gap reale, mai menzionato nei 4 documenti precedenti:

**`_glm_minimax_think_act_verify` (modalità `mix-gm`, [riga 3438-3489](src/ai-router-proxy.py#L3438-L3489))**: lo step VERIFY (GLM-5.2) logga il proprio giudizio (`verify_text[:100]`, riga 3484) ma **non influenza in alcun modo** la risposta restituita al client — `act_raw` viene ritornato alla riga 3489 indipendentemente dall'esito della verifica. La "V" di THINK-ACT-VERIFY in `mix-gm` è **osservazionale, non enforcing**: se GLM segnala un'incongruenza o un'allucinazione, il client riceve comunque la risposta non corretta.

Questo è direttamente rilevante per l'obiettivo "zero allucinazioni cross-model" del prompt originale, ed è un gap concreto — a differenza dei bug 1/4/5 sopra, non ho trovato commit che lo affrontino.

**Nota anti-loop**: nella pipeline `mix-am` non esiste un ciclo THINK↔ACT ripetuto — THINK gira una sola volta, ACT prova una lista fissa di 2 executor senza tornare al THINK. L'"escalation" in fast-path (righe 3944-3991) è esplicitamente a 2 round (commentati `R1`/`R2` nel codice), quindi già bounded. Le proposte "anti-loop guard con cap iterazioni + no-progress detection" di **tutti e 4** i documenti risolvono un problema che l'evidenza nel codice attuale non mostra essere presente in `mix-am`. Non ho tracciato ogni ramo di `mix-ag`/`mix-gm`/`_glm_execute_with_chain` in dettaglio, ma le fallback chain lette (GLM→MiniMax→Anthropic, righe 3499-3572) sono lineari a 3 hop, mai ricorsive.

**Nota naming (minore)**: il codice usa internamente `mode == "mix-am"` (es. righe 4011, 663), mentre il `CLAUDE.md` di progetto elenca le modalità core come `anthropic, minimax, mixed, inverse`. Drift documentale, non bloccante, da allineare separatamente.

---

## 3. Fonti della Ricerca (tabella completa, consolidata dai 4 documenti + verifiche personali)

### 3.1 Note di verifica personale (questa sessione, via `m3-web`)

Non ho ripetuto l'intera ricerca (sarebbe stato spreco: 4 documenti l'hanno già fatta con buone fonti). Ho invece verificato miratamente le due claim più critiche per decidere lo scope del piano:

- **arXiv:2607.01641** "When Agents Do Not Stop: Uncovering Infinite Agentic Loops in LLM Agents" — **confermato esistente** (trovato anche citato da Reddit, LinkedIn e paper correlati sullo stesso tema, luglio 2026). Rilevante in generale per agenti iterativi, ma nella pipeline `mix-am` attuale non ho trovato il pattern di loop che descrive (Sezione 2).
- **Ecosistema MCP/A2A 2026** — confermato via ricerca indipendente, inclusa **arXiv:2505.02279** "A Survey of Agent Interoperability Protocols" (MCP/ACP/A2A/ANP): MCP standardizza agent→tool/dati, A2A standardizza agent↔agent per delega di task tra agenti *esterni* indipendenti. Conferma indipendente della conclusione già raggiunta da tutti e 3 i piani + dall'audit: **MCP/A2A non applicabili** all'instradamento interno tra fasi (THINK/ACT/VERIFY) della stessa richiesta in un singolo proxy.

Le fonti restanti in tabella (3.2) **non sono state ri-verificate singolarmente in questa sessione** — sono riportate come trovate nei documenti precedenti, con indicazione di quale documento le cita. Molte (LLMLingua-2/Microsoft, HHEM/Vectara, MemGPT, Chain-of-Verification/Meta, Plan-and-Act, Chain of Agents/Google, LiteLLM) sono paper e repository reali e noti anche indipendentemente da questa sessione, quindi trattate come attendibili; per le fonti 2026 più recenti e meno note (LoopTrap, AgentTether, Semantic Caching blog) resta valida solo l'attendibilità del documento che le cita, non una verifica diretta mia.

### 3.2 Tabella fonti (deduplicata)

| # | Fonte | URL | Data | Trovata da | Rilevanza | Verificata da me |
|---|---|---|---|---|---|---|
| 1 | When Agents Do Not Stop | https://arxiv.org/abs/2607.01641 | 2026-07 | P2, MiniMax | Loop infiniti in agenti LLM: 47 progetti, 66% su LangGraph/AutoGen | **Sì (m3-web)** |
| 2 | A Survey of Agent Interoperability Protocols | https://arxiv.org/html/2505.02279v1 | 2025 | (trovata da me, non nei 4 doc) | MCP/ACP/A2A/ANP: conferma MCP=tool, A2A=agent↔agent, nessuno per routing interno | **Sì (m3-web)** |
| 3 | Plan-and-Act | https://arxiv.org/abs/2503.09572 | 2025 | P2, MiniMax | Planner→Executor, piano in forma strutturata (207 citazioni riportate da MiniMax, non verificato da me) | No |
| 4 | Architecting Resilient LLM Agents: Plan-then-Execute | https://arxiv.org/abs/2509.08646 | 2025 | P2 | Control-flow integrity: l'executor non ridefinisce il piano | No |
| 5 | Chain of Agents (Google, NeurIPS'24) | https://arxiv.org/abs/2406.02818 | 2025-01 | P2, MiniMax | Communication Unit accumulata invece del testo integrale tra worker | No |
| 6 | MemGPT | https://arxiv.org/abs/2310.08560 | 2023 | P2 | Summarization ricorsiva, memoria a due tier | No |
| 7 | ACON | https://arxiv.org/abs/2510.00615 | 2026 | P2 | Compressione unificata: −26÷54% token di picco, >95% accuracy | No |
| 8 | Semantic Early-Stopping for Iterative LLM Agent Loops | https://arxiv.org/html/2606.27009v1 | 2026-06 | P2, MiniMax | Cap iterazioni + no-progress detection + budget, terminazione non delegabile al modello | No |
| 9 | Chain-of-Verification (CoVe) | https://arxiv.org/abs/2309.11495 | 2023 | P1, P2, MiniMax | 4-step draft→plan→verify→synthesis, tecnica anti-allucinazione più citata tra le fonti | No |
| 10 | Survey of Context Engineering | https://arxiv.org/pdf/2507.13334 | 2025 | P2 | Efficienza comunicazione inter-agente = collo di bottiglia sistemico | No |
| 11 | Communication-Centric Survey LLM-MAS | https://arxiv.org/html/2502.14321v2 | 2025 | P2 | Stesso tema, survey complementare | No |
| 12 | LoopTrap Attack | https://arxiv.org/abs/2605.05846 | 2026 | MiniMax | Loop poisoning: attacco che infetta le termination guarantees | No |
| 13 | AgentTether Loop Detection | https://arxiv.org/pdf/2607.06273 | 2026 | MiniMax | Flag ripetizioni su tool+args identici | No |
| 14 | GPTCache / architettura semantic cache | https://arxiv.org/pdf/2603.03301 | 2026 | MiniMax | LRU/LFU/FIFO/Random per cache embedding | No |
| 15 | LLMLingua-2 (ACL Findings 2024) | https://aclanthology.org/2024.findings-acl.57/ | 2024 | MiniMax | Data distillation GPT-4, BERT-encoder, task-agnostic | No |
| 16 | microsoft/LLMLingua (repo) | https://github.com/microsoft/LLMLingua | 2026 | P1, P2, MiniMax | Compressione prompt fino a 20x; LLMLingua-2 comprime solo il "context" | No |
| 17 | openai/swarm (archiviato) | https://github.com/openai/swarm | — | P2 | Nessuno stato nascosto tra chiamate: ogni handoff passa esplicitamente `context_variables` | No |
| 18 | langchain-ai/langgraph-swarm-py | https://github.com/langchain-ai/langgraph-swarm-py | — | P2 | Task description esplicito nel nodo successivo; senza checkpointer lo swarm "dimentica" | No |
| 19 | AG2 Context Variables | https://docs.ag2.ai/latest/docs/user-guide/advanced-concepts/orchestration/group-chat/context-variables/ | — | P2 | Context-variables fuori dalla history → token-efficient | No |
| 20 | AutoGen conversation patterns + discussion #7144 | https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns/ | — | P2 | Carryover; stato spalmato nei messaggi = comportamento imprevedibile | No |
| 21 | crewAI issues #724 / #928 | https://github.com/crewAIInc/crewAI/issues/724 | — | P2 | Caso reale: contesto perso quando il passaggio è implicito | No |
| 22 | LiteLLM Reliability / Routing | https://docs.litellm.ai/docs/proxy/reliability | 2026 | P2, MiniMax | Fallback tipizzati per classe di errore, cooldown per deployment su 429 | No |
| 23 | Anthropic — multi-agent research system | https://www.anthropic.com/engineering/multi-agent-research-system | — | P2 | Il lead salva il piano in memoria esterna; delega richiede 4 elementi (obiettivo, formato output, tool, confini) | No |
| 24 | HHEM 2.1 (Vectara) | https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model | 2024 | MiniMax | Zero-token hallucination gate, outperforma GPT-4 su benchmark citato | No |
| 25 | HHEM 2.3 (Vectara) | https://www.vectara.com/blog/hallucination-detection-commercial-vs-open-source-a-deep-dive | 2025 | MiniMax | HHEM-2.3 > HHEM-2.1-Open | No |
| 26 | HHEM Leaderboard (GitHub) | https://github.com/vectara/hallucination-leaderboard | 2026 | MiniMax | Valutazione cross-modello; già in uso nel setup del progetto (`~/.claude/services/hhem/`) | No (esistenza servizio locale non riverificata in questa sessione, vedi 7.4) |
| 27 | Semantic Caching 2026 | https://llmtest.io/blog/llm-semantic-caching-approaches-2026 | 2026-05 | MiniMax | −20/70% costo, 3 approcci con relativi failure mode | No |
| 28 | Awesome LLM Token Optimization | https://github.com/pleasedodisturb/awesome-llm-token-optimization | 2026-07 | MiniMax | Lista curata: caching, routing, compression | No |
| 29 | Prompt Compression Caveats | https://wynandpieters.dev/posts/the-token-saving-cake-is-a-lie/ | 2026-04 | MiniMax | Osservazioni pratiche su LLMLingua-2 integrato in Claude Code | No |
| 30 | Token Cost 2026 (Obvious Works) | https://www.obviousworks.ch/en/token-optimization-saves-up-to-80-percent-llm-costs/ | 2026-02 | P3, MiniMax | Prompt caching + model routing + context engineering | No |
| 31 | AI Gateway 2026 (Zuplo) | https://zuplo.com/learning-center/best-api-gateways-ai-llm-workloads-2026 | 2026-05 | MiniMax | Confronto 10 gateway, token rate limiting + MCP | No |
| 32 | Envoy AI Gateway MCP | https://aigateway.envoyproxy.io/blog/mcp-implementation/ | 2025-10 | MiniMax | Routing MCP enterprise con observability | No |
| 33 | MCP Gateway Architecture | https://api7.ai/learning-center/api-gateway-guide/what-is-mcp-gateway | 2026-04 | MiniMax | Proxy routing MCP per tool integration | No |
| 34 | A2A Protocol Google (overview) | https://www.ruh.ai/blogs/ai-agent-protocols-2026-complete-guide | 2025-11 | MiniMax | Protocollo bilaterale tra agent, handshake + task state | No |
| 35 | A2A Protocol v1 spec | https://pub.towardsai.net/a2a-protocol-v1-2026-how-ai-agents-actually-talk-to-each-other-c500079bca73 | 2026-04 | MiniMax | Agent Card + Task artifact pattern | No |
| 36 | LiteLLM proxy (repo) | https://github.com/BerriAI/liteLLM-proxy | 2026 | MiniMax | Router multi-provider OpenAI-compatible, 50+ modelli | No |

**Fonti citate solo da P3, senza URL nel documento originale** (non fabbrico link — regola R3 anti-allucinazione: mai inventare un URL non verificato): Plano (katanemo, AI-native proxy con orchestrazione), Proxima (Zen4-bit, multi-AI MCP server routing), C3PO (framework Chain-of-Thought + verification multimodale), Stanford AI Index 2026 (hallucination rate benchmark 4-34%), llm-router-proxy (b24039971, multi-agent gateway). Se servono per decisioni future, vanno prima ritrovate con URL verificabile.

---

## 4. Analisi Critica Dettagliata dei 3 Piani Esistenti

Riletta alla luce della Sezione 1 (verifica bug), non solo come confronto architetturale astratto.

### 4.1 Piano 1 — IMCP + CoVe + LLMLingua (`comunicazione-multi-modello-2026.md`)

| Aspetto | Valutazione |
|---|---|
| **Pro** | CoVe con trigger adattivo (soglia 500 token) è pragmatico; distinzione MCP (tool-uso interno, già presente) vs A2A (esterno, non applicabile) è corretta e confermata dalla mia verifica (Sezione 3.1); Adapter ABC pattern è un'idea pulita se un giorno servissero >2 provider realmente eterogenei. |
| **Contro** | Nessuna lettura del codice reale — zero bug citati con file:linea, quindi non falsificabile con evidence-gate come gli altri; LLMLingua = dipendenza torch/transformers (~2GB) per un proxy che oggi non ne ha nessuna pesante; Semantic Cache con embedding richiede una chiamata API aggiuntiva per ogni lookup (latenza + costo, contro l'obiettivo "massimo risparmio token"). |
| **Over-engineering** | IMCP è un protocollo custom inventato quando il problema (bug 1, "piano non passa") non esisteva nemmeno al momento della stesura — la soluzione risolveva un sintomo mai osservato nel codice. 8 file nuovi per un router che ne conta circa 15 in totale. |
| **Idee da adottare** | Trigger adattivo per verifica (soglia lunghezza/token) — riuso in Sezione 7.4 per il gate HHEM opzionale. Adapter pattern: non prioritario ora, da tenere presente se emerge un quarto provider. |
| **Verdetto rivisto** | Il punteggio basso dell'audit (4.05) resta giustificato, ma non per "0 bug reali affrontati" (quel criterio oggi vale meno) — piuttosto per l'assenza totale di verifica contro il codice e per le dipendenze pesanti non giustificate. |

### 4.2 Piano 2 — 5 Bug Reali + HandoffPacket + ContextBudgeter (`mixed-mode-bilateral-redesign-2026-07-19.md`)

| Aspetto | Valutazione |
|---|---|
| **Pro** | Ricerca la più solida in assoluto (23 fonti GitHub+arXiv uniche, la maggioranza della tabella in Sezione 3.2 viene da qui); pattern architetturali citati (Explicit Handoff Payload, Structured Intermediate Representation, Bounded Loop with External Guards) sono validi e generalizzabili, indipendentemente dai bug specifici; unico piano che prova a leggere il codice con precisione (file:linea). |
| **Contro** | **La lettura del codice era sbagliata o obsoleta su 4 bug su 5** (Sezione 1) — l'unico vero merito differenziale del piano (bug reali con evidenza) si è rivelato in gran parte non verificato contro lo stato attuale; ambito dichiarato solo mixed-mode, non tocca `mix-ag`/`mix-gm`/`glm`. |
| **Over-engineering** | Nessuno strutturale (è il piano più snello), ma l'intero impianto (`HandoffPacket`, `ContextBudgeter`, `anti_loop_guard.py`) è dimensionato per risolvere problemi che, verificati, in gran parte non ci sono più — quindi diventa over-engineering *per conseguenza della scoperta*, non per scelta di design. |
| **Idee da adottare** | I pattern generali (Sezione 3 di P2: "Bounded Loop with External Guards", "Typed Error Fallbacks", "Independent Verification") restano utili come linee guida, non come nuovi file: applicati al gap reale trovato in Sezione 2 (VERIFY non-enforcing in `mix-gm`), non ai bug inesistenti. |
| **Verdetto rivisto** | Il punteggio più alto dell'audit (8.65, vincitore) era corretto **sulla qualità della ricerca e del metodo**, non sulla sua applicabilità pratica oggi: il metodo (leggere il codice, citare file:linea) è quello giusto — è quello che ho applicato in questo documento, arrivando a conclusioni diverse perché il codice nel frattempo è cambiato. |

### 4.3 Piano 3 — MCP/A2A + HHEM + 10 Nuovi File (`piano-comunicazione-bilaterale-2026.md`)

| Aspetto | Valutazione |
|---|---|
| **Pro** | HHEM come gate zero-token è l'idea con il miglior rapporto beneficio/costo di tutto il corpus di documenti — riutilizzata in Sezione 7.4; buona descrizione dell'architettura esistente (10+ layer di ottimizzazione token già documentati correttamente). |
| **Contro** | Nessun bug reale citato (0/5), confermato anche dalla mia verifica — semplicemente il piano non ha provato a cercarli; MCP/A2A proposti come layer nuovo (`mcp_layer.py`, `a2a_protocol.py`) nonostante MCP sia già usato internamente per i tool esistenti — non serve un secondo layer MCP per il routing tra fasi interne (confermato in Sezione 3.1); KV-Cache compression è esplicitamente server-side, non implementabile lato proxy client. |
| **Over-engineering** | Il più marcato dei tre: 10 file nuovi, timeline 9-14 giorni, success metrics dichiarati ("zero allucinazioni 95%+", "60-80% risparmio token") senza baseline misurata sul progetto reale. |
| **Idee da adottare** | HHEM validator (Sezione 7.4) e il concetto di Schema Transformer *come principio* (non come file dedicato: se un giorno servirà normalizzare `remap_body_for_minimax()` per un 4° provider, farlo lì, non prima). |
| **Verdetto rivisto** | Il punteggio basso (3.80) resta il più giustificato dei tre, e la mia verifica non lo corregge in meglio (a differenza di P2): P3 non aveva comunque ancorato le sue proposte al codice reale, quindi la scoperta della Sezione 1 non lo salva né lo affossa ulteriormente. |

---

## 5. Il Piano (rivisto alla luce della verifica)

Principio guida, diretta conseguenza della Sezione 1: **non costruire infrastruttura per bug che non esistono**. Lo scope realistico è molto più piccolo di quanto proposto da P1 (8 file), P2 (0 file nuovi ma refactor ampio su tutte le 7 modalità), P3 (10 file) e dalla sintesi MiniMax (3 file nuovi).

### 5.1 Architettura Proposta

```
Client
  │
  ▼
handle()  ── CTX pre-check (osserva soltanto) ── TRIM INTERCEPT
  │                                                  └─ [FIX 5.2] write atomico (tempfile+os.replace)
  │                                                     + lock in-process per-fingerprint
  ▼
mode dispatch: anthropic | minimax | mix-am | mix-ag | mix-gm | glm | inverse
  │
  ├─ mix-am → _pipeline_think_act()
  │      THINK (Anthropic, testo libero                     ACT (MiniMax executor chain:
  │      + marcatori opz. OBIETTIVO/VINCOLI/NON-FARE)  ──▶  MIXED_EXECUTOR_MODEL → MINIMAX_MODEL)
  │      "PIANO-GUIDA" già iniettato in system                    │
  │      (verificato presente, Sezione 1 #1)                      │
  │                                                    [opzionale] HHEM-gate (Sezione 5.4)
  │                                                    score<0.5 → 1 retry, altrimenti passa
  │                                                                 │
  │                                    429 → relay diretto al client │ altro fail → _mixed_haiku_rescue
  │
  ├─ mix-gm → _glm_minimax_think_act_verify()
  │      THINK (GLM-5.2) ──piano testo──▶ ACT (MiniMax) ──raw──▶ VERIFY (GLM-5.2)
  │                                                                    │
  │                                        oggi: solo log (Sezione 2) │
  │                                        [FIX 5.2] marcatore incongruenza
  │                                        rilevato → retry ACT ×1, altrimenti procedi
  │
  └─ mix-ag / glm / inverse / anthropic / minimax
         invariati — nessun bug verificato in questa sessione (non tutti i rami tracciati, Sezione 2)
```

### 5.2 Fix immediati (unico bug reale + gap trovato)

- **Bug 3 — trim-state race**: scrittura atomica in `_trim_context_after_response` via `tempfile` nella stessa directory + `os.replace()`; lock in-process per-fingerprint (`threading.Lock` in un dict keyed by `fp`) attorno al blocco read→unlink in `handle()`. ~15 righe, stesso file, zero nuove dipendenze (stdlib).
- **Gap VERIFY non-enforcing in `mix-gm`**: se il testo di VERIFY contiene un marcatore di incongruenza (pattern testuale semplice tipo "INCOERENTE"/"NO"/"ERRORE" da chiedere esplicitamente nel prompt di verify — non serve JSON schema rigido, coerente con la scelta "piano è testo libero" già presa nel codice, riga 2741), fare **un** retry di ACT con la nota di correzione iniettata nel `system`, altrimenti procedere. Cap a 1 retry, coerente con il pattern "R1/R2" già presente altrove nel codice. ~30 righe in `_glm_minimax_think_act_verify`.

### 5.3 Miglioramento opzionale (non bug fix): arricchire il piano THINK→ACT

Il piano oggi è testo libero iniettato in `system` — funziona (il bug 1 non esiste), ma non dà all'executor un modo strutturato per dichiarare "step ineseguibile", né campi tipo `ground_facts`/`boundaries` utili a un futuro gate HHEM.

- Se si vuole investire qui: aggiungere al prompt di `_build_think_body` marcatori leggeri (`OBIETTIVO:` / `VINCOLI:` / `NON FARE:`) come testo, non JSON — zero rischio di parse-fail, coerente con la "Version C" già scelta dal team.
- **Non creare `handoff_packet.py`** con dataclass strutturate finché non c'è un consumer reale (es. il gate HHEM di 5.4) che ne tragga beneficio. Farlo ora sarebbe over-engineering per campi che oggi nessun codice legge.

### 5.4 HHEM come gate opzionale (zero-token, già disponibile nel progetto)

Wiring minimo: dopo VERIFY (`mix-gm`) o dopo ACT (`mix-am`, `mix-ag`) su risposte sopra una soglia pratica (es. >300-500 caratteri), estrarre le frasi principali con uno split semplice e chiamare `hhem-score` locale (`:4002`, `~/.claude/services/hhem/`). Score < 0.5 → log + eventuale singolo retry (stesso bound di 5.2). **Da verificare prima di implementare**: che il servizio HHEM sia raggiungibile in rete dal processo del proxy (non assunto qui, solo verificato che il servizio esiste nel resto del setup utente — vedi fonte #26 in Sezione 3.2, non riverificata come endpoint attivo).

### 5.5 Compressione token — non toccare

Il sistema attuale (Token Counter, Context Rewrite, Context Shrink HHEM-adaptive, Summarizer, Model Context Map — 10+ layer già documentati) è più sofisticato di quanto LLMLingua/semantic-cache aggiungerebbero, al costo di una dipendenza pesante (torch ~2GB) o di una nuova cache SQLite da mantenere. Nessuno dei 4 documenti ha dimostrato con dati reali del progetto (solo stime percentuali) che serva altro. **Raccomandazione: non implementare nulla qui** finché non c'è un problema misurato nei log di produzione.

### 5.6 Anti-loop — solo audit mirato, non nuovo modulo

Non costruire `anti_loop_guard.py`: nessuna evidenza nel codice attuale di loop non-bounded in `mix-am` (verificato riga per riga). Resta da fare un audit grep-first (non una riscrittura) di `mix-ag`/`mix-gm`/`_glm_execute_with_chain` per confermare che ogni fallback chain termini in un numero finito di step — dalla lettura fatta (Sezione 2) sembra di sì (3 hop lineari, mai ricorsivi), ma non è stato tracciato ogni ramo.

### 5.7 File da creare/modificare

| File | Azione | Righe stima | Perché |
|---|---|---|---|
| `src/ai-router-proxy.py` | MODIFICA | +45/−5 | Trim-state atomico (5.2), VERIFY-enforcing in `mix-gm` (5.2), marcatori testuali nel piano THINK (5.3, opzionale) |
| `src/hhem_gate.py` | NUOVO (opzionale) | ~40 | Wiring minimo HHEM su ACT/VERIFY — solo se si decide di investire in 5.4 |

**Totale: 1 file modificato obbligatorio, 1 file nuovo opzionale.** Ben sotto il limite di 6 file richiesto dal prompt originale, e sotto quanto proposto da tutti e 4 i documenti precedenti (3-10 file nuovi).

---

## 6. Trade-off & Rischi

| Decisione | Pro | Contro | Rischio residuo |
|---|---|---|---|
| Fix minimale (no HandoffPacket/anti-loop-guard) | Zero scope creep, coerente con evidenza reale | Se emergono NUOVI bug non coperti da questa verifica, serve un secondo giro | Basso — la Sezione 1 copre i 5 claim esistenti punto per punto |
| Trim-state con `tempfile`+`os.replace()`+lock in-process | Standard POSIX, elimina la race verificata | Lock in-process non copre eventuali processi multipli (solo thread/task nello stesso processo asyncio) | Basso — il proxy gira come singolo processo aiohttp |
| VERIFY-enforcing via marcatore testuale (non JSON) | Coerente con la scelta già fatta nel codice, zero parse-fail nuovi | Meno robusto di uno schema tipizzato | Medio-basso — mitigato dal cap a 1 retry, mai un loop |
| HHEM gate come opzionale, non obbligatorio | Non blocca la release del fix principale | Se non implementato, resta il gap "VERIFY osservazionale" parzialmente aperto (il retry di 5.2 lo mitiga già in parte) | Accettabile |
| Nessun intervento su compressione token | Nessun nuovo rischio/dipendenza | Non si sa se serve finché non si misura | Basso — la misura è il prossimo passo naturale, non un'implementazione al buio |

---

## 7. Timeline

| Fase | Giorni | Deliverable |
|---|---|---|
| Trim-state atomico (bug 3) | 0.5 | `tempfile`+`os.replace()`+lock in `ai-router-proxy.py` |
| VERIFY enforcing `mix-gm` (gap Sezione 2) | 0.5 | 1 retry su incongruenza rilevata |
| Marcatori testuali nel piano THINK (opzionale) | 0.5 | Update prompt `_build_think_body` |
| HHEM gate wiring (opzionale) | 1 | `hhem_gate.py` + 2 call site |
| Audit fallback chain `mix-ag`/`mix-gm` (grep-first) | 0.5 | Conferma bound, o issue mirato se trovato un ramo ricorsivo |
| Test (concorrenza trim-state + retry VERIFY) | 1 | `sviluppo/tests/test_trim_race.sh` + `test_mixgm_verify_retry.sh` |
| **Totale** | **2-4 giorni** (vs 7-17 giorni stimati da tutti i piani precedenti) | |

---

## 8. Cosa scarto e perché (aggiornato dopo verifica)

| Da | Scarto | Perché (motivo aggiornato) |
|---|---|---|
| P1, P2, P3, MiniMax | `HandoffPacket`/`IMCP` come dataclass/protocollo strutturato dedicato | Il bug che doveva risolvere (piano non iniettato) **non esiste** — il piano è già iniettato come testo in `system`. Un arricchimento testuale (5.3) basta finché non c'è un consumer che ha bisogno di campi tipizzati. |
| P1, P2, P3, MiniMax | `anti_loop_guard.py` con cap iterazioni + no-progress detection | Nessuna evidenza di loop non-bounded in `mix-am` (verificato); l'escalation esistente è già a 2 round hardcoded. Serve solo un audit, non un nuovo modulo. |
| P1 | IMCP protocollo custom, LLMLingua, Adapter ABC, Semantic Cache | Over-engineering indipendentemente dalla verifica bug: dipendenza torch ~2GB, protocollo inventato senza adozione esterna, pattern prematuro per 2-3 provider reali. |
| P2 | `ContextBudgeter` come componente unificato dedicato | Il sistema di shrink esistente (Sezione 1, bug 2) è già un trade-off deliberato, non un bug da unificare — un refactor qui sposterebbe complessità senza un problema misurato a giustificarlo. |
| P3 | MCP/A2A, KV-Cache compression, 10 file nuovi, Unified Pipeline, Prompt Cache Layer | Confermato dalla verifica web (Sezione 3.1): MCP/A2A sono per orchestrazione *esterna* multi-agente, non routing interno. KV-Cache è server-side. Il resto è scope creep per un proxy che deve restare un proxy. |
| MiniMax (sintesi) | Semantic Cache SQLite, Delta-Token, CoVe light generalizzato | Stesso principio di 5.5: nessun dato reale di produzione che dimostri il bisogno; da rivalutare solo dopo aver misurato. |
| Tutti e 4 | I 5 bug come motivazione primaria del piano | 4 su 5 non esistono più (Sezione 1) — la motivazione va sostituita con: 1 bug reale + 1 gap trovato in questa sessione (Sezione 2). |

---

## 9. Nota metodologica per il prossimo ciclo (anti-allucinazione)

- **Prima di qualsiasi piano di fix su questo codice, verificare il bug contro `git log -p` + lettura diretta del file** — non fidarsi di un documento precedente anche se dettagliato con file:linea, perché il codice cambia: qui, 5+ commit di redesign (`2026-07-01` → `2026-07-13`) hanno reso obsoleta l'analisi *prima ancora* che venisse riscritta indipendentemente da 3 modelli diversi, incluso uno che ha eseguito letteralmente questo stesso prompt di ricerca.
- Questo file ha una data di validità: HEAD `b470dfc` (2026-07-19). Se sono passati giorni/commit, rileggere il codice prima di implementare qualsiasi punto della Sezione 5.
- Le fonti in Sezione 3.2 non marcate "verificata da me" restano affidabili solo quanto il documento che le ha trovate — se una decisione futura dipende in modo critico da una di esse, ri-verificarla con una query mirata prima di agire.

---

**File di riferimento nella stessa cartella**: `PIANO-UNIVERSALE-multi-modello.md` (template originale, ora superato da questo documento), `audit-comparativo-piani.md`, `comunicazione-multi-modello-2026.md` (P1), `mixed-mode-bilateral-redesign-2026-07-19.md` (P2), `piano-comunicazione-bilaterale-2026.md` (P3), `Minimax_piano-universale-3-models-2026.md` (esecuzione MiniMax di questo stesso prompt).
