# PIANO — Ricerca Universale 3-Modelli 2026

**Creato**: 2026-07-19
**Metodo**: Ricerca web approfondita su GitHub, arXiv, HuggingFace + analisi forense codice

---

## 1. Fonti della Ricerca

| # | Fonte | URL | Data | Rilevanza |
|---|---|---|---|---|
| 1 | MCP Gateway Architecture | https://api7.ai/learning-center/api-gateway-guide/what-is-mcp-gateway | 2026-04 | Proxy routing MCP per tool integration |
| 2 | A2A Protocol Google | https://www.ruh.ai/blogs/ai-agent-protocols-2026-complete-guide | 2025-11 | Protocollo bilaterale tra agent per handshake + task state |
| 3 | A2A Protocol v1 Spec | https://pub.towardsai.net/a2a-protocol-v1-2026-how-ai-agents-actually-talk-to-each-other-c500079bca73 | 2026-04 | Agent Card + Task artifact pattern |
| 4 | Plan-and-Act (arXiv) | https://arxiv.org/abs/2503.09572 | 2025 | HandoffPlanner → Actor pattern, citato 207 volte |
| 5 | Chain-of-Agents Google | https://research.google/blog/chain-of-agents-large-language-models-collaborating-on-long-context-tasks/ | 2025-01 | Comunicazione strutturata tra agent senza training |
| 6 | Chain-of-Verification | https://arxiv.org/abs/2309.11495 | 2023 | CoVe 4-step: draft→plan→verify→synthesis, citato 1100 |
| 7 | Semantic Early-Stopping arXiv | https://arxiv.org/abs/2606.27009 | 2026-06 | Cosine-distance su embeddings per loop detection |
| 8 | Semantic Caching 2026 | https://llmtest.io/blog/llm-semantic-caching-approaches-2026 | 2026-05 | −20-70% cost, 3 approcci con failure modes |
| 9 | GPTCache Semantic | https://arxiv.org/pdf/2603.03301 | 2026 | Architettura LRU/LFU/FIFO/Random per cache embedding |
| 10 | LiteLLM Proxy | https://github.com/BerriAI/liteLLM-proxy | 2026 | Router multi-provider, OpenAI-compatible, 50+ modelli |
| 11 | HHEM 2.1 Vectara | https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model | 2024 | Zero-token hallucination gate, outperforma GPT-4 |
| 12 | HHEM 2.3 Vectara | https://www.vectara.com/blog/hallucination-detection-commercial-vs-open-source-a-deep-dive | 2025 | HHEM-2.3 > HHEM-2.1-Open su benchmark |
| 13 | HHEM GitHub Leaderboard | https://github.com/vectara/hallucination-leaderboard | 2026 | Valutazione cross-modello, già in uso nel progetto |
| 14 | LLMLingua-2 Microsoft | https://aclanthology.org/2024.findings-acl.57/ | 2024 | Data distillation GPT-4, BERT-encoder, task-agnostic |
| 15 | LLMLingua-2 GitHub | https://github.com/microsoft/LLMLingua | 2026 | 20x compression ratio, LongLLMLingua +4% perf @4x |
| 16 | Token Optimization 2026 | https://github.com/pleasedodisturb/awesome-llm-token-optimization | 2026-07 | Curated list 2026: caching, routing, compression |
| 17 | LoopTrap Attack arXiv | https://arxiv.org/abs/2605.05846 | 2026 | Loop poisoning: attacco che infetta termination guarantees |
| 18 | AgentTether Loop Detection | https://arxiv.org/pdf/2607.06273 | 2026 | Flag ripetizioni su tool+args identici |
| 19 | Prompt Compression Caveats | https://wynandpieters.dev/posts/the-token-saving-cake-is-a-lie/ | 2026-04 | Integrato LLMLingua-2 in Claude Code, osservazioni pratiche |
| 20 | Token Cost 2026 | https://www.obviousworks.ch/en/token-optimization-saves-up-to-80-percent-llm-costs/ | 2026-02 | Prompt caching + model routing + context engineering |
| 21 | AI Gateway 2026 | https://zuplo.com/learning-center/best-api-gateways-ai-llm-workloads-2026 | 2026-05 | 10 gateway confronto, token rate limiting + MCP |
| 22 | Envoy AI Gateway MCP | https://aigateway.envoyproxy.io/blog/mcp-implementation/ | 2025-10 | Enterprise routing MCP con observability |

---

## 2. Analisi Critica Piani Esistenti

### Piano 1 (IMCP + CoVe + LLMLingua)

**Pro**:
- CoVe è la tecnica anti-allucinazione più validata (1100 citazioni)
- Trigger adattivo 500 token pragmatico
- Adapter ABC pattern elegante

**Contro**:
- IMCP = protocollo inventato da zero, nessun standard
- LLMLingua torch-dipendente (~2GB) — overkill per un proxy
- 8 file nuovi per un router che ne ha ~15 totali
- I 5 bug REALI non sono nemmeno menzionati
- CoVe richiede 4 step per OGNI risposta — overhead ingestibile su high-volume

**Verdict**: Le buone idee (CoVe, trigger adattivo) sopravvivono. Il resto è over-engineering.

### Piano 2 (5 Bug + HandoffPacket + ContextBudgeter)

**Pro**:
- Unico piano che ANALIZZA I BUG REALI con file:linea
- HandoffPacket strutturato è il pattern giusto (Plan-and-Act in forma lightweight)
- Anti-loop completo con cap + no-progress + budget
- Trade-off quantificati (−25÷54%)
- Ricerca GitHub+arXiv seria

**Contro**:
- Scope limitato a mixed-mode — le altre 6 modalità ignorate
- HHEM già presente nel progetto ma non usato come gate pre-VERIFY
- Semantic cache citata ma mai implementata come layer separato
- Il trim-state race condition manca soluzione atomica concreta

**Verdict**: Il miglior punto di partenza. Corregge i difetti listed e allarga a tutte le 7 modalità.

### Piano 3 (MCP/A2A + HHEM + 10 file)

**Pro**:
- MCP/A2A sono standard emergenti reali (Google + Anthropic + 50+ provider)
- HHEM zero-token è tecnicamente eccellente
- Schema Transformer è idea pulita

**Contro**:
- 10 nuovi file per un proxy — scope creep puro
- MCP è per agent→tool, NON per routing interno tra modelli (Piano 3 ignora questo)
- A2A richiede agent registration, skill discovery, task state machine — overkill per 2-step pipelines
- KV-Cache compression è server-side, non applicabile
- Nessuno dei 5 bug affrontato
- Nessun fallback concreto per MCP failures

**Verdict**: Scartare quasi tutto. HHEM come gate è l'unica idea da tenere.

---

## 3. Il Mio Piano

### 3.1 Architettura Proposta

```
Client → [TokenCounter] → [SemanticCache] → [ContextRewrite]
       → [Router] ────────────────────────────────→ [LLM1 (THINK)]
       → [HandoffPacket] ─────────────────────────→ [LLM2 (ACT)]
       → [HHEM-Gate] ────────────────────────────→ [LLM3 (VERIFY/ACT)]
       → [StreamingRelay] → Client
```

**Pipeline per modalità**:
| Modalità | THINK | ACT | VERIFY |
|---|---|---|---|
| `anthropic` | Claude | — | — |
| `minimax` | M3 | — | — |
| `mixed` | Claude | MiniMax | (opz. Haiku rescue) |
| `inverse` | M3 | Claude | — |
| `glm` | GLM tier | — | — |
| `glm-minimax` | GLM | MiniMax | — |
| `anthropic-glm` | Claude | GLM | Claude VERIFY |

### 3.2 Comunicazione Bilaterale

**Pattern: HandoffPacket v2 (Plan-and-Act lightweight)**

```json
{
  "handoff_id": "uuid",
  "source_model": "claude-sonnet-4-5",
  "target_model": "minimax-m3",
  "objective": "Il piano che Anthropic ha generato",
  "plan_steps": ["step1", "step2"],
  "context_digest": "SHA256(summary)",
  "ground_facts": ["fatto1", "fatto2"],
  "boundaries": {"max_tokens": 200000, "deadline_ms": 30000},
  "anti_loop_token": "unique_per_request"
}
```

**Iniezione in ACT**:
- I piani THINK vengono INIETTATI nei messaggi ACT come `user` priming
- Il target ACT vede il piano come contesto, non come messaggio autore
- VERIFY riceve il piano originale come reference per cross-check

### 3.3 Anti-Allucinazione

**Layer 1 — HHEM-Gate (zero-token)**
- Trigger: output > 100 token E passaggio tra modelli diversi
- Threshold: score < 0.5 → fallback chain
- Già implementato nel progetto (`:4002`), serve solo wiring

**Layer 2 — Chain-of-Verification light (trigger: fact-dense output)**
- Trigger: se il contenuto supera 300 token E contiene fatti specifici (nomi, date, numeri)
- Pattern: ASK question → GENERATE answer → VERIFY facts con stesso modello (1 round)
- Non CoVe completo (4 step) — overhead eccessivo
- Implementazione: un secondo call al modello ACT con prompt di verifica

**Layer 3 — Evidence-Gate (già nel progetto)**
- Ogni claim fattuale nel piano deve avere un `ground_facts` array
- VERIFY controlla: ogni claim citato in `ground_facts`?

### 3.4 Anti-Loop

**Cap iterazioni**: max 3 cicli THINK→ACT→VERIFY per sessione

**No-progress detection** (arXiv 2026-06, Semantic Early-Stopping):
- Serializza ultimi 3 output → embedding cosine-distance
- Se distanza < 0.05 per 2 round consecutivi → STOP
- Fallback: ritorna ultimo output, marca con `degraded: true`

**Token budget cap**:
- Budget = `model_context_limit × 0.8`
- Ogni iterazione consuma dal budget
- Budget esaurito → FINALIZE con quello che c'è

**Loop poisoning defense** (LoopTrap arXiv 2026):
- Il `handoff_id` è unico per request
- Il `anti_loop_token` cambia ogni ciclo
- Se un attacco cerca di fissare il loop, i token non matchano → exception

### 3.5 Compressione Token

**Layer 1 — Semantic Cache** (nuovo, 0 nuove dipendenze)
- Hash dei messaggi → embedding cosine-similarity
- Cache hit → ritorna cached response
- Cache miss → proceeds normal
- Storage: SQLite locale con TTL 1h per request, 24h per summary
- Threshold: similarity > 0.92 → hit (tuned per request, > 0.95 per code)
- Fonte: GPTCache + arXiv 2603.03301, implementato senza dipendenze esterne

**Layer 2 — Prompt Hash Deduplication** (nuovo, 10 righe)
- Hash SHA256 dei messaggi flat
- Se hash già processato in questa finestra 5s → return cached
- Complemento a token counter cache (30s), non sostituto

**Layer 3 — Head+Tail Expansion** (già in context_rewrite.py)
- Aumentare da 6+6 a 8+8 messaggi quando budget > 60%
- Ratio adattivo basato su `context_manager.get_fill_ratio()`

**NOTA**: LLMLingua-2 scartato — richiede torch/BERT-encoder, overhead > beneficio per un proxy. LLMlingua-2 è per **training-time compression**, non per inference-time. Il proxy è lato client inference.

### 3.6 Fix Bug

**Bug 1 — CRITICA: Piano THINK non iniettato in ACT**

```
File: ai-router-proxy.py ~3600-3800 (_mixed_haiku_rescue + executor loop)
Fix:
  1. THINK output → HandoffPacket.plan_steps
  2. Prima di ACT: Inject HandoffPacket come system message
  3. ACT vede il piano, opera con contesto
Pseudocodice:
  think_result = await anthropic_think(messages)
  handoff = HandoffPacket(
    source="anthropic",
    target="minimax",
    objective=think_result.objective,
    plan_steps=think_result.steps,
    ground_facts=think_result.facts
  )
  act_messages = inject_handoff(handoff, messages)
  act_result = await minimax_act(act_messages)
```

**Bug 2 — MEDIA: Dual shrink inconsistente**

```
File: ai-router-proxy.py ~3200 (_shrink_and_retry_minimax) + executor loop
Fix: Unificare shrink path
  1. Pre-check → _shrink_and_retry_minimax (esistente)
  2. Executor 400 error → STESSA _shrink_and_retry_minimax
  3. Non esiste path separato
  4. Retry count: pre-check = 1, executor loop = max 3
```

**Bug 3 — MEDIA: Race condition trim-state**

```
File: context_shrink.py
Fix: Atomic read-check-write via SQLite transaction
  BEGIN IMMEDIATE;
  SELECT state FROM trim_state WHERE fingerprint = ?;
  -- se stale (> 30s), aggiorna
  UPDATE trim_state SET state=?, updated_at=NOW() WHERE ...;
  COMMIT;
  Con SQLite: "BEGIN IMMEDIATE" acquisisce write lock immediato
```

**Bug 4 — BASSA: mixed_fail_last_status su forward_minimax exception**

```
File: ai-router-proxy.py ~forward_minimax()
Fix:
  try:
    result = await _forward_minimax(...)
    mixed_fail_last_status = result.status
  except Exception as e:
    mixed_fail_last_status = 500  # era: non impostato
    raise
```

**Bug 5 — MEDIA: Fast-path 400 silent drop**

```
File: ai-router-proxy.py ~executor loop
Fix: Dopo shrink, se ritorna 400:
  1. Log: "SHRINK_FAILED_400"
  2. Fallback: _mixed_haiku_rescue() (stesso path degli altri fallback)
  3. Se Haiku anche 400 → return 400 al client
```

### 3.7 File da Creare/Modificare

| File | Azione | Righe | Descrizione |
|---|---|---|---|
| `src/handoff_packet.py` | **CREARE** | ~80 | HandoffPacket dataclass + inject_handoff() |
| `src/semantic_cache.py` | **CREARE** | ~120 | SQLite semantic cache senza dipendenze esterne |
| `src/anti_loop_guard.py` | **CREARE** | ~60 | Semantic early-stopping via embedding cosine |
| `src/glm_backend.py` | **MODIFICARE** | ~15 | Bug 1: injection HandoffPacket in ACT path |
| `src/ai-router-proxy.py` | **MODIFICARE** | ~60 | Bugs 2,4,5 + anti-loop guard + semantic cache |
| `src/context_shrink.py` | **MODIFICARE** | ~20 | Bug 3: atomic trim-state |
| `src/streaming_relay.py` | **MODIFICARE** | ~10 | HHEM-gate pre-streaming |

**3 nuovi file, 4 modificati.** Zero nuove dipendenze (solo stdlib + SQLite).

---

## 4. Trade-off & Rischi

| Decisione | Pro | Contro | Rischio residuo |
|---|---|---|---|
| HandoffPacket invece di IMCP | Standard Plan-and-Act, 207 citazioni | Richiede refactor THINK→ACT | Basso — backward compatible |
| Semantic cache senza embedding esterno | Zero dipendenze, 20-70% cost save | Hash-only, no semantic similarity reale | Medio — usare hash + rough similarity come proxy |
| HHEM come gate pre-VERIFY | Zero token cost, outperform GPT-4 | Serve endpoint locale :4002 | Basso — già nel progetto |
| CoVe light invece di CoVe full | Overhead gestibile, effetto reale | Meno completo di CoVe 4-step | Basso — full CoVe è over-engineering |
| SQLite per trim-state atomico | Standard library, ACID compliant | Write lock su alta concorrenza | Basso — "BEGIN IMMEDIATE" è nativo |
| MAX 3 cicli loop cap | Deterministico, semplice | Task complessi potrebbero servire di più | Mitigato: budget tokens residuale permette continuation |
| LLMLingua-2 scartato | Nessuna nuova dipendenza | Perdiamo compressione 20x | Nullo — LLMLingua-2 è training-time, non inference-time |
| MCP/A2A non adottati | Nessun scope creep | Non usiamo standard emergenti | Accettabile — MCP è per tool, non per routing interno |

---

## 5. Timeline

| Step | Giorni | Contenuto | Deliverable |
|---|---|---|---|
| 1 | 1 | Fix Bug 1 (CRITICA) + HandoffPacket | `handoff_packet.py`, THINK iniettato in ACT |
| 2 | 1 | Fix Bugs 2, 4, 5 | Patch atomico su ai-router-proxy.py |
| 3 | 1 | Fix Bug 3 (race trim-state) | `context_shrink.py` atomic |
| 4 | 2 | Semantic Cache | `semantic_cache.py` |
| 5 | 1 | Anti-Loop Guard | `anti_loop_guard.py` |
| 6 | 1 | HHEM-Gate wiring + CoVe light | `streaming_relay.py` + verify chain |
| 7 | 1 | Test end-to-end tutte le 7 modalità | `test_handoff.sh` |
| **Totale** | **7** | | |

---

## 6. Cosa Scarto e Perché

**Da Piano 1**:
- ~~IMCP protocol~~ → Inventato, nessuno standard lo usa
- ~~LLMLingua-2~~ → Dipendenza torch, training-time non inference-time
- ~~Adapter ABC pattern~~ → Overhead per 2 modelli, interface minima sufficiente
- ~~Trigger CoVe 500 token~~ → Troppo granulare; meglio trigger su fact-density

**Da Piano 2**:
- ~~Scope mixed-mode only~~ → Esteso a tutte le 7 modalità
- ~~LLMLingua citata ma non implementata~~ → Semantic cache al suo posto
- ~~ContextBudgeter come file separato~~ → Integrato in anti_loop_guard.py

**Da Piano 3**:
- ~~MCP server/client~~ → Per orchestrazione esterna, non routing interno
- ~~A2A protocol~~ → Richiede agent registration + skill discovery, overkill
- ~~KV-Cache compression~~ → Server-side, non fattibile lato proxy
- ~~Schema Transformer~~ → Type-safe già coperto da HandoffPacket
- ~~Prompt Caching~~ → Già in token_counter.py, non serve ripetere
- ~~10 nuovi file~~ → 3 file nuovi totali

**Cosa tengo da tutti e 3**:
- ✅ CoVe light (da P1)
- ✅ HandoffPacket strutturato (da P2)
- ✅ Anti-loop deterministico (da P2)
- ✅ HHEM come gate (da P3)
- ✅ Focus su bug reali (da P2)
- ✅ Trade-off quantificati (da P2)
- ✅ Ricerca arXiv/GitHub solida (da P2)

---

## 7. Criteri di Successo Misurabili

| Metric | Baseline | Target | Misura |
|---|---|---|---|
| Bug 1: ACT senza contesto | 100% delle richieste | 0% (piano iniettato) | Log verifica handoff_id presente |
| Bug 2: Dual shrink | Inconsistente | 100% same path | Codice unificato |
| Bug 3: Race trim | Concurrency failure possibile | 0 failures in load test | 1000 concurrent req, 0 errori |
| Bug 4: Status non impostato | mixed_fail_last_status missing | 100% set | Log grep |
| Bug 5: 400 silent drop | Cade silenziosamente | Log + fallback chain | 400 dopo shrink → fallback |
| Token cost saving | Baseline attuale | −15-25% | Semantic cache hit rate |
| Loop detection | Nessuna protezione | 100% bounded | Max 3 cicli enforced |
| HHEM gate | Wire esistente, non usato | 100% pass | Coverage test |

---

## 8. Failed Approaches (da non ripetere)

| Approccio | Perché Fallito | Lezione |
|---|---|---|
| IMCP (Piano 1) | Zero adozione, standard invented | Mai inventare standard se esistono pattern consolidati |
| 10 file nuovi (Piano 3) | Scope creep puro | Un proxy deve rimanere un proxy |
| LLMLingua-2 per inference | È training-time, non inference | Prima capire cosa fa una tech, poi se applicarla |
| KV-Cache proxy-side | Server-side only | Non proporre ciò che non si può implementare |
| MCP per routing interno | MCP è per tool/agent esterno | Usare lo strumento giusto per il contesto giusto |
| CoVe full 4-step per tutto | Overhead 4x per ogni richiesta | Trigger adattivo invece di always-on |
