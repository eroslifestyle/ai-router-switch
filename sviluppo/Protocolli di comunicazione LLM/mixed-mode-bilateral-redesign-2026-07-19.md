# Architectural Design Document — Comunicazione Bilaterale Mixed-Mode (THINK Anthropic → ACT MiniMax)

Data: 2026-07-19 · Basato su ricerca web reale (fonti verificate, GitHub + arXiv/HuggingFace)

## 0. Bug diagnosticati (analisi codice, `src/ai-router-proxy.py`)

| # | Severità | File:Linea | Problema |
|---|---|---|---|
| 1 | 🔴 CRITICA | `_pipeline_think_act()` L.2714 vs L.2762 | Il piano generato da Anthropic in fase THINK non viene iniettato nei messaggi passati a MiniMax in fase ACT (`_build_act_body` riceve lo stesso `orig["messages"]` di `_build_think_body`, senza il piano). MiniMax opera senza sapere cosa Anthropic ha deciso → risposte contraddittorie tra i turni, percepite come loop. |
| 2 | 🟡 MEDIA | L.2652 vs L.2800 | Dual shrink inconsistente: il pre-check chiama `_shrink_and_retry_minimax`, ma il path 400 dentro l'executor loop salta lo shrink e va diretto a `_mixed_haiku_rescue`. |
| 3 | 🟡 MEDIA | L.3621-3636 | Race condition sul trim-state file: richieste concorrenti con lo stesso fingerprint possono leggere un trim stale/oscillante. |
| 4 | 🟢 BASSA | L.2770-2774 | `mixed_fail_last_status` non viene impostato quando `forward_minimax` lancia un'eccezione → il check 429 successivo (L.2831) è sempre `False` per quel caso. |
| 5 | 🟡 MEDIA | L.4001-4008 | Fast-path 400: se `_shrink_and_retry_minimax` ritorna 400 di nuovo, cade silenziosamente senza handling. |

## 1. Ricerca — Fonti

### GitHub / Framework

| Fonte | Rilevanza |
|---|---|
| [openai/swarm](https://github.com/openai/swarm) (archiviato, sostituito da Agents SDK) | Nessuno stato nascosto tra chiamate — ogni handoff porta esplicitamente tutto il contesto via `context_variables`. |
| [langchain-ai/langgraph-swarm-py](https://github.com/langchain-ai/langgraph-swarm-py) | Il modello successivo va compilato con un "task description" esplicito con tutto il contesto rilevante; senza checkpointer lo swarm "dimentica" chi era attivo — analogo diretto del problema di stato per-fingerprint. |
| [microsoft/LLMLingua](https://github.com/microsoft/LLMLingua) (EMNLP'23/ACL'24) | Compressione prompt fino a 20x con un modello piccolo locale; LLMLingua-2 comprime solo la parte "context", non instruction/question. |
| [AG2 Context Variables](https://docs.ag2.ai/latest/docs/user-guide/advanced-concepts/orchestration/group-chat/context-variables/) + [AutoGen conversation patterns](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns/) | `carryover` (riassunto iniettato) + context-variables strutturate FUORI dalla history → token-efficient. [Discussion #7144](https://github.com/microsoft/autogen/discussions/7144): stato spalmato nei messaggi = comportamento imprevedibile. |
| [crewAIInc/crewAI #724](https://github.com/crewAIInc/crewAI/issues/724) / [#928](https://github.com/crewAIInc/crewAI/issues/928) | Caso reale del bug 1: contesto perso quando il passaggio è implicito invece che esplicito (`context=[task]` + `expected_output`). |
| [LiteLLM Reliability](https://docs.litellm.ai/docs/proxy/reliability) + [Routing](https://docs.litellm.ai/docs/routing) | Fallback tipizzati per classe di errore (context_window / rate_limit / content_policy), `enable_pre_call_checks`, cooldown per deployment su 429. [Issue #26015](https://github.com/BerriAI/litellm/issues/26015): 429 mid-stream senza fallback → hang. |
| [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | Il lead salva il piano in memoria esterna prima di delegare; ogni delega richiede 4 elementi: obiettivo, formato output, tool consentiti, confini del task. |

### Paper / arXiv

| Fonte | Rilevanza |
|---|---|
| [Plan-and-Act (arXiv:2503.09572)](https://arxiv.org/abs/2503.09572) | Planner→Executor: il piano deve viaggiare in forma strutturata, non prosa libera. |
| [Architecting Resilient LLM Agents: Plan-then-Execute (arXiv:2509.08646)](https://arxiv.org/abs/2509.08646) | Control-flow integrity: l'executor non può ridefinire il piano, solo eseguirlo. |
| [Chain of Agents, Google, NeurIPS'24 (arXiv:2406.02818)](https://arxiv.org/abs/2406.02818) | Communication Unit accumulata invece del testo integrale tra worker sequenziali; +10% su RAG/full-context. |
| [MemGPT (arXiv:2310.08560)](https://arxiv.org/abs/2310.08560) | Summarization ricorsiva (nuovo summary = vecchio summary + messaggi evitti), memoria a due tier. |
| [ACON (arXiv:2510.00615)](https://arxiv.org/abs/2510.00615) | Compressione unificata (un solo punto) di observations + history: −26÷54% token di picco, >95% accuracy mantenuta. |
| [When Agents Do Not Stop (arXiv:2607.01641)](https://arxiv.org/html/2607.01641v1) | 47 progetti con loop infiniti, 66% su LangGraph/AutoGen. Il loop nasce quando un feedback path (es. retry/rescue) resta fuori dal bound principale. |
| [Semantic Early-Stopping (arXiv:2606.27009)](https://arxiv.org/pdf/2606.27009) + [Google ADK LoopAgent](https://google.github.io/adk-docs/agents/workflow-agents/loop-agents/) | Terminazione non delegabile al modello: cap iterazioni, no-progress detection, budget token, timeout esterni. Round 1-2 catturano ~75% del miglioramento. |
| Chain-of-Verification (CoVe, Meta) | Le domande di verifica non devono condizionare sul draft, altrimenti l'errore si copia nel verdetto. |
| [Survey of Context Engineering (arXiv:2507.13334)](https://arxiv.org/pdf/2507.13334) · [Communication-Centric Survey LLM-MAS (arXiv:2502.14321)](https://arxiv.org/html/2502.14321v2) | L'efficienza di comunicazione inter-agente è il collo di bottiglia sistemico. |

## 2. Pattern identificati

1. **Explicit Handoff Payload** — nessuno stato implicito; obiettivo, formato output, vincoli, confini sono campi obbligatori.
2. **Structured Intermediate Representation / Plan-as-Contract** — il piano viaggia come JSON, l'executor lo esegue senza reinterpretarlo.
3. **Context Variables fuori dalla history** — stato condiviso in canale separato dai messaggi, non accumulato nella conversazione.
4. **Single Compression Point + Pre-Call Check** — conteggio/compressione token in un unico componente, riusato da ogni error-path.
5. **Recursive Summarization con anchor** — perdita di contesto controllata e monotona, mai troncazione posizionale cieca.
6. **Typed Error Fallbacks** — 400 (context_window) e 429 (rate_limit) sono classi distinte con policy distinte, mai mescolate.
7. **Bounded Loop with External Guards** — cap iterazioni, no-progress detection, budget token, timeout; il bound copre OGNI feedback path incluso il rescue.
8. **Independent Verification** — il verifier non condiziona sul draft, cita gli step del piano che verifica.

## 3. Design proposto

### 3.1 Handoff THINK→ACT: `HandoffPacket` strutturato (fix bug 1)

Al termine di THINK, il proxy costruisce un pacchetto strutturato iniettato nella chiamata MiniMax:

```json
{
  "handoff_version": 1,
  "objective": "<goal del task, 1-2 frasi>",
  "plan": [{"id": "S1", "action": "...", "expected_output": "...", "constraints": ["..."]}],
  "context_digest": "<riassunto ricorsivo, vedi 3.2>",
  "ground_facts": ["<fatti verificati con fonte file:linea>"],
  "boundaries": ["cosa NON fare"],
  "output_format": "<schema atteso>",
  "iteration": {"n": 2, "max": 4, "prev_result_hash": "..."}
}
```

Regole:
- `objective`, `output_format`, `plan`, `boundaries` obbligatori. Se THINK non produce un piano parsabile → packet marcato `"degraded": true` (mai handoff vuoto silenzioso).
- MiniMax esegue citando `S<n>`; se uno step è ineseguibile lo dichiara — non ridefinisce il piano (control-flow integrity).
- Il packet vive fuori dalla history conversazionale: rigenerato/aggiornato ogni ciclo, non accumulato nei messaggi.

### 3.2 Compressione token unificata: `ContextBudgeter` (fix bug 2, 5)

Un unico componente, un'unica API, usato da tutti i path:

- Pre-call check: stima token prima di ogni chiamata MiniMax; oltre budget → shrink.
- Il path 400 dentro l'executor loop richiama lo **stesso** `ContextBudgeter.shrink()` del pre-check (non salta più a rescue diretto). 1 retry, poi rescue solo se fallisce ancora.
- Strategia di shrink a livelli:
  1. Drop dei tool-result voluminosi più vecchi, sostituiti da stub (`[output di S2: 4.2KB, esito OK]`);
  2. Summarization ricorsiva: `context_digest = summarize(digest_precedente + messaggi_evitti)`, con anchor fissi (primo messaggio utente + HandoffPacket mai compressi);
  3. (opzionale) LLMLingua-2 locale come compressore token-level a costo API zero.
- Poiché `context_digest` vive nel HandoffPacket, lo shrink non fa mai perdere il piano.

### 3.3 Stato trim atomico per-fingerprint (fix bug 3)

- Scrittura atomica: `tempfile` nella stessa dir + `os.replace()`.
- Lock per-fingerprint in-process (`threading.Lock` in dict keyed by fingerprint).
- Trim-level monotono e versionato: scritture con versione ≤ corrente vengono ignorate.
- TTL sul record: fingerprint inattivo oltre soglia → stato rimosso.

### 3.4 Error recovery coerente 400/429 (fix bug 2, 4)

Tabella unica `error_class → policy`, usata da tutti i path (mixed, anthropic, rescue):

| Errore | Policy |
|---|---|
| 400 context-too-large | `ContextBudgeter.shrink()` → retry 1x → rescue. HandoffPacket sempre preservato nel retry. |
| 429 con `x-should-retry: true` | Retry breve con jitter, rispetta `retry-after`. Stesso codice in path anthropic e mixed (dedup di `_mixed_haiku_rescue`). |
| 429 persistente | Cooldown backend + fallback al tier successivo (m2.7→M3→Haiku), portando il HandoffPacket intatto. |
| Eccezione in `forward_minimax` | `mixed_fail_last_status` impostato in `finally`/`except` con sentinella esplicita (mai lasciato al valore precedente). |

### 3.5 Terminazione esplicita del loop bilaterale

Guard deterministici esterni, mai delegati al modello:

1. Cap hard iterazioni THINK↔ACT: `MAX_MIXED_ITERATIONS = 3-4`.
2. No-progress detection: hash/similarità output ACT vs iterazione precedente (`prev_result_hash`); simile oltre soglia → stop (intercetta direttamente "ripete sempre le stesse cose").
3. Budget token cumulativo per richiesta + timeout wall-clock.
4. Il bound copre TUTTI i feedback path (rescue, shrink-retry, fallback 429 incrementano lo stesso contatore) — oggi il rescue è fuori dal bound, ed è lì che nasce il loop apparente.
5. Early-stopping generate: all'esaurimento del cap, ultima chiamata "sintetizza senza tool" invece di stop silenzioso.
6. Verify indipendente (CoVe-style): THINK verifica ACT dal piano (S1..Sn completati? conforme a output_format?), non rileggendo il draft come verità.

## 4. Trade-off

| Aspetto | Status quo | Design proposto |
|---|---|---|
| Token/richiesta mixed | History piena, doppio shrink incoerente | +200-600 token per HandoffPacket+digest; −25÷50% sul contesto ritrasmesso (ordine ACON: −26÷54% picco) |
| Latenza | 1 THINK + N ACT non bounded | +1 chiamata summarization solo quando si evince contesto; cap iterazioni riduce la coda lunga delle richieste patologiche |
| Costo summarization | zero (troncazione cieca, ma allucinazioni) | ~0.3-1K token/update, eseguibile su Haiku/M2.7; LLMLingua-2 locale = zero costo API se adottato |
| Complessità codice | 2 path shrink + rescue duplicati (bug-prone) | Netto negativo: dedup in `ContextBudgeter` + tabella error→policy unica |
| Robustezza | trim state raceable, `mixed_fail_last_status` stale | scritture atomiche + versioning (I/O trascurabile, file <1KB) |
| Qualità | contraddizioni, loop, allucinazioni da troncamento | piano sempre presente all'executor, perdita contesto controllata, loop bounded con uscita utile |

Rischio residuo dichiarato: il digest ricorsivo introduce una piccola possibilità di deriva semantica cumulativa, mitigata da anchor non comprimibili e verify CoVe-style — non eliminabile al 100% secondo le fonti consultate.

## 5. Prossimi passi implementativi

1. Fix bug 1 (handoff): iniettare HandoffPacket in `_build_act_body`.
2. Unificare shrink (bug 2, 5): far convergere pre-check ed executor-loop 400 sullo stesso `ContextBudgeter`.
3. Trim state atomico (bug 3): `os.replace()` + lock + versioning.
4. `mixed_fail_last_status` su exception (bug 4).
5. Guard anti-loop: cap iterazioni + no-progress detection che copre anche il rescue path.
6. Test: `sviluppo/tests/test_glm_modes.sh` come riferimento per istanza isolata; aggiungere `test_mixed_mode.sh` dedicato.
