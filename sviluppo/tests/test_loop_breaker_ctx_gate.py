"""Il loop-breaker chiude il giro SOLO a contesto saturo (fix 2026-09-05).

Bug: `check()` guarda la firma dell'ultimo assistant, che e' identica sia quando un
modello si e' impantanato sia quando una chat sana ripete lo stesso tool_use a
cadenza regolare (polling: attesa di un job, watch, monitor). Con la sola soglia a 4
turni il router restituiva 400 a sessioni di lavoro legittime.

Misurato: sid:82e400cf (mode anthropic) ha preso il 400 con 5 turni a 112 secondi
esatti l'uno dall'altro e 94k token su 200k di finestra (47%); l'impantanamento vero
per cui il modulo e' nato (sessione 74070e0d, mode gpt) girava al 167% della finestra.
La saturazione e' l'unico segnale che separa i due casi.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import loop_breaker  # noqa: E402

PROXY = ROOT / "src" / "ai-router-proxy.py"


def test_soglia_di_saturazione_fra_i_due_casi_misurati():
    """La soglia deve stare fra il polling sano (47%) e l'impantanamento (167%)."""
    assert 0.47 < loop_breaker.LOOP_BREAKER_MIN_CTX_PCT < 1.67


def test_il_proxy_interrompe_solo_sopra_la_soglia():
    """Il 400 deve stare dietro il controllo should_break().

    Regressione a livello sorgente: `handle()` non e' isolabile senza avviare il
    proxy, ma il difetto era strutturale — il `return _err_response(...)` non era
    protetto da una guardia di contesto. Ora deve arrivare DOPO should_break().
    """
    src = PROXY.read_text()
    blocco = re.search(
        r"import loop_breaker.*?except Exception as _e:\s*\n\s*log\(f\"loop-breaker EXC",
        src, flags=re.DOTALL)
    assert blocco, "blocco loop-breaker non trovato in ai-router-proxy.py"
    testo = blocco.group(0)

    assert "should_break(" in testo, (
        "il loop-breaker non chiama should_break()")
    # Il 400 deve venire DOPO should_break(), non prima.
    i_guardia = testo.index("should_break(")
    i_errore = testo.index("_err_response")
    assert i_guardia < i_errore, "il 400 non e' protetto da should_break()"


def test_la_misura_del_contesto_precede_il_loop_breaker():
    """ctx_check deve essere gia' calcolato quando il loop-breaker lo legge."""
    src = PROXY.read_text()
    i_precheck = src.index("CTX.pre_check(")
    i_loop = src.index("import loop_breaker")
    assert i_precheck < i_loop, (
        "il loop-breaker legge ctx_check prima che venga calcolato")
