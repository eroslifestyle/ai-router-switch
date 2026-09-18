# Il campo `thinking` di Haiku inoltrato a z.ai raddoppia la latenza di GLM

**Documento di condivisione** · 2026-09-18 · progetto `ai-router-switch` · fix `976b56e`, in produzione dal 2026-09-17 21:33

Questo documento descrive un problema di latenza diagnosticato, corretto e misurato su un proxy Anthropic-compatible che instrada verso z.ai (GLM). È scritto per chi gestisce un setup analogo: contiene la diagnosi, il codice del fix, i numeri pre/post reali, i limiti di ciò che il fix ottiene e le trappole di misura in cui siamo caduti. In fondo c'è un prompt pronto da incollare al proprio assistente.

Nessuna affermazione qui è dedotta: tutte vengono da misure sul traffico reale o da richieste dirette all'API z.ai.

---

## 1. In breve

Claude Code allega `"thinking": {"type": "adaptive"}` alle richieste che indirizza ai modelli Haiku. Verso i provider terzi il client normalmente **omette** quel campo; un proxy che fa da tunnel lo inoltra invece così com'è. **z.ai lo onora**: il modello GLM che riceve la richiesta entra in thinking esteso senza che nessuno l'abbia chiesto.

Nel nostro caso valeva **25.692 richieste in 30 giorni** nate come Haiku e atterrate su GLM, il **73%** con thinking in risposta, per **89 ore** di tempo. Il fix — rimuovere il campo prima di inoltrare — ha ridotto del **31% il tempo medio** e azzerato la coda di richieste oltre i 120 secondi.

---

## 2. Il sintomo e come ci siamo arrivati

Il sintomo riferito era generico: «il flow di coding è lentissimo». L'analisi del sidecar su 30 giorni (406 ore di richieste) ha mostrato che **il thinking pesava il 69% del tempo totale**, 281 ore.

Prima di arrivare al campo `thinking` abbiamo escluso, con i dati, quattro cause plausibili:

- **cache-miss**: smentita, il `read` della cache cresce regolarmente e la `creation` resta bassa;
- **overhead degli MCP**: smentita;
- **Write contro Edit** (rigenerazione di file interi invece di modifiche chirurgiche): smentita;
- **ordine dei `tools[]`** nel body: ininfluente.

Abbiamo anche valutato la scorciatoia «passare tutto alla modalità Anthropic pura»: dà +12% di velocità al costo di 3,3× la quota Anthropic. Non è una soluzione, è uno scambio.

Il test che ha isolato la causa è un A/B diretto contro l'API, stessa richiesta con tool, inviata in tre varianti:

| campo `thinking` | tempo |
|---|---|
| assente | 6,4 s |
| `{"type":"disabled"}` | 6,6 s |
| `{"type":"adaptive"}` | **16,0 s** |

Il campo ereditato dalle richieste Haiku era l'unica differenza.

---

## 3. Il fix

Rimuovere il campo — **non** metterlo a `{"type":"disabled"}` — quando il modello *richiesto dal client* non supporta il thinking. L'omissione replica ciò che il client fa nativamente verso i provider terzi ed è accettata da tutti i modelli GLM.

```python
def strip_thinking_for_model(body: bytes, req_model: str | None, log_fn=None) -> bytes:
    """Toglie il campo top-level `thinking` se il modello RICHIESTO non lo supporta."""
    if os.environ.get("AIROUTER_GLM_KEEP_THINKING") == "1":   # rollback senza deploy
        return body
    try:
        from anthropic_capabilities import unsupported_fields
    except Exception:
        return body
    if req_model and "thinking" not in unsupported_fields(req_model):
        return body                      # il modello richiesto lo supporta: non toccare
    try:
        d = json.loads(body)
    except Exception:
        return body
    if not isinstance(d, dict) or "thinking" not in d:
        return body
    d.pop("thinking")
    if log_fn:
        log_fn(f"GLM strip thinking: {req_model} non supporta il campo, rimosso")
    return json.dumps(d).encode()
```

Tre dettagli che contano più del codice:

**Il punto di innesto.** Va chiamata sul body *prima* di riscrivere il campo `model`, e le va passato il modello **richiesto dal client**, non quello upstream. Da noi l'ordine è:

```
strip_thinking_blocks(body)
  → strip_thinking_for_model(body, req_model)
    → set_body_model(body, glm_model)
```

Se le si passa il modello upstream (un nome GLM) la condizione non scatta mai e il fix diventa un no-op silenzioso. È l'errore più facile da commettere: lo stesso scambio, altrove nel nostro proxy, aveva reso inerte un controllo per 4.692 richieste senza che nulla lo segnalasse.

**Il criterio è il modello richiesto, non la modalità del router.** Così le richieste che usano legittimamente il thinking — quelle del modello di ragionamento — restano intatte. Si toglie il campo solo dove non l'aveva chiesto nessuno.

**Serve un interruttore di rollback senza deploy** (da noi una variabile d'ambiente più un restart): permette di smentire il fix con una misura invece che con un'opinione.

---

## 4. Le misure, pre e post

Stesso filtro nelle due finestre: richieste nate come Haiku, atterrate su `glm-4.7`, `status == 200`. PRE = 24 h prima del deploy; POST = dal deploy in poi.

| metrica | PRE (N=854) | POST (N=752) | delta |
|---|---|---|---|
| quota risposte con thinking | 50,5% | 40,7% | −9,8 punti |
| total_ms medio | 16.812 | 11.551 | **−31%** |
| mediana | 9.115 | 7.407 | −19% |
| richieste oltre 120 s | 9 (1,1%) | **0** | azzerata |
| output_tokens medi | 658 | 419 | −36% |

Spezzato per sottoinsieme, dove si vede il meccanismo vero:

| | PRE (ms / token) | POST (ms / token) |
|---|---|---|
| risposte **con** thinking | 24.345 / 979 | 16.060 / 561 |
| risposte **senza** thinking | 9.136 / 331 | 8.456 / 322 |

Le risposte senza thinking non si muovono. Scomponendo i −5.261 ms di media:

- **~28%** del guadagno viene dall'aver ridotto il *numero* di richieste che pensano (50,5% → 40,7%);
- **~72%** viene dall'aver **accorciato quelle che pensano comunque** (24,3 s → 16,1 s, 979 → 561 token).

Con `adaptive` ereditato, 2 risposte su 3 erano *solo thinking fino a `max_tokens`*: è quella la coda oltre i 120 secondi, ed è la voce che il fix cancella. Descrivere il fix come «GLM penserà di meno» racconta la parte sbagliata del risultato.

Il dato è stabile su campioni diversi: il fast-check su 1 ora dava 11.519 ms di media, il campione da 752 richieste dà 11.551.

---

## 5. Cosa il fix NON ottiene

**La quota di risposte con thinking non va a zero, e non deve.** Da noi si è fermata al ~40%, ed è il comportamento corretto. Due ragioni, entrambe misurate:

- Il contatore «blocchi thinking» della telemetria conta i blocchi **nella risposta**, non il campo nella richiesta. Sono due cose diverse: si può aver rimosso il campo nel 100% delle richieste e vedere comunque thinking in uscita.
- **glm-4.7 pensa di suo sui prompt lunghi.** A/B diretto su z.ai con ~29 KB di contesto e 8 tool: **thinking 3/3 sia a campo omesso sia con `{"type":"disabled"}`**. Sul prompt corto, invece, entrambe le varianti danno 0. Il campo non governa quel comportamento: è del modello.

Conseguenza pratica: **non inseguire un fix v2 via campo `thinking`**. L'abbiamo provato contro l'API, non dedotto. Per ridurre il residuo la leva è la dimensione del contesto, non un parametro della richiesta.

---

## 6. Quattro trappole di misura

**La doc z.ai su `disabled` è smentita dal nostro endpoint.** La documentazione lascia intendere che su alcuni modelli il thinking non sia disattivabile; il probe reale risponde **200** su glm-5.3, glm-5-turbo, glm-4.7 e glm-4.6V (429 solo su glm-5V-Turbo, con la nostra chiave). Il commento nel nostro codice afferma ancora il contrario ed è il residuo di una convinzione presa dalla doc e mai testata: il fix resta valido perché l'omissione è comunque la scelta giusta, ma la motivazione scritta lì non regge. Testate voi.

**`input_tokens` di z.ai esclude il contesto cacheato** (`cache_read_input_tokens` è contato a parte). Un `input_tokens: 38` non significa body vuoto o system perso: ci ha fatto inseguire un bug inesistente per mezz'ora.

**Il calo di `output_tokens` non è un confondente, e si dimostra.** Il sospetto legittimo è che il POST sia più veloce perché i task erano più facili. Se fosse così, il calo si vedrebbe anche nel sottoinsieme senza thinking — dove invece l'output resta a ~325 token in entrambe le finestre. È thinking non più generato.

**Contate le ore che hanno davvero traffico**, non l'ampiezza della finestra. Il nostro campione POST copriva 9,4 ore di orologio ma solo 3 ore con richieste reali. Spezzate sempre il campione per ora prima di dichiarare un risultato.

---

## 7. Checklist operativa

1. **Gate di applicabilità.** Il problema esiste solo se il vostro proxy inoltra a z.ai un body che contiene ancora il campo `thinking`. Verificatelo nel codice, nei log e con l'A/B a tre varianti del §2. Se i tre controlli sono negativi, il fix non vi serve.
2. **Applicate lo strip nel punto giusto**, con interruttore di rollback, e aggiungete un test che copra **entrambi** i rami: campo rimosso per il modello di esecuzione, campo intatto per il modello di ragionamento.
3. **Registrate la baseline PRE prima del deploy.** Senza, il POST non dimostra nulla.
4. **Dopo il deploy verificate la copertura prima delle prestazioni**: ogni richiesta inoltrata a GLM deve avere la riga di log dello strip (da noi 251/251). Una copertura parziale spiega un miglioramento parziale.
5. **Misurate a 24 h** con le metriche del §4, incluse le due righe per sottoinsieme: sono quelle che distinguono un fix che funziona da un campione fortunato.

---

## Appendice — prompt pronto da incollare al proprio assistente

```
Questo messaggio arriva da un'altra installazione che usa un proxy Anthropic-compatible verso z.ai (GLM) come esecutore. Hanno trovato, corretto e misurato un problema di latenza che potrebbe riguardare anche il nostro setup.

Non applicare nulla prima di aver eseguito il gate di applicabilità. Non diamo per scontato di avere lo stesso problema: se il gate è negativo, fermati e dimmelo, non adattare il fix a forza. Ogni esito che riporti dev'essere accompagnato da output letterale (conteggi, tempi, righe di log), non da affermazioni.

GATE — la condizione necessaria è che un client Claude Code invii richieste con un campo top-level "thinking" e che il nostro proxy inoltri quel body a api.z.ai/api/anthropic senza rimuoverlo. Tre controlli, dal più economico:
1) nel codice, trova il punto dove il body viene inoltrato a z.ai e verifica se qualcosa rimuove "thinking" prima dell'invio;
2) nei log, conta quante richieste nate come Haiku atterrano su un modello GLM e quante di queste producono blocchi thinking in risposta (da loro: 73% su 25.692 richieste in 30 giorni, 89 ore);
3) A/B diretto contro l'API: stessa richiesta con tool, inviata senza il campo, con {"type":"disabled"} e con {"type":"adaptive"} (da loro: 6,4 s / 6,6 s / 16,0 s).

FIX — rimuovere il campo (non metterlo a "disabled") quando il modello RICHIESTO DAL CLIENT non supporta il thinking, chiamando lo strip PRIMA di riscrivere il campo "model" del body e passandogli il modello richiesto, non quello upstream: se gli passi il modello upstream la condizione non scatta mai e il fix è un no-op silenzioso. Prevedi un interruttore di rollback senza deploy e un test che copra entrambi i rami (campo rimosso per il modello di esecuzione, campo intatto per il modello di ragionamento).

RISULTATI ATTESI, dai loro numeri (richieste Haiku → glm-4.7, status 200; PRE 24 h N=854, POST N=752): quota risposte con thinking 50,5% → 40,7%; total_ms medio 16.812 → 11.551 (−31%); mediana 9.115 → 7.407; richieste oltre 120 s da 9 a ZERO; output_tokens medi 658 → 419. Per sottoinsieme: con thinking 24.345 ms/979 token → 16.060/561; senza thinking 9.136/331 → 8.456/322, cioè praticamente fermo. Il ~72% del guadagno viene dall'accorciamento delle risposte che pensano comunque, non dall'averne ridotto il numero: sparivano le risposte di solo thinking che saturavano max_tokens.

NON ASPETTARTI che la quota di thinking vada a zero: il contatore conta i blocchi nella RISPOSTA, non il campo nella richiesta, e glm-4.7 pensa di suo sui prompt lunghi (3/3 con ~29 KB di contesto e 8 tool, sia a campo omesso sia con "disabled"). Il residuo ~40% è nativo. Non proporre un fix v2 via campo thinking: è già stato provato contro l'API.

TRAPPOLE DI MISURA: input_tokens di z.ai esclude il contesto cacheato (cache_read è a parte), quindi un valore piccolo non significa body vuoto; un calo di output_tokens non è di per sé prova di task più facili — controlla il sottoinsieme senza thinking, lì deve restare fermo; conta le ore che hanno davvero traffico, non l'ampiezza della finestra; la doc z.ai su "disabled" è smentita dal probe reale (200 su glm-5.3, glm-5-turbo, glm-4.7, glm-4.6V).

Procedi in quest'ordine: gate → baseline PRE → fix con rollback e test → verifica della copertura a log → misura a 24 h.
```
