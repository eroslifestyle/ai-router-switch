"""Bug (trovato 2026-09-05): StreamingRelay.relay(chat_fp_for_rewrite="default") ha un
default silenzioso, e in ai-router-proxy.py nessuno dei 10 call site di `await relay(`
lo sovrascriveva col fp reale della chat. Effetto misurato: router-usage.jsonl e
BUG-CATALOG.md attribuiscono OGNI richiesta (qualunque modalita') alla stessa chiave
"default", perdendo la possibilita' di distinguere le chat concorrenti nella telemetria.

Regressione a livello sorgente (non serve avviare il proxy): ogni `await relay(`
in ai-router-proxy.py deve passare `chat_fp_for_rewrite=fp`.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PROXY = SRC / "ai-router-proxy.py"
MINIMAX = SRC / "pipeline_minimax.py"


def _relay_calls(src: str) -> list[str]:
    """Estrae ogni chiamata `await relay(...)` completa (contatore di parentesi:
    il regex naivo [^)]* si tronca alla prima `)` annidata e i call site hanno
    parene fino a 3 livelli, es. _effective_minimax_model(...) / f"...lower()")."""
    chiamate = []
    for m in re.finditer(r"await relay\(", src):
        depth = 1
        for i in range(m.end(), len(src)):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    chiamate.append(src[m.start():i + 1])
                    break
    return chiamate


def test_ogni_relay_passa_il_fp_reale():
    src = PROXY.read_text()
    chiamate = _relay_calls(src)
    assert len(chiamate) >= 10, f"attese >=10 chiamate a relay(), trovate {len(chiamate)}"
    senza_fp = [c for c in chiamate if "chat_fp_for_rewrite" not in c]
    assert not senza_fp, f"relay() senza chat_fp_for_rewrite=fp: {senza_fp}"


def test_ogni_relay_in_pipeline_minimax_passa_il_fp_reale():
    src = MINIMAX.read_text()
    chiamate = _relay_calls(src)
    assert len(chiamate) >= 2, f"attese >=2 chiamate a relay(), trovate {len(chiamate)}"
    senza_fp = [c for c in chiamate if "chat_fp_for_rewrite" not in c]
    assert not senza_fp, f"relay() senza chat_fp_for_rewrite=fp: {senza_fp}"
