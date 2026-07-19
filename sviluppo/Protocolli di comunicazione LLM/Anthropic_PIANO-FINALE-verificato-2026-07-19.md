# PIANO FINALE — Comunicazione Multi-Modello AI Router Proxy (verificato contro il codice reale)

**Creato**: 2026-07-19 · **Eseguito da**: Claude (Sonnet), ruolo THINKER/VERIFIER come da `prompt-ricerca-universale-3-models.md`
**HEAD verificato**: `b470dfc` · **Metodo**: evidence-gate (R2 anti-allucinazione) su tutti i claim dei 3 piani + della sintesi MiniMax, non solo nuova ricerca

---

## 0. Executive Summary — la scoperta critica

Invece di ripetere da zero la ricerca web (già fatta 4 volte, ~30 fonti solide raccolte), ho applicato la regola evidence-gate: **ho letto il codice sorgente reale riga per riga** per ogni bug rivendicato dai documenti esistenti (P1, P2, P3, e `Minimax_piano-universale-3-models-2026.md` — quest'ultimo è l'esecuzione MiniMax di questo stesso prompt).

**Risultato**: dei "5 bug reali" su cui si fonda la vittoria di P2 nell'audit comparativo (bug reali 10/10, criterio con peso 30%), **solo 1 su 5 è ancora presente nel codice attuale**. Gli altri 4 sono stati risolti da commit di redesign precedenti alla stesura dei documenti stessi (tutti datati 2026-07-19), e nessuno dei 3 modelli — incluso quello che ha eseguito letteralmente questo prompt — se n'è accorto.

Questo cambia lo scope corretto dell'intero progetto: non serve l'infrastruttura pesante (HandoffPacket dataclass, anti-loop guard, semantic cache, HHEM validator come nuovo layer) proposta da tutti e 4 i documenti per "fixare i bug" — perché 4 di quei bug non esistono. Il piano finale qui sotto è quindi molto più piccolo.

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

## 3. Cosa resta valido dei 4 documenti precedenti

L'audit comparativo (`audit-comparativo-piani.md`) resta un buon confronto pro/contro sui pattern architetturali (non riprodotto qui per intero). Due claim che ho ri-verificato personalmente via ricerca web mirata (`[WEB→M3]`, non ripetendo l'intera ricerca già fatta):

- **arXiv:2607.01641** "When Agents Do Not Stop: Uncovering Infinite Agentic Loops in LLM Agents" — confermato esistente (citato anche da Reddit/LinkedIn/altri paper correlati sullo stesso tema, 2026). Rilevante in generale per agenti iterativi, ma — vedi sopra — non ho trovato nella pipeline `mix-am` attuale il pattern di loop che descrive.
- **Ecosistema MCP/A2A 2026**: confermato — MCP standardizza agent→tool/dati, A2A standardizza agent↔agent per delega di task tra agenti *esterni* indipendenti. Conferma indipendente della conclusione già raggiunta da tutti e 3 i piani + dall'audit: **MCP/A2A non applicabili** all'instradamento interno tra fasi (THINK/ACT/VERIFY) della stessa richiesta in un singolo proxy.

Le altre fonti citate (LLMLingua-2/Microsoft, HHEM/Vectara, MemGPT, Chain-of-Verification/Meta, Plan-and-Act, Chain of Agents/Google, LiteLLM) sono paper e repository reali e noti anche indipendentemente da questa sessione — le tratto come attendibili senza ri-verifica puntuale (dichiarato: non ricontrollate una per una qui).

---

## 4. Il piano (rivisto alla luce della verifica)

Principio guida, diretta conseguenza della Sezione 1: **non costruire infrastruttura per bug che non esistono**. Lo scope realistico è molto più piccolo di quanto proposto da P1 (8 file), P2 (0 file nuovi ma refactor ampio su tutte le 7 modalità), P3 (10 file) e dalla sintesi MiniMax (3 file nuovi).

### 4.1 Fix immediati (unico bug reale + gap trovato)

- **Bug 3 — trim-state race**: scrittura atomica in `_trim_context_after_response` via `tempfile` nella stessa directory + `os.replace()`; lock in-process per-fingerprint (`threading.Lock` in un dict keyed by `fp`) attorno al blocco read→unlink in `handle()`. ~15 righe, stesso file, zero nuove dipendenze (stdlib).
- **Gap VERIFY non-enforcing in `mix-gm`**: se il testo di VERIFY contiene un marcatore di incongruenza (pattern testuale semplice tipo "INCOERENTE"/"NO"/"ERRORE" da chiedere esplicitamente nel prompt di verify — non serve JSON schema rigido, coerente con la scelta "piano è testo libero" già presa nel codice, riga 2741), fare **un** retry di ACT con la nota di correzione iniettata nel `system`, altrimenti procedere. Cap a 1 retry, coerente con il pattern "R1/R2" già presente altrove nel codice. ~30 righe in `_glm_minimax_think_act_verify`.

### 4.2 Miglioramento opzionale (non bug fix): arricchire il piano THINK→ACT

Il piano oggi è testo libero iniettato in `system` — funziona (il bug 1 non esiste), ma non dà all'executor un modo strutturato per dichiarare "step ineseguibile", né campi tipo `ground_facts`/`boundaries` utili a un futuro gate HHEM.

- Se si vuole investire qui: aggiungere al prompt di `_build_think_body` marcatori leggeri (`OBIETTIVO:` / `VINCOLI:` / `NON FARE:`) come testo, non JSON — zero rischio di parse-fail, coerente con la "Version C" già scelta dal team.
- **Non creare `handoff_packet.py`** con dataclass strutturate finché non c'è un consumer reale (es. il gate HHEM di 4.3) che ne tragga beneficio. Farlo ora sarebbe over-engineering per campi che oggi nessun codice legge.

### 4.3 HHEM come gate opzionale (zero-token, già disponibile nel progetto)

Wiring minimo: dopo VERIFY (`mix-gm`) o dopo ACT (`mix-am`, `mix-ag`) su risposte sopra una soglia pratica (es. >300-500 caratteri), estrarre le frasi principali con uno split semplice e chiamare `hhem-score` locale (`:4002`, `~/.claude/services/hhem/`). Score < 0.5 → log + eventuale singolo retry (stesso bound di 4.1). **Da verificare prima di implementare**: che il servizio HHEM sia raggiungibile in rete dal processo del proxy (non assunto qui, solo verificato che il servizio esiste nel resto del setup utente).

### 4.4 Compressione token — non toccare

Il sistema attuale (Token Counter, Context Rewrite, Context Shrink HHEM-adaptive, Summarizer, Model Context Map — 10+ layer già documentati) è più sofisticato di quanto LLMLingua/semantic-cache aggiungerebbero, al costo di una dipendenza pesante (torch ~2GB) o di una nuova cache SQLite da mantenere. Nessuno dei 4 documenti ha dimostrato con dati reali del progetto (solo stime percentuali) che serva altro. **Raccomandazione: non implementare nulla qui** finché non c'è un problema misurato nei log di produzione.

### 4.5 Anti-loop — solo audit mirato, non nuovo modulo

Non costruire `anti_loop_guard.py`: nessuna evidenza nel codice attuale di loop non-bounded in `mix-am` (verificato riga per riga). Resta da fare un audit grep-first (non una riscrittura) di `mix-ag`/`mix-gm`/`_glm_execute_with_chain` per confermare che ogni fallback chain termini in un numero finito di step — dalla lettura fatta (Sezione 2) sembra di sì (3 hop lineari, mai ricorsivi), ma non è stato tracciato ogni ramo.

### 4.6 File da creare/modificare

| File | Azione | Righe stima | Perché |
|---|---|---|---|
| `src/ai-router-proxy.py` | MODIFICA | +45/−5 | Trim-state atomico (4.1), VERIFY-enforcing in `mix-gm` (4.1), marcatori testuali nel piano THINK (4.2, opzionale) |
| `src/hhem_gate.py` | NUOVO (opzionale) | ~40 | Wiring minimo HHEM su ACT/VERIFY — solo se si decide di investire in 4.3 |

**Totale: 1 file modificato obbligatorio, 1 file nuovo opzionale.** Ben sotto il limite di 6 file richiesto dal prompt originale, e sotto quanto proposto da tutti e 4 i documenti precedenti (3-10 file nuovi).

---

## 5. Trade-off & Rischi

| Decisione | Pro | Contro | Rischio residuo |
|---|---|---|---|
| Fix minimale (no HandoffPacket/anti-loop-guard) | Zero scope creep, coerente con evidenza reale | Se emergono NUOVI bug non coperti da questa verifica, serve un secondo giro | Basso — la Sezione 1 copre i 5 claim esistenti punto per punto |
| Trim-state con `tempfile`+`os.replace()`+lock in-process | Standard POSIX, elimina la race verificata | Lock in-process non copre eventuali processi multipli (solo thread/task nello stesso processo asyncio) | Basso — il proxy gira come singolo processo aiohttp |
| VERIFY-enforcing via marcatore testuale (non JSON) | Coerente con la scelta già fatta nel codice, zero parse-fail nuovi | Meno robusto di uno schema tipizzato | Medio-basso — mitigato dal cap a 1 retry, mai un loop |
| HHEM gate come opzionale, non obbligatorio | Non blocca la release del fix principale | Se non implementato, resta il gap "VERIFY osservazionale" parzialmente aperto (il retry di 4.1 lo mitiga già in parte) | Accettabile |
| Nessun intervento su compressione token | Nessun nuovo rischio/dipendenza | Non si sa se serve finché non si misura | Basso — la misura è il prossimo passo naturale, non un'implementazione al buio |

---

## 6. Timeline

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

## 7. Cosa scarto e perché (aggiornato dopo verifica)

| Da | Scarto | Perché (motivo aggiornato) |
|---|---|---|
| P1, P2, P3, MiniMax | `HandoffPacket`/`IMCP` come dataclass/protocollo strutturato dedicato | Il bug che doveva risolvere (piano non iniettato) **non esiste** — il piano è già iniettato come testo in `system`. Un arricchimento testuale (4.2) basta finché non c'è un consumer che ha bisogno di campi tipizzati. |
| P1, P2, P3, MiniMax | `anti_loop_guard.py` con cap iterazioni + no-progress detection | Nessuna evidenza di loop non-bounded in `mix-am` (verificato); l'escalation esistente è già a 2 round hardcoded. Serve solo un audit, non un nuovo modulo. |
| P1 | IMCP protocollo custom, LLMLingua, Adapter ABC, Semantic Cache | Over-engineering indipendentemente dalla verifica bug: dipendenza torch ~2GB, protocollo inventato senza adozione esterna, pattern prematuro per 2-3 provider reali. |
| P2 | `ContextBudgeter` come componente unificato dedicato | Il sistema di shrink esistente (Sezione 1, bug 2) è già un trade-off deliberato, non un bug da unificare — un refactor qui sposterebbe complessità senza un problema misurato a giustificarlo. |
| P3 | MCP/A2A, KV-Cache compression, 10 file nuovi, Unified Pipeline, Prompt Cache Layer | Confermato dalla verifica web (Sezione 3): MCP/A2A sono per orchestrazione *esterna* multi-agente, non routing interno. KV-Cache è server-side. Il resto è scope creep per un proxy che deve restare un proxy. |
| MiniMax (sintesi) | Semantic Cache SQLite, Delta-Token, CoVe light generalizzato | Stesso principio di 4.4: nessun dato reale di produzione che dimostri il bisogno; da rivalutare solo dopo aver misurato. |
| Tutti e 4 | I 5 bug come motivazione primaria del piano | 4 su 5 non esistono più (Sezione 1) — la motivazione va sostituita con: 1 bug reale + 1 gap trovato in questa sessione (Sezione 2). |

---

## 8. Nota metodologica per il prossimo ciclo (anti-allucinazione)

- **Prima di qualsiasi piano di fix su questo codice, verificare il bug contro `git log -p` + lettura diretta del file** — non fidarsi di un documento precedente anche se dettagliato con file:linea, perché il codice cambia: qui, 5+ commit di redesign (`2026-07-01` → `2026-07-13`) hanno reso obsoleta l'analisi *prima ancora* che venisse riscritta indipendentemente da 3 modelli diversi, incluso uno che ha eseguito letteralmente questo stesso prompt di ricerca.
- Questo file ha una data di validità: HEAD `b470dfc` (2026-07-19). Se sono passati giorni/commit, rileggere il codice prima di implementare qualsiasi punto della Sezione 4.

---

**File di riferimento nella stessa cartella**: `PIANO-UNIVERSALE-multi-modello.md` (template originale, ora superato da questo documento), `audit-comparativo-piani.md`, `comunicazione-multi-modello-2026.md` (P1), `mixed-mode-bilateral-redesign-2026-07-19.md` (P2), `piano-comunicazione-bilaterale-2026.md` (P3), `Minimax_piano-universale-3-models-2026.md` (esecuzione MiniMax di questo stesso prompt).
