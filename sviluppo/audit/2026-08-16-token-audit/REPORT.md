# Audit consumo token e integrità del dialogo fra modelli — 2026-08-16

**Stato al momento della misura:** HEAD `7694c45`, `src/` pulito, servizio `ai-router` active
(pid 185725, riavviato alle 17:52), modalità globale `anthropic`, 0 errori nelle ultime 400
righe di log. Finestra principale: 7 giorni (14.817 richieste), con separazione sul restart
delle 17:52 dove il traffico post-fix esiste.

**Fonti:** sidecar `~/.claude/logs/router-usage.jsonl` (166.565 righe, 5 troncate e scartate),
`~/.claude/logs/ai-router.log` (31,3 MB, letto con `grep -a`), `logs/BUG-CATALOG.jsonl`
(293 firme), `logs/debug-errors.jsonl`. Una sonda diretta verso le porte di modalità 8772 e
8775 (non tocca la modalità globale).

---

## 1. Consumo per modalità — 7 giorni

| mode | req | input | output | cache read | cache creation | %cache | creat/read | tool KB | n tool |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mix-am-2 | 6.988 | 134,7M | 6,26M | 846,6M | 375,9M | 95% | 0,444 | 110 | 63 |
| mix-gm | 4.191 | 242,0M | 2,73M | 14,9M | 4,66M | 33% | 0,312 | 114 | 64 |
| minimax | 1.696 | 32,6M | 0,80M | 12,7M | 3,34M | 25% | 0,262 | 170 | 98 |
| mix-am | 967 | 5,0M | 0,97M | 157,7M | 68,3M | 94% | 0,433 | 156 | 108 |
| anthropic | 604 | 0,04M | 0,59M | 110,6M | 29,8M | 98% | 0,269 | 181 | 131 |
| mix-gm-2 | 279 | 7,5M | 0,05M | 9,9M | 0,73M | 58% | 0,074 | 256 | 275 |
| glm | 55 | 0,66M | 0,001M | 0,32M | 0 | 47% | — | 73 | 59 |
| local | 37 | 0,07M | 0,30M | 0 | 0 | 0% | — | 87 | 49 |

Split sul restart delle 17:52 (dove c'è traffico post-fix): `anthropic` scende a
**creat/read 0,014** con 204.260 read/req; `mix-gm-2 → glm` passa a **126.208 read/req** con
100% di hit e input sceso da 22.197 a 15.553. I fix del 16/08 tengono.

**Attenzione a due colonne:** GLM non riporta **mai** `cache_creation` (0 su tutte le righe),
quindi il suo `creat/read = 0` non significa «cache perfetta», significa «campo assente». E
il dato GLM anteriore a `b4ed405` (17:37) non è confrontabile: l'input che risulta pagato
pieno (73.372/req su 2.614 richieste `mix-gm → glm`) è in larga parte `cache_read` che il
parser leggeva nel posto sbagliato. La riga `mix-gm` della tabella va letta come **non
misurata**, non come «242M token bruciati».

---

## 2. Finding

### F1 — `thinking` con firma non-Anthropic: 1.001 turni utente falliti con 400 🔴

Il più costoso e il più concreto.

**Misura.** 1.001 eventi `relay_error_400` con
`messages.N.content.0: Invalid \`signature\` in \`thinking\` block`:
mix-am-2 553, mix-am 442, anthropic 6, mix-ag-2 1. Per giorno: 04/08 245, 09/08 677,
10/08 28, 14/08 46, **15/08 6** — vivo, non chiuso. L'indice è quasi sempre
`messages.1.content.0` (677) o `messages.3.content.0` (325): il primo o il secondo turno
assistant, mai la coda.

**Causa, provata con sonda diretta.** Interrogando la porta 8775 (glm) con
`thinking.type=enabled`, la risposta contiene:

```
blocco thinking: signature='2d6ffcf0856f4b39aa5edc24'  chiavi=['signature','thinking','type']
```

24 caratteri esadecimali: non è una firma crittografica Anthropic. MiniMax emette blocchi
`thinking` nel **99%** delle risposte in mix-am-2 (3.614 su 3.655) e nel 98% in mix-gm; GLM
nel 31%. Quei blocchi arrivano al client, entrano nella cronologia, e al turno successivo il
ruolo THINK li rimanda ad Anthropic, che li rifiuta.

**Perché il router non lo intercetta.** `strip_thinking_blocks`
([anthropic_body.py:99](src/anthropic_body.py#L99)) esiste ed è corretta, ma è applicata solo
in **uscita** verso i provider non-Anthropic — [ai-router-proxy.py:855](src/ai-router-proxy.py#L855)
(glm), [:877](src/ai-router-proxy.py#L877) (qwen), [:904](src/ai-router-proxy.py#L904) (local),
più `minimax_body`. Manca il gemello in **entrata** verso Anthropic:
[forward_anthropic.py:296](src/forward_anthropic.py#L296) sanifica gli id `server_tool_use`
prodotti da MiniMax — che è **esattamente lo stesso problema, già risolto** il 26/07 — ma non
tocca le firme thinking.

**Fix minimo.** Nel percorso verso Anthropic, rimuovere dalla cronologia i blocchi `thinking`
la cui `signature` non ha il formato Anthropic (o è assente). Non serve un nuovo modulo:
`anthropic_body` è già il posto giusto e `strip_thinking_blocks` è già scritta — basta una
variante selettiva chiamata accanto a `sanitize_server_tool_ids`, nelle **due** gemelle
`forward_anthropic` / `forward_anthropic_direct` (il commento a
[forward_anthropic.py:271](src/forward_anthropic.py#L271) avverte già che modificarne una
sola le fa divergere).

### F2 — mix-gm: il THINK lavora su un contesto compresso, e nessuno lo dice 🟠

1.371 righe `GLM preventivo shrink` nel log, **in corso durante questa analisi**:

```
[18:52:05] context_shrink sticky: compressi i primi 50 messaggi di 301, coda=242 -> 523491b <= 600000b
[18:52:05] GLM preventivo shrink 647762b -> 523491b
```

I primi 50 messaggi su 301 vengono sostituiti da un riassunto di `trim_smart`, che tronca
ogni messaggio a `TRUNCATE_MAX_LEN = 1800` caratteri
([trim_smart.py:9](src/trim_smart.py#L9)). Il modello THINK di mix-gm risponde quindi su una
conversazione mutilata mentre in mode `anthropic` la stessa conversazione passa intera — e il
client non riceve alcun segnale della differenza. È la voce 11 del TODO («manca la misura
qualitativa di TRUNCATE_MAX_LEN») vista dal lato dell'utente: non è una costante da tarare,
è una perdita di informazione sistematica su 1.371 richieste.

Nota sulla cache: lo shrink sticky fa il suo lavoro (il prefisso resta stabile), ma la coda
cresce a ogni turno — 236 → 239 → 242 messaggi, 521.588 → 523.491 byte. Quando supererà i
600.000, K salterà da 50 a 100 e la cache si invaliderà in blocco. È il costo previsto dello
sticky; vale la pena saperlo prima di vedere il picco.

### F3 — lo shrink proattivo verso MiniMax ignora le modalità `-2` 🟠

[ai-router-proxy.py:523](src/ai-router-proxy.py#L523):

```python
_MINIMAX_BACKEND_MODES = {"minimax", "mix-am", "mix-gm"}
```

Mancano `mix-am-2` e `mix-gm-2`, che fanno ACT su MiniMax esattamente come le loro modalità
base. `mix-am-2` è **la modalità più usata del sistema**: 6.988 richieste, il 47% del
traffico dei 7 giorni. Il commento sopra quella riga descrive il bug che lo shrink doveva
chiudere («il client manda Opus con 1M di contesto, MiniMax ne regge 200K, il body va grezzo
finché non torna 400»): in mix-am-2 quel bug non è mai stato chiuso.

Non ho una prova diretta di troncamenti causati da questo — in mix-am-2 il `ctx_gate` misura
sul limite del client (1M) e quindi non li segnala, ed è proprio questo il punto: **il buco
non è osservabile con la strumentazione attuale**. Lo classifico come rischio strutturale da
verificare, non come danno misurato.

Stessa dimenticanza, ma inerte, in [context_manager.py:238](src/context_manager.py#L238):
`_provider_for` manda `mix-am-2`, `mix-gm-2`, `qwen`, `local` e `mix-al` sul ramo `'glm'` per
default. È senza conseguenze solo perché il suo unico chiamante è codice morto (vedi F7).

### F4 — mode `local` / `mix-al`: protocollo e telemetria entrambi ciechi 🟠

37 richieste in 7 giorni, e su tutte: `cache_read = 0`, `cache_creation = 0`,
`stop_reason` **vuoto in 36 casi su 37**, `outcome = unknown` nel 97%, output medio 8.047
token — il più alto di ogni modalità. In più 21 errori 500 da litellm
(`Broken pipe`, `Connection reset`). Senza `stop_reason` il client non sa se la generazione
è finita o è stata tagliata, e nessuna analisi di consumo su questa modalità significa
qualcosa. Da tenere presente prima di dare per buono qualunque numero su `local`/`mix-al`.

### F5 — output generato e buttato: ~300k token in 27 risposte 🟡

27 risposte chiuse con `stop_reason = max_tokens`: mix-gm 6 (195.840 token di output,
32.640 a risposta — cioè il tetto di z.ai), mix-am-2 4 (104.192), mix-gm-2 15, glm 2. Una
risposta troncata a metà di un `tool_use` non è utilizzabile: quei token sono pagati e persi,
e il turno va rifatto.

Collegato: `clamp_glm_max_tokens` ([glm_backend.py:342](src/glm_backend.py#L342)) scatta
**2.714 volte**. Il clamp è necessario (z.ai rifiuta oltre 32.768 con 400), ma è silenzioso:
il client chiede 64k, ne ottiene al massimo 32k e lo scopre solo dal troncamento.

### F6 — 333 richieste MiniMax perse per timeout di lettura 🟡

`forward_exception: Timeout on reading data from socket`, 333 occorrenze in mode `minimax`,
ultima il 14/08. La sessione globale ha `sock_read=120`
([ai-router-proxy.py:1038](src/ai-router-proxy.py#L1038)); l'override esiste solo per il
non-streaming ([router_utils.py:42](src/router_utils.py#L42)). In streaming, 120 secondi di
silenzio upstream — plausibili durante un thinking lungo di M3, che a differenza di Anthropic
non manda `ping` — chiudono la connessione.

### F7 — due metodi morti che l'analisi orfani non vede 🟢

`airouter-info orfani` dice «267 simboli top-level, 0 mai referenziati». Vero, ma **non entra
nelle classi**: dei 122 metodi definiti in `src/`, due non sono referenziati da nessuna parte,
nemmeno dai test —
[context_manager.py:100](src/context_manager.py#L100) `ContextManager.post_check()` e
[context_manager.py:138](src/context_manager.py#L138) `ContextManager.reassign()`, con la sua
catena di fallback fra modelli che sembra viva e non lo è. Vale la pena estendere
`airouter-info orfani` ai metodi: costa poco e questa classe di residui è invisibile oggi.

### F8 — 4 modalità su 12 non sono validate da traffico reale 🟢

Sull'intero storico del sidecar: `mix-ag-2` **2 richieste in assoluto** (ultima 09/08),
`mix-al` 34 (08/08), `mix-ag` 69 (09/08), `qwen` 948 (08/08). Qualunque regressione in queste
quattro è invisibile. `mix-ag-2` in particolare è dichiarata, ha una porta assegnata (8785) e
non è mai stata esercitata: le due sole richieste che ha visto includono una delle 1.001 di F1.

### F9 — `sprechi` ha lo stesso difetto già noto a `costo` 🟢

Il checkpoint avverte che `airouter-info costo` stampa percentuali assurde da quando la cache
è sana. **`sprechi` ha lo stesso problema**: dichiara «anthropic 99% del suo input» perché
`input_tokens` vale ~2 quando la cache funziona, e qualunque numero diviso per 2 è enorme. La
colonna «% del suo input» va ricalcolata su `input + cache_read`, o tolta.

---

## 3. Cosa NON è un problema (verificato)

- **`tool_choice` orfano dopo lo strip.** Tutti e tre i percorsi (`filter_tools_for_backend`,
  `strip_server_tools_for_minimax`, `qwen_tool_trim`) rimuovono `tool_choice` quando `tools`
  si svuota e chiamano `sanitize_tool_choice`. Resta un solo scarto semantico, minore: un
  `{"type":"tool","name":X}` che punta a un tool rimosso viene degradato ad `auto`, quindi il
  client che pretendeva quel tool può ricevere testo.
- **`defer_loading` orfano.** `sanitize_defer_loading` è chiamata su tutti e tre i percorsi.
  La voce del TODO che la dava mancante in `strip_server_tools_for_minimax` era già obsoleta.
- **`role=system` buttati.** 2.344 scarti registrati fino al **16/08 13:59**, poi zero: il
  choke-point di [ai-router-proxy.py:768](src/ai-router-proxy.py#L768) li converte prima dello
  smistamento. Il commento a [router_utils.py:496](src/router_utils.py#L496) che parla di
  «voce aperta nel TODO» è rimasto indietro rispetto al codice.
- **`path` assente nel 95% delle righe del sidecar.** Non è un bug: il campo esiste da
  `dbca72e` (15:34) e le modalità che risultano scoperte (mix-am-2, mix-gm, mix-am, minimax)
  non hanno avuto traffico dopo quell'ora. Il relay lo passa sempre.
- **`DebugLogger.capture() got an unexpected keyword argument 'snippet'`** (33 occorrenze):
  nessuna chiamata con `snippet=` esiste più nel codice, ultima occorrenza 14/08.

---

## 4. Priorità suggerita

1. **F1** — un turno utente su cento in mix-am/mix-am-2 finisce in 400 evitabile, e il fix ha
   già un precedente identico nello stesso file.
2. **F3** — una riga da correggere, sulla modalità che porta metà del traffico.
3. **F6** — un timeout da alzare o un keepalive da tollerare; 333 richieste perse.
4. **F2** — decidere se il contesto compresso in mix-gm va segnalato al client o ridotto meno
   aggressivamente. È una scelta di prodotto, non un bug.
5. **F4**, **F5**, **F7**, **F8**, **F9** — debito misurabile, nessuna urgenza.
