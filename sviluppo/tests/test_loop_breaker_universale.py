"""Stress test universale per loop-breaker su tutte le 14 modalità.

Testa: (1) ogni modello reale ha finestra misurata in MODEL_CONTEXT_MAP,
       (2) should_break() genera decisioni coerenti su tutte le modalità,
       (3) should_break() non solleva eccezioni su input sporchi.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import loop_breaker
from model_context_map import MODEL_CONTEXT_MAP, has_measured_context_limit
from role_routing import VALID_MODES, resolve_route

_CHIAVI_ESATTE = {k.lower() for k in MODEL_CONTEXT_MAP}


# ── Test 1: Ogni modello raggiungibile ha finestra misurata ────────────────────

def test_ogni_modello_raggiungibile_ha_finestra_misurata():
    """Per OGNI modalità e ogni ruolo (THINK, ACT), il modello effettivo
    deve avere una finestra misurata in MODEL_CONTEXT_MAP, altrimenti
    should_break() decide su default=200k, un valore non verificato.
    """
    # Modelli richiesti in arrivo (i nomi che il client sa)
    richiesti = [
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-haiku-4-5-20251001",
    ]

    non_misurati = []
    for mode in VALID_MODES:
        for modello_richiesto in richiesti:
            try:
                provider, modello_effettivo = resolve_route(mode, modello_richiesto)
                effettivo = modello_effettivo or modello_richiesto
                if not has_measured_context_limit(effettivo):
                    non_misurati.append(
                        f"mode={mode} richiesto={modello_richiesto} effettivo={effettivo}"
                    )
                # Serve il match ESATTO, non basta uno per prefisso: il prefisso fa
                # ereditare in silenzio la finestra di un ALTRO modello. E' gia'
                # successo (glm-4.7 prendeva i 128k di glm-4, sottostima del 36%),
                # e has_measured_context_limit da sola direbbe "misurato".
                elif effettivo.lower() not in _CHIAVI_ESATTE:
                    non_misurati.append(
                        f"mode={mode} richiesto={modello_richiesto} effettivo={effettivo}"
                        f" — nessuna voce ESATTA, eredita da un prefisso"
                    )
            except Exception as e:
                non_misurati.append(
                    f"mode={mode} richiesto={modello_richiesto} — ERRORE: {e}"
                )

    if non_misurati:
        msg = "Modelli senza finestra misurata (aggiungi a MODEL_CONTEXT_MAP):\n"
        msg += "\n".join(f"  - {m}" for m in non_misurati)
        raise AssertionError(msg)


# ── Test 2: Matrice decisione su tutte le modalità ──────────────────────────────

def test_matrice_decisione_su_tutte_le_modalita():
    """should_break() deve decidere in modo coerente su quattro casi:
    - repeats < soglia: False indipendentemente da ctx_pct/limite_misurato
    - repeats >= soglia, ctx_pct < 80%, limite misurato: False (polling sano)
    - repeats >= soglia, ctx_pct >= 80%, limite misurato: True (impantanamento)
    - repeats >= soglia, ctx_pct >= 80%, limite NON misurato: False (default non fidato)
    """
    richiesti = ["claude-opus-5", "claude-haiku-4-5-20251001"]
    N = loop_breaker.LOOP_BREAKER_N
    MIN_PCT = loop_breaker.LOOP_BREAKER_MIN_CTX_PCT

    for mode in VALID_MODES:
        for modello_richiesto in richiesti:
            try:
                provider, modello_effettivo = resolve_route(mode, modello_richiesto)
                effettivo = modello_effettivo or modello_richiesto
                misurato = has_measured_context_limit(effettivo)
            except Exception:
                continue  # skip errori resolve_route

            # Caso 1: repeats < soglia -> False sempre
            assert not loop_breaker.should_break(N - 1, 0.95, misurato), (
                f"case1 FAIL: mode={mode}, modello={effettivo}, "
                f"repeats={N-1} (< {N}) non deve interrompere"
            )

            # Caso 2: repeats >= soglia, ctx < 80%, misurato -> False
            ctx_basso = 0.47  # il polling sano misurato il 2026-09-05
            assert not loop_breaker.should_break(N, ctx_basso, misurato), (
                f"case2 FAIL: mode={mode}, modello={effettivo}, "
                f"repeats={N}, ctx={ctx_basso:.0%} < {MIN_PCT:.0%}, "
                f"misurato={misurato} — non deve interrompere"
            )

            # Caso 3: repeats >= soglia, ctx >= 80%, misurato=True -> True
            if misurato:
                ctx_alto = 1.31  # l'impantanamento vero misurato il 2026-08-28
                assert loop_breaker.should_break(N, ctx_alto, misurato), (
                    f"case3 FAIL: mode={mode}, modello={effettivo}, "
                    f"repeats={N}, ctx={ctx_alto:.0%} >= {MIN_PCT:.0%}, "
                    f"misurato=True — DEVE interrompere"
                )

            # Caso 4: repeats >= soglia, ctx >= 80%, misurato=False -> False
            ctx_alto = 1.31
            assert not loop_breaker.should_break(N, ctx_alto, False), (
                f"case4 FAIL: repeats={N}, ctx={ctx_alto:.0%}, "
                f"misurato=False — non deve interrompere senza finestra nota"
            )


# ── Test 3: should_break() non solleva mai eccezioni ──────────────────────────────

def test_should_break_non_solleva_mai():
    """should_break() gestisce input sporchi senza eccezioni.
    Valori non validi vengono normalizzati, non sollevati.
    """
    casi_sporchi = [
        (4, None, True),           # ctx_pct None
        (4, "abc", True),          # ctx_pct stringa non numerica
        (4, -1, True),             # ctx_pct negativo (ma valido numericamente)
        (4, float('nan'), True),   # NaN
        (4, float('inf'), True),   # infinity
        (-1, 0.9, True),           # repeats negativo
        (-100, 0.9, False),        # repeats negativo, limite non misurato
    ]

    for repeats, ctx_pct, limite_misurato in casi_sporchi:
        try:
            result = loop_breaker.should_break(repeats, ctx_pct, limite_misurato)
            assert isinstance(result, bool), (
                f"should_break({repeats}, {ctx_pct}, {limite_misurato}) "
                f"ritorno {type(result)} invece di bool"
            )
        except Exception as e:
            raise AssertionError(
                f"should_break({repeats}, {ctx_pct}, {limite_misurato}) "
                f"sollevò eccezione: {e}"
            ) from e


# ── Test 4: Il proxy usa should_break() nel blocco loop-breaker ───────────────────

def test_il_proxy_usa_should_break():
    """Regressione sorgente: il proxy deve richiamare loop_breaker.should_break()
    nel blocco di chiusura loop-breaker, e _err_response non deve essere
    raggiungibile senza passare da should_break().
    """
    PROXY = ROOT / "src" / "ai-router-proxy.py"
    src = PROXY.read_text()

    # should_break deve essere presente nel blocco
    assert "loop_breaker.should_break(" in src, (
        "il proxy non chiama loop_breaker.should_break()"
    )

    # Verifica strutturale: il pattern deve stare nel blocco loop-breaker
    import re
    blocco = re.search(
        r"import loop_breaker.*?except Exception as _e:\s*\n\s*log\(f\"loop-breaker EXC",
        src, flags=re.DOTALL)
    assert blocco, "blocco loop-breaker non trovato nel proxy"
    testo = blocco.group(0)

    assert "should_break(" in testo, (
        "should_break() non e' nel blocco loop-breaker del proxy"
    )

    # _err_response deve stare DENTRO il ramo che passa da should_break
    i_should = testo.index("should_break(")
    i_errore = testo.index("_err_response")
    assert i_should < i_errore, (
        "la chiamata a _err_response non sta dopo should_break() nel codice"
    )
