import json
import logging
import os
from typing import Tuple

from trim_smart import build_shrink_summary, SHRINK_KEEP_TAIL
from router_utils import _repair_message_sequence
from token_counter import estimate_tokens_body, bytes_per_token
from model_context_map import get_safe_input_limit

log = logging.getLogger(__name__)

# ATTEMPT 1b (G5): degradazione progressiva prima di scendere agli ultimi 2 messaggi.
KEEP_RECENT_IMAGES = 2       # messaggi finali lasciati intatti (immagini comprese)
TOOL_RESULT_MAX_CHARS = 4000  # oltre questa soglia il tool_result viene troncato

# Il dedup lato client di Claude Code sostituisce una Read ripetuta con questo
# segnaposto, che rimanda a un tool_result PRECEDENTE. Se lo shrink ha buttato via
# quel messaggio, il rimando punta al nulla: il modello non vede mai il contenuto,
# non ricorda di averci gia' provato e riemette la stessa Read all'infinito.
# Loop misurato il 2026-08-19, sessione 74070e0d in mode gpt: 11+ turni identici da
# ~3 minuti l'uno. La storia completa pero' il router ce l'ha, quindi il contenuto
# si ripesca da li' e il segnaposto torna utile.
DEDUP_PLACEHOLDER_MARK = "file unchanged since your last Read"

# Quota del limite sicuro riservata al riassunto. Era implicitamente 3/4, perche'
# la coda era fissa a SHRINK_KEEP_TAIL messaggi e tutto il resto finiva al
# riassunto: su code-max (131k) lo shrink consegnava 179 KB dove ne erano ammessi
# ~390, cioe' buttava due terzi della finestra a ogni turno. Con la coda adattiva
# il rapporto si inverte: al riassunto una quota fissa, ai messaggi veri il resto.
SUMMARY_BUDGET_SHARE = float(os.environ.get("AIROUTER_SHRINK_SUMMARY_SHARE", "0.25"))

# Scalini della griglia su cui si cerca la lunghezza della coda: ~log2 di questo
# numero e' quante volte si serializza il corpo candidato. Tarato il 2026-08-19 su
# un corpo da 1,3 MB / 800 messaggi (il caso peggiore osservato), con richiamo
# attivo: griglia 16 -> 534 ms e 90,2% di finestra usata; 64 -> 755 ms e 97,8%.
# Si pagano 221 ms per il 7,6% di contesto in piu': in locale il prefill di quella
# finestra costa ~200 s, quindi il contesto vale incomparabilmente di piu' del
# quarto di secondo. Su corpi normali la ricerca costa una frazione di questi valori.
FIT_GRID_STEPS = int(os.environ.get("AIROUTER_FIT_GRID_STEPS", "64"))

# Cache in-process del punto di taglio scelto per ciascuna chat (fp -> quanti
# messaggi droppare dall'inizio). Senza questo, ogni turno sopra soglia
# ricalcolava keep da zero sull'intera storia: anche con la griglia quantizzata
# (FIT_GRID_STEPS) il valore ottimale slitta ad ogni turno perche' n cresce,
# spostando "gli ultimi keep messaggi" e rompendo il prefisso byte-per-byte che
# il prompt caching richiede. Tenendo fisso il PUNTO DI TAGLIO (drop_count)
# invece del CONTEGGIO (keep), il prefisso msgs[drop_count:] resta identico tra
# un turno e l'altro finche' il nuovo turno non lo fa uscire dal budget — la
# cache tiene, si paga solo l'append. Root cause diagnosticata 2026-08-22:
# creation/read 0,614 su MiniMax (67% del contesto "nuovo" per richiesta)
# contro 0,040 di Anthropic.
_STICKY_DROP_COUNT: dict = {}
_STICKY_DROP_COUNT_MAX = 2000

# Quando il punto di taglio sticky esce dal budget, il nuovo taglio lascia libero
# circa questo fraction del budget: i turni successivi ci stanno per molti turni
# senza spostare il taglio, invece di avanzare di pochi messaggi a ogni turno
# (finestra scorrevole che rompeva il prefisso e la prompt cache a ogni richiesta).
REWRITE_HEADROOM_FRACTION = float(os.environ.get("AIROUTER_REWRITE_HEADROOM", "0.25"))

# Riassunto in cache per (fp -> (drop_count, summary)): finche' il punto di taglio
# non cambia, il summary nel system viene riemesso BYTE-IDENTICO invece di essere
# ricalcolato sull'intera storia crescente (che cambiava il system a ogni turno).
_STICKY_SUMMARY: dict = {}


def _tool_result_text(content) -> str:
    """Testo di un tool_result, che il formato sia stringa o lista di blocchi."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            sub.get("text", "") for sub in content
            if isinstance(sub, dict) and sub.get("type") == "text"
        )
    return ""


def _restore_deduped_reads(full_msgs: list, tail_msgs: list) -> list:
    """Rimpiazza i segnaposto del dedup con il tool_result vero, ripescato dalla storia piena.

    Nel tail resta solo il rimando ("refer to that earlier tool_result"), mentre il
    contenuto sta in un messaggio che lo shrink ha tagliato. Qui si ricongiungono:
    tool_use_id -> path del file -> ultimo contenuto realmente letto.
    """
    key_by_id = {}
    for msg in full_msgs:
        for block in msg.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") or {}
            key = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
            if key and block.get("id"):
                key_by_id[block["id"]] = key

    content_by_key = {}
    for msg in full_msgs:
        for block in msg.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            key = key_by_id.get(block.get("tool_use_id"))
            text = _tool_result_text(block.get("content"))
            if key and text and DEDUP_PLACEHOLDER_MARK not in text:
                content_by_key[key] = text

    if not content_by_key:
        return tail_msgs

    restored = []
    for msg in tail_msgs:
        content = msg.get("content")
        if not isinstance(content, list):
            restored.append(msg)
            continue

        new_content, touched = [], False
        for block in content:
            real = None
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = _tool_result_text(block.get("content"))
                if DEDUP_PLACEHOLDER_MARK in text:
                    real = content_by_key.get(key_by_id.get(block.get("tool_use_id")))
            if real is None:
                new_content.append(block)
                continue
            new_block = dict(block)
            new_block["content"] = (
                real[:TOOL_RESULT_MAX_CHARS] + "\n…[troncato]"
                if len(real) > TOOL_RESULT_MAX_CHARS else real
            )
            new_content.append(new_block)
            touched = True

        if not touched:
            restored.append(msg)
            continue
        new_msg = dict(msg)
        new_msg["content"] = new_content
        restored.append(new_msg)

    return restored


def _tail(msgs: list, keep: int) -> list:
    """Coda riparata e con i rimandi del dedup risolti."""
    return _restore_deduped_reads(msgs, _repair_message_sequence(msgs[-keep:]))


def _fit_keep(msgs: list, model: str, safe_limit: int, make_candidate) -> int:
    """Quanti messaggi finali entrano davvero nel limite. 0 se non ne entra nessuno.

    Ricerca binaria invece di un numero fisso: la finestra utile non e' una
    costante, dipende da quanto pesano il system, i tool e i messaggi di QUESTA
    conversazione. Tenerne sei a prescindere sprecava la finestra sui modelli
    grandi e non bastava comunque su quelli piccoli.

    La ricerca e' su una GRIGLIA di ~FIT_GRID_STEPS valori, non su ogni intero.
    Due motivi. Ogni sonda costa una serializzazione piu' un parse dell'intero
    corpo candidato (`estimate_tokens_body` ci cammina dentro per pesare le
    immagini): su un corpo da 1,3 MB sono ~70 ms l'una, e cercare l'ottimo esatto
    ne chiedeva una decina. E un `keep` quantizzato cambia meno da un turno
    all'altro, il che aiuta il prefisso a restare stabile. Tenere 230 messaggi
    invece di 238 non cambia nulla; spendere mezzo secondo per scoprirlo si'.
    """
    n = len(msgs)
    if not n:
        return 0

    step = max(1, n // FIT_GRID_STEPS)
    griglia = list(range(step, n + 1, step))
    if griglia and griglia[-1] != n:
        griglia.append(n)
    if not griglia:
        griglia = [n]

    def _entra(k: int) -> bool:
        return estimate_tokens_body(make_candidate(_tail(msgs, k)), model) <= safe_limit

    lo, hi, best = 0, len(griglia) - 1, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if _entra(griglia[mid]):
            best, lo = griglia[mid], mid + 1
        else:
            hi = mid - 1

    # Nemmeno il primo scalino entra: si prova comunque il minimo, cosi' una
    # conversazione di pochi messaggi enormi non finisce a zero per colpa del passo.
    if not best and _entra(1):
        best = 1
    return best


def rewrite_for_context(body: bytes, model: str, fp: str,
                        safe_limit_override: int | None = None) -> Tuple[bytes, bool]:
    # Fail-safe: un errore nel rewrite non deve MAI bloccare il proxy.
    try:
        return _rewrite_impl(body, model, fp, safe_limit_override)
    except Exception as e:
        log.warning("rewrite_for_context fail-safe: %s", e)
        return (body, False)


def _rewrite_impl(body: bytes, model: str, fp: str,
                  safe_limit_override: int | None = None) -> Tuple[bytes, bool]:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return (body, False)

    msgs = data.get("messages", [])
    if not msgs:
        return (body, False)

    token_est = estimate_tokens_body(body, model)
    # Riserva all'output esattamente il max_tokens chiesto dal client invece di
    # una percentuale fissa: su un modello da 1M con max_tokens=32k questo
    # restituisce ~168k token di contesto in piu' rispetto al buffer del 20%.
    # Override (2026-10-01): il retry post-400 di local_backend passa il limite
    # calcolato dal n_ctx/n_prompt REALI restituiti da llama.cpp, stima a parte.
    safe_limit = safe_limit_override or get_safe_input_limit(model, data.get("max_tokens"))

    if token_est <= safe_limit:
        return (body, False)

    # ATTEMPT 1: finestra scorrevole (piu' contesto recente possibile) + summary
    # build_shrink_summary vuole il budget in CARATTERI, safe_limit e' in TOKEN:
    # passarlo senza convertire produceva un riassunto ~4x piu' piccolo del dovuto
    # e sprecava tre quarti della finestra (misurato 2026-08-02: un body da 1135KB
    # arrivava a 52k token invece dei ~196k consentiti a MiniMax).
    budget_chars = int(safe_limit * bytes_per_token(model))
    budget = int(budget_chars * SUMMARY_BUDGET_SHARE)

    # Helper per costruire il system preservando liste e cache_control
    def _build_system(system_raw, summary):
        # system_raw può essere lista (formato Claude Code), stringa, o assente/None/altro
        if isinstance(system_raw, list):
            # Preserva i blocchi originali invariati (con cache_control etc.)
            result = list(system_raw)  # shallow copy
            if summary:
                result.append({"type": "text", "text": summary})
            return result
        elif isinstance(system_raw, str):
            # Comportamento invariato per stringhe
            if system_raw:
                return (system_raw + "\n\n" + summary) if summary else system_raw
            return summary if summary else None
        else:
            # Assente, None o altro tipo
            return summary if summary else None

    # Summary in cache: riusato byte-identico finche' il punto di taglio (drop_count)
    # non cambia; ricalcolato sull'intera storia a ogni turno rompeva il system e
    # con lui la prompt cache. Stessa struttura e limiti di _STICKY_DROP_COUNT.
    last_drop = _STICKY_DROP_COUNT.get(fp, 0)
    cached = _STICKY_SUMMARY.get(fp)
    if cached and cached[0] == last_drop:
        summary = cached[1]
    else:
        summary = build_shrink_summary(msgs, budget)

    system_content = _build_system(data.get("system"), summary)

    def _make_candidate(system_val):
        def _candidate(tail: list) -> bytes:
            cand = dict(data)
            cand["messages"] = tail
            if system_val:
                cand["system"] = system_val
            cand.pop("thinking", None)
            return json.dumps(cand).encode()
        return _candidate

    _candidate = _make_candidate(system_content)

    # Punto di taglio "sticky": riusa il drop_count dell'ultimo turno invece di
    # ricalcolare keep da zero, cosi' il prefisso msgs[drop_count:] resta
    # identico tra un turno e l'altro e il prompt caching lato provider tiene.
    # Vedi commento su _STICKY_DROP_COUNT sopra.
    keep = 0
    sticky_hit = False
    if 0 < last_drop < len(msgs):
        sticky_keep = len(msgs) - last_drop
        if estimate_tokens_body(_candidate(_tail(msgs, sticky_keep)), model) <= safe_limit:
            keep = sticky_keep
            sticky_hit = True
    if not keep:
        # Taglio a scatti con margine: si cerca il keep per il budget RIDOTTO
        # (REWRITE_HEADROOM_FRACTION libero), cosi' il nuovo drop_count resta
        # valido per molti turni invece di slittare a ogni richiesta.
        headroom_limit = int(safe_limit * (1.0 - REWRITE_HEADROOM_FRACTION))
        # Il summary consuma budget: si misura il candidato col summary GIA'
        # dentro, altrimenti il keep "con margine" sfora il safe_limit e la
        # correzione sotto lo riporta al budget pieno, cancellando il margine.
        probe = _make_candidate(_build_system(data.get("system"), summary))
        # Il margine vale quando il taglio STICKY e' gia' attivo (sessione che
        # scorre). Al primo taglio di una sessione si riempie la finestra al
        # limite pieno: l'80% di riempimento e' il contratto del fit originale
        # (test_finestra_adattiva_locale), e una sessione a un turno non ha
        # prefissi da proteggere.
        fit_limit = safe_limit if not last_drop else headroom_limit
        keep = _fit_keep(msgs, model, fit_limit, probe)
        # ponytail: se il margine non basta per lo summary reale, si rientra col pieno
        if keep and estimate_tokens_body(probe(_tail(msgs, keep)), model) > safe_limit:
            keep = _fit_keep(msgs, model, safe_limit, probe)

    if keep:
        # Secondo passaggio: i messaggi appena usciti dalla finestra vengono messi
        # da parte (per sopravvivere a un /compact) e quelli PERTINENTI alla
        # richiesta attuale rientrano come estratti nel system. "Recente" e "utile"
        # non coincidono: la decisione presa duecento messaggi fa e' spesso quella
        # che serve adesso. Due passaggi e non uno perche' il blocco richiamato
        # occupa spazio, quindi la finestra va rimisurata con lui dentro.
        #
        # Il blocco richiamato va in cache insieme al summary per (fp, drop_count):
        # build_recall_block interroga la richiesta corrente (query_text = ultimo
        # messaggio user), quindi su sticky hit varrebbe un testo diverso a ogni
        # turno e romperebbe il system lo stesso. archive() viene chiamato SOLO
        # quando cambia il punto di taglio: i dropped sono gli stessi altrimenti.
        cached_recall = _STICKY_SUMMARY.get(fp) if sticky_hit else None
        recall_block = cached_recall[2] if cached_recall and len(cached_recall) > 2 else None
        if recall_block is None:
            dropped = msgs[:-keep] if keep < len(msgs) else []
            recall_block = ""
            if dropped:
                try:
                    import context_recall
                    if not sticky_hit:
                        context_recall.archive(fp, dropped)
                    recall_block = context_recall.build_recall_block(
                        fp, msgs, dropped, int(budget * context_recall.RECALL_BUDGET_SHARE))
                except Exception as e:
                    log.warning("ctx-recall fallito fp=%s: %s", fp, e)

        final_keep = keep
        recall_system = None
        if recall_block:
            # Anche qui vale lo sticky: ricalcolare keep col fit a ogni turno
            # sposterebbe il prefisso comunque, invalidando il taglio scelto sopra.
            # Il fit di questo ramo usa lo stesso limite CON margine: rientrare
            # al limite pieno cancellerebbe l'headroom e il taglio slitterebbe
            # comunque a ogni turno.
            if sticky_hit:
                keep_recall = keep
            else:
                system_recall = _build_system(system_content, recall_block)
                # Stesso limite usato per il keep principale: al primo taglio
                # (fit a limite pieno per il contratto dell'80%) un rifit con
                # margine qui sotto accorcerebbe la finestra e abbasserebbe il
                # riempimento; nelle sessioni in corso il margine protegge il
                # prefisso.
                fit_recall_limit = safe_limit if not last_drop else headroom_limit
                keep_recall = _fit_keep(msgs, model, fit_recall_limit,
                                        _make_candidate(system_recall))
                if not keep_recall:
                    keep_recall = _fit_keep(msgs, model, safe_limit,
                                            _make_candidate(system_recall))
            if keep_recall:
                final_keep = keep_recall
                recall_system = _build_system(system_content, recall_block)
            # Il richiamo non ci sta: meglio la finestra piena senza estratti.

        # Sticky salvato col keep EFFETTIVAMENTE restituito (recall incluso):
        # salvare un valore diverso sposterebbe il prefisso al turno successivo.
        drop = len(msgs) - final_keep
        if not sticky_hit or drop != _STICKY_DROP_COUNT.get(fp):
            if len(_STICKY_DROP_COUNT) > _STICKY_DROP_COUNT_MAX:
                _STICKY_DROP_COUNT.clear()
            _STICKY_DROP_COUNT[fp] = drop
            if len(_STICKY_SUMMARY) > _STICKY_DROP_COUNT_MAX:
                _STICKY_SUMMARY.clear()
            _STICKY_SUMMARY[fp] = (drop, summary, recall_block)
        if final_keep > SHRINK_KEEP_TAIL:
            log.info("shrink: keep %s a %d messaggi (drop_count=%d) fp=%s",
                     "stabile" if sticky_hit else "ricalcolato", final_keep, drop, fp)
        if recall_system is not None:
            return (_make_candidate(recall_system)(_tail(msgs, final_keep)), True)
        return (_candidate(_tail(msgs, final_keep)), True)

    # Nemmeno un messaggio ci sta: si scende ai tentativi degradanti. Il candidato
    # a coda fissa serve ancora alla scelta del piu' piccolo, in fondo.
    new_bytes = _candidate(_tail(msgs, SHRINK_KEEP_TAIL))

    # ATTEMPT 1b: degradazione progressiva - rimuove immagini vecchie e tronca tool_result
    new_1b = _degrade_images_and_tools(data, msgs, KEEP_RECENT_IMAGES, TOOL_RESULT_MAX_CHARS)
    new_1b_bytes = json.dumps(new_1b).encode()
    if estimate_tokens_body(new_1b_bytes, model) <= safe_limit:
        return (new_1b_bytes, True)

    # ATTEMPT 2: degradazione progressiva del riassunto, mai la sua rimozione
    for budget in [budget_chars // 2, budget_chars // 4, budget_chars // 8, budget_chars // 16]:
        summary_b = build_shrink_summary(msgs, budget)
        tail_b = _tail(msgs, SHRINK_KEEP_TAIL)

        cand = dict(data)
        cand["messages"] = tail_b
        sys_b = _build_system(data.get("system"), summary_b)
        if sys_b:
            cand["system"] = sys_b
        cand.pop("thinking", None)

        cand_bytes = json.dumps(cand).encode()
        if estimate_tokens_body(cand_bytes, model) <= safe_limit:
            return (cand_bytes, True)

    # Ultimo tentativo: coda ridotta a 2 messaggi ma conservando il riassunto compresso
    tail2 = _tail(msgs, 2) if len(msgs) >= 2 else _repair_message_sequence(msgs)
    summary2 = build_shrink_summary(msgs, budget_chars // 16)

    new2 = dict(data)
    new2["messages"] = tail2
    system2 = _build_system(data.get("system"), summary2)
    if system2:
        new2["system"] = system2
    new2.pop("thinking", None)

    new2_bytes = json.dumps(new2).encode()
    if estimate_tokens_body(new2_bytes, model) <= safe_limit:
        return (new2_bytes, True)

    # ATTEMPT 3: troncamento hard - un singolo messaggio piu' grande del limite non e' riducibile dai tentativi precedenti (tail, degradazione e ultimi-2 lo contengono comunque)
    def _hard_truncate_messages(candidate, target_tokens):
        """
        Helper inline per troncamento hard del testo.
        Ritorna una copia del candidato con testo troncato.
        Fail-safe: qualsiasi eccezione ritorna il candidato invariato.
        """
        TRUNC_SUFFIX = "\n…[troncato per limiti di contesto]"
        MIN_TEXT_CHARS = 500  # soglia sotto la quale un blocco non viene toccato
        try:
            result = json.loads(json.dumps(candidate))
            if estimate_tokens_body(json.dumps(result).encode(), model) <= target_tokens:
                return result

            def _get_text_size(msg):
                """Calcola la dimensione in caratteri del testo di un messaggio."""
                size = 0
                content = msg.get("content", "")
                if isinstance(content, str):
                    size = len(content)
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            size += len(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            tr_content = block.get("content", "")
                            if isinstance(tr_content, str):
                                size += len(tr_content)
                            elif isinstance(tr_content, list):
                                for sub in tr_content:
                                    if isinstance(sub, dict) and sub.get("type") == "text":
                                        size += len(sub.get("text", ""))
                return size

            def _truncate_text(text, min_chars):
                """Tronca il testo aggiungendo il suffisso di troncamento."""
                if len(text) <= min_chars:
                    return text
                return text[:min_chars] + TRUNC_SUFFIX

            # Ordina i messaggi per dimensione testuale (dal piu' grande)
            # L'ultimo messaggio viene troncato per ultimo: e' la richiesta corrente,
            # mutilarla per prima rende la risposta inutile
            messages_with_size = []
            total_msgs = len(result.get("messages", []))
            for i, msg in enumerate(result.get("messages", [])):
                size = _get_text_size(msg)
                messages_with_size.append((size, i, msg))

            messages_with_size.sort(key=lambda x: (1 if x[1] == total_msgs - 1 else 0, -x[0]))

            # Tronca iterativamente partendo dal messaggio piu' grande (non-ultimo)
            for size, idx, msg in messages_with_size:
                if size <= MIN_TEXT_CHARS:
                    continue

                content = msg.get("content", "")
                if isinstance(content, str):
                    msg["content"] = _truncate_text(content, MIN_TEXT_CHARS)
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            continue
                        if block.get("type") == "text":
                            block["text"] = _truncate_text(block.get("text", ""), MIN_TEXT_CHARS)
                        elif block.get("type") == "tool_result":
                            tr_content = block.get("content", "")
                            if isinstance(tr_content, str):
                                block["content"] = _truncate_text(tr_content, MIN_TEXT_CHARS)
                            elif isinstance(tr_content, list):
                                for sub in tr_content:
                                    if isinstance(sub, dict) and sub.get("type") == "text":
                                        sub["text"] = _truncate_text(sub.get("text", ""), MIN_TEXT_CHARS)

                # Verifica se siamo rientrati nel limite
                if estimate_tokens_body(json.dumps(result).encode(), model) <= target_tokens:
                    return result

            return result
        except Exception:
            return candidate

    new3 = _hard_truncate_messages(new2, safe_limit)
    new3_bytes = json.dumps(new3).encode()
    if estimate_tokens_body(new3_bytes, model) <= safe_limit:
        return (new3_bytes, True)

    # Nessun candidato rientra nel limite safe_limit. Si sceglie il piu' piccolo
    # per massimizzare la probabilita' che l'upstream lo accetti. Da quando
    # ATTEMPT 2 non azzera piu' il riassunto, ogni candidato lo porta con se',
    # quindi "piu' piccolo" non significa piu' "senza contesto". Il caso era
    # silenzioso e ora viene loggato perche' e' una degradazione che va vista.
    candidates = [
        (new_bytes, len(new_bytes)),
        (new_1b_bytes, len(new_1b_bytes)),
        (new2_bytes, len(new2_bytes)),
        (new3_bytes, len(new3_bytes)),
    ]
    best_bytes, best_len = min(candidates, key=lambda x: x[1])
    if best_len < len(body):
        log.warning('rewrite fallback: nessun candidato entro %d token, scelto il piu\' piccolo (%db da %db) fp=%s', safe_limit, best_len, len(body), fp)
        return (best_bytes, True)

    return (body, False)


def _degrade_images_and_tools(
    data: dict, msgs: list, keep_recent: int, tool_max_chars: int
) -> dict:
    """Rimuove immagini e tronca contenuti tool dai messaggi non recenti.

    Considera recenti i messaggi con indice >= len(msgs) - keep_recent e li lascia intatti.
    Per i messaggi non recenti, degrada i blocchi content di tipo image e tool_result.
    Formato: Anthropic Messages API (nessun riferimento a OpenAI).
    """
    def _degrade_block(block: dict, tool_max_chars: int) -> dict:
        """Elabora un singolo blocco content per il degrado."""
        if not isinstance(block, dict):
            return block

        btype = block.get("type", "")

        # Immagine base64: sostituisci con placeholder testuale
        if btype == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                return {"type": "text", "text": "[immagine rimossa per limiti di contesto]"}
            return block

        # Risultato tool: tronca stringa o elabora lista di sottoblocchi
        if btype == "tool_result":
            content = block.get("content")
            if isinstance(content, str):
                if len(content) > tool_max_chars:
                    new_block = dict(block)
                    new_block["content"] = content[:tool_max_chars] + "\n…[troncato]"
                    return new_block
                return block

            if isinstance(content, list):
                new_content = []
                for sub in content:
                    if not isinstance(sub, dict):
                        new_content.append(sub)
                        continue

                    stype = sub.get("type", "")
                    if stype == "text":
                        text = sub.get("text", "")
                        if len(text) > tool_max_chars:
                            new_content.append({
                                "type": "text",
                                "text": text[:tool_max_chars] + "\n…[troncato]"
                            })
                        else:
                            new_content.append(sub)
                    elif stype == "image":
                        src = sub.get("source", {})
                        if src.get("type") == "base64":
                            new_content.append({"type": "text", "text": "[immagine rimossa per limiti di contesto]"})
                        else:
                            new_content.append(sub)
                    else:
                        new_content.append(sub)

                new_block = dict(block)
                new_block["content"] = new_content
                return new_block

            return block

        # Altri tipi di blocco: restituisci invariato
        return block

    new_msgs = []
    for idx, msg in enumerate(msgs):
        is_recent = idx >= len(msgs) - keep_recent
        if is_recent:
            new_msgs.append(msg)
            continue

        new_msg = dict(msg)
        content = msg.get("content")

        if isinstance(content, list):
            new_content = [_degrade_block(b, tool_max_chars) for b in content]
            new_msg["content"] = new_content

        new_msgs.append(new_msg)

    result = dict(data)
    result["messages"] = new_msgs
    result.pop("thinking", None)
    return result


# _save_trim_state RIMOSSA (fix 2026-07-21): scriveva il body riscritto (tail-6 +
# summary) in TRIM_STATE_DIR; il TRIM INTERCEPT (rimosso) lo caricava al turno DOPO
# al posto della richiesta vera → il modello riceveva 6 messaggi stantii senza
# l'ultimo messaggio utente. Il rewrite è già applicato in-request: persistere lo
# stato cross-turno era il bug, non una feature.
