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

PROXY = Path(__file__).resolve().parents[2] / "src" / "ai-router-proxy.py"


def test_ogni_relay_passa_il_fp_reale():
    src = PROXY.read_text()
    chiamate = re.findall(r"await relay\([^)]*\)", src, flags=re.DOTALL)
    assert len(chiamate) >= 10, f"attese >=10 chiamate a relay(), trovate {len(chiamate)}"
    senza_fp = [c for c in chiamate if "chat_fp_for_rewrite" not in c]
    assert not senza_fp, f"relay() senza chat_fp_for_rewrite=fp: {senza_fp}"
