#!/usr/bin/env python3
"""Stress test end-to-end dei fix F1/F2/F3 del 2026-08-16, sul router VIVO.

Usa le PORTE DI MODALITA' (8771 anthropic, 8775 glm, 8781 mix-am-2, 8784 mix-gm-2):
non tocca la modalita' globale. Ogni richiesta porta l'header
``x-airouter-synthetic: 1`` cosi' il sidecar la marca e non inquina le analisi.

Ogni prova dichiara cosa si aspetta e perche', e stampa PASS/FAIL con l'evidenza.
Uso:  python3 stress_fix_20260816.py [--giri N]
"""
import argparse
import gzip
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORTE = {"anthropic": 8771, "glm": 8775, "mix-am-2": 8781, "mix-gm-2": 8784,
         "minimax": 8772}
LOG = "/home/mrxxx/.claude/logs/ai-router.log"

# Firme misurate il 2026-08-16 con sonde dirette sulle porte di modalita'.
FIRMA_GLM = "9be54cbc3de7463699fb38fa"                                  # 24 hex
FIRMA_MINIMAX = "94a6f37d40128c61ca1dc32eb7f3ab880e5f891b" + "0" * 24   # 64 hex
# Forma di una firma Anthropic: base64 lungo, fuori dall'alfabeto esadecimale.
# Questa e' inventata, quindi Anthropic DEVE rifiutarla: serve da controprova.
FIRMA_ANTHROPIC_FINTA = ("ErUBCkYIBRgCIkDvW3n+Yz9KpQm2XcRt5hLbNmA1sVfE8gK0xTzZ"
                         "/QcRtYuOpLmN4wXhVbGdJeSaKfDcBnMqZrEwTyUiIoPlAg==")

esiti = []


def chiama(porta, payload, timeout=180):
    """POST /v1/messages. Ritorna (status, dict|testo). Gestisce gzip."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{porta}/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json",
                 "anthropic-version": "2023-06-01",
                 "accept-encoding": "identity",
                 "x-airouter-synthetic": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
            enc = (r.headers.get("content-encoding") or "").lower()
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
        enc = (e.headers.get("content-encoding") or "").lower()
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"
    if "gzip" in enc:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    testo = raw.decode("utf-8", "replace")
    try:
        return status, json.loads(testo)
    except Exception:
        return status, testo


def conversazione_con_thinking(firma, testo_thinking="Devo leggere il file."):
    """Cronologia realistica di una modalita' mista: l'ACT ha risposto con un
    thinking firmato da lui, piu' un tool_use, e ora il turno torna al THINK."""
    return {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "tools": [{"name": "Read", "description": "Legge un file",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"}},
                                    "required": ["path"]}}],
        "messages": [
            {"role": "user", "content": "Leggi /etc/hostname e dimmi cosa contiene."},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": testo_thinking, "signature": firma},
                {"type": "tool_use", "id": "toolu_01AbCdEfGhIjKlMnOpQrStUv",
                 "name": "Read", "input": {"path": "/etc/hostname"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01AbCdEfGhIjKlMnOpQrStUv",
                 "content": "leobox"},
            ]},
        ],
    }


def errore_di_firma(corpo):
    s = json.dumps(corpo) if isinstance(corpo, dict) else str(corpo)
    return "signature" in s and "thinking" in s


def registra(nome, ok, dettaglio):
    esiti.append((nome, ok, dettaglio))
    print(f"  [{'PASS' if ok else 'FAIL'}] {nome}\n         {dettaglio}")


# ── T1/T2: F1, i thinking estranei ────────────────────────────────────────────
def t1_thinking_estranei():
    print("\nT1 — cronologia con thinking firmato da un altro provider -> Anthropic")
    print("     atteso 200: prima del fix erano 400 'Invalid signature in thinking block'")
    for nome, firma in (("GLM (24 hex)", FIRMA_GLM), ("MiniMax (64 hex)", FIRMA_MINIMAX)):
        st, corpo = chiama(PORTE["anthropic"], conversazione_con_thinking(firma))
        ok = st == 200
        extra = ""
        if not ok and errore_di_firma(corpo):
            extra = " <- il fix NON ha declassato il blocco"
        registra(f"T1 {nome}", ok, f"status={st}{extra}")


def t2_controprova_firma_anthropic():
    print("\nT2 — controprova: firma in stile Anthropic ma inventata")
    print("     atteso 400 di firma: prova che non stiamo declassando tutto alla cieca")
    st, corpo = chiama(PORTE["anthropic"],
                       conversazione_con_thinking(FIRMA_ANTHROPIC_FINTA))
    ok = st == 400 and errore_di_firma(corpo)
    registra("T2 firma Anthropic finta rifiutata", ok,
             f"status={st} errore_di_firma={errore_di_firma(corpo)}")


# ── T3: F2, il target di shrink GLM segue il modello ──────────────────────────
def _riempi(payload, byte_target):
    """Gonfia la conversazione con turni finti finche' supera byte_target."""
    i = 0
    while len(json.dumps(payload).encode()) < byte_target:
        payload["messages"].insert(-1, {"role": "user", "content": f"nota {i}: " + "dati " * 400})
        payload["messages"].insert(-1, {"role": "assistant", "content": f"ricevuto {i}"})
        i += 1
    return payload


def _righe_log_da(offset):
    with open(LOG, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", "replace")


def _offset_log():
    import os
    return os.path.getsize(LOG)


def t3_glm_non_comprime_piu():
    print("\nT3 — body da ~700 KB verso glm-5.2 (porta 8775)")
    print("     atteso: NESSUN 'GLM preventivo shrink' (target ora ~3,2 MB per glm-5.2)")
    payload = _riempi({"model": "claude-opus-5", "max_tokens": 64,
                       "messages": [{"role": "user", "content": "Rispondi solo: OK"}]},
                      700_000)
    off = _offset_log()
    st, corpo = chiama(PORTE["glm"], payload, timeout=240)
    time.sleep(1.5)
    coda = _righe_log_da(off)
    shrink = [r for r in coda.splitlines() if "GLM preventivo shrink" in r]
    ok = st == 200 and not shrink
    registra("T3 glm-5.2 non compresso a 700 KB", ok,
             f"status={st} shrink_nel_log={len(shrink)}"
             + (f" | {shrink[0][-90:]}" if shrink else ""))


MINIMAX_CTX_TOKEN = 204_800  # contesto di MiniMax-M2.7, da model_context_map


def t4_minimax_riceve_sotto_il_suo_limite():
    """Il body da 900 KB verso l'ACT MiniMax di mix-am-2 deve arrivargli COMPRESSO.

    La prima versione di questa prova cercava la riga 'backend-bottleneck shrink'
    e falliva pur essendo tutto a posto: il `ctx gate` comprime gia' prima (log
    'ctx: proactive rewrite 565287b < 901983b'), quindi al bottleneck-shrink non
    restava nulla da tagliare e non logga quando non taglia. Cercare quella riga
    misurava l'implementazione, non l'effetto. Qui si misura l'effetto: che
    MiniMax riceva un input dentro il suo contesto, comunque ci si arrivi.
    """
    print("\nT4 — body da ~900 KB verso l'ACT MiniMax di mix-am-2 (porta 8781)")
    print(f"     atteso: 200 e input_tokens sotto i {MINIMAX_CTX_TOKEN:,} di MiniMax-M2.7")
    payload = _riempi({"model": "claude-haiku-4-5-20251001", "max_tokens": 64,
                       "messages": [{"role": "user", "content": "Rispondi solo: OK"}]},
                      900_000)
    grezzi = len(json.dumps(payload).encode())
    off = _offset_log()
    st, corpo = chiama(PORTE["mix-am-2"], payload, timeout=240)
    time.sleep(1.5)
    coda = _righe_log_da(off)
    ridotto = [r for r in coda.splitlines()
               if ("proactive rewrite" in r or "backend-bottleneck shrink" in r)]
    u = (corpo or {}).get("usage", {}) if isinstance(corpo, dict) else {}
    tok_in = int(u.get("input_tokens", 0) or 0) + int(u.get("cache_read_input_tokens", 0) or 0)
    ok = st == 200 and bool(ridotto) and 0 < tok_in < MINIMAX_CTX_TOKEN
    registra("T4 mix-am-2: MiniMax riceve dentro il suo contesto", ok,
             f"status={st} inviati={grezzi:,}b compressioni={len(ridotto)} "
             f"input_tokens={tok_in:,} (limite {MINIMAX_CTX_TOKEN:,})")


# ── T5: la cache regge fra turni consecutivi ──────────────────────────────────
def t5_cache_fra_turni(porta_nome, giri=3):
    print(f"\nT5 — {giri} turni consecutivi su {porta_nome}: la cache deve crescere")
    print("     atteso: cache_read > 0 e in crescita, cache_creation bassa")
    prefisso = ("Sei un assistente. Contesto di riferimento: " + "regola operativa. " * 900)
    letture, creazioni = [], []
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": prefisso, "cache_control": {"type": "ephemeral"}}]}]
    for g in range(giri):
        payload = {"model": "claude-haiku-4-5-20251001", "max_tokens": 32,
                   "messages": msgs + [{"role": "user", "content": f"turno {g}: di' OK"}]}
        st, corpo = chiama(PORTE[porta_nome], payload, timeout=120)
        u = (corpo or {}).get("usage", {}) if isinstance(corpo, dict) else {}
        letture.append(int(u.get("cache_read_input_tokens", 0) or 0))
        creazioni.append(int(u.get("cache_creation_input_tokens", 0) or 0))
        time.sleep(1)
    ok = letture[-1] > 0 and letture[-1] >= letture[0]
    registra(f"T5 cache viva su {porta_nome}", ok,
             f"read={letture} creation={creazioni}")


# ── T6: orfani ────────────────────────────────────────────────────────────────
def t6_orfani():
    print("\nT6 — orfani: simboli top-level e metodi di classe")
    try:
        out = subprocess.run(["airouter-info", "orfani"], capture_output=True,
                             text=True, timeout=120).stdout
        m = re.search(r"mai referenziati:\s*(\d+)", out)
        n_top = int(m.group(1)) if m else -1
    except Exception as e:
        n_top = -1
        out = str(e)
    ok = n_top == 0
    registra("T6 zero simboli top-level orfani", ok, f"mai referenziati={n_top}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--giri", type=int, default=3)
    ap.add_argument("--salta-grossi", action="store_true",
                    help="salta T3/T4 (i due body da centinaia di KB)")
    a = ap.parse_args()

    print("=" * 78)
    print("STRESS TEST fix F1/F2/F3 — router vivo, porte di modalita', traffico sintetico")
    print("=" * 78)
    t1_thinking_estranei()
    t2_controprova_firma_anthropic()
    if not a.salta_grossi:
        t3_glm_non_comprime_piu()
        t4_minimax_riceve_sotto_il_suo_limite()
    t5_cache_fra_turni("anthropic", a.giri)
    t6_orfani()

    print("\n" + "=" * 78)
    passati = sum(1 for _, ok, _ in esiti if ok)
    print(f"RISULTATO: {passati}/{len(esiti)} prove superate")
    for nome, ok, det in esiti:
        if not ok:
            print(f"  FAIL {nome}: {det}")
    print("=" * 78)
    return 0 if passati == len(esiti) else 1


if __name__ == "__main__":
    sys.exit(main())
