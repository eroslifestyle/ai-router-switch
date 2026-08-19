#!/usr/bin/env python3
"""Stress end-to-end dei fix GLM/local del 18-19 agosto 2026, su istanza ISOLATA.

Perche' esiste: i test unitari girano su fake, e i difetti chiusi in questi due
giorni erano tutti invisibili ai fake — un predicato falso in produzione, un
override letto come default, un modello che risponde 200 fingendo di vedere
un'immagine. Qui si mandano richieste VERE a z.ai attraverso un proxy avviato
apposta sulle porte 8795-8799, senza toccare il servizio live su :8787.

Uso:  python3 sviluppo/tests/stress_fix_20260819.py [--carico N]

Ogni prova nomina il fix che difende. Uscita 0 se tutte passano.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROXY = os.path.join(ROOT, "src", "ai-router-proxy.py")
LOG_ISTANZA = "/tmp/stress-20260819-proxy.log"      # stdout/stderr del processo
# Log applicativo DEDICATO: senza AIROUTER_LOGS_DIR l'istanza scriverebbe nel log
# di produzione, e le asserzioni sulle righe ("GLM vision:", "clamp max_tokens")
# leggerebbero anche il traffico vero — o non leggerebbero nulla, come e' successo
# alla prima esecuzione: log() scrive su file, non sullo stdout catturato qui.
DIR_LOG = "/tmp/stress-20260819-logs"
LOG_ROUTER = os.path.join(DIR_LOG, "ai-router.log")
PORTE = {"glm": 8795, "mix-gm": 8796, "mix-ag": 8797, "local": 8798}
PORTA_DIN = 8799

esiti = []


def registra(nome, ok, dettaglio=""):
    esiti.append((nome, ok, dettaglio))
    print(f"  {'PASS' if ok else 'FAIL'}  {nome}" + (f"  [{dettaglio}]" if dettaglio else ""))


def chiama(porta, corpo, timeout=120, stream=False):
    """POST /v1/messages sull'istanza isolata. Ritorna (status, testo)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{porta}/v1/messages",
        data=json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _decodifica(r.read(), r.headers.get("Content-Encoding", ""))
    except urllib.error.HTTPError as e:
        return e.code, _decodifica(e.read(), e.headers.get("Content-Encoding", ""))
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _decodifica(grezzo, codifica):
    """Il proxy inoltra la codifica dell'upstream: senza questo, i byte gzip
    passavano per testo e ogni regex sul modello rispondeva '?'."""
    codifica = (codifica or "").lower()
    if "gzip" in codifica or grezzo[:2] == b"\x1f\x8b":
        import gzip as _gz
        try:
            grezzo = _gz.decompress(grezzo)
        except Exception:
            pass
    elif "deflate" in codifica:
        import zlib as _zl
        try:
            grezzo = _zl.decompress(grezzo)
        except Exception:
            pass
    return grezzo.decode("utf-8", errors="replace")


def modello_di(testo):
    m = re.search(r'"model"\s*:\s*"([^"]+)"', testo)
    return m.group(1) if m else "?"


def png_1x1():
    import base64, struct, zlib
    def blocco(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    png = (b"\x89PNG\r\n\x1a\n"
           + blocco(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + blocco(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
           + blocco(b"IEND", b""))
    return base64.b64encode(png).decode()


def log_istanza():
    """Log applicativo del router + stdout del processo, concatenati."""
    testo = ""
    for percorso in (LOG_ROUTER, LOG_ISTANZA):
        try:
            with open(percorso, errors="replace") as f:
                testo += f.read()
        except Exception:
            continue
    return testo


# ── Avvio istanza isolata ────────────────────────────────────────────────────

def avvia():
    env = dict(os.environ)
    env["AIROUTER_PORT"] = str(PORTA_DIN)
    env["AIROUTER_PORT_MODE_JSON"] = json.dumps({str(p): m for m, p in PORTE.items()})
    env["PYTHONPATH"] = f"{ROOT}:{os.path.join(ROOT, 'src')}"
    os.makedirs(DIR_LOG, exist_ok=True)
    for vecchio in (LOG_ROUTER,):
        try:
            os.remove(vecchio)
        except FileNotFoundError:
            pass
    env["AIROUTER_LOGS_DIR"] = DIR_LOG
    # Il catalogo diagnostico ha un override PROPRIO: senza, l'istanza isolata
    # scrive gli eventi in logs/ del repo insieme a quelli veri, e le prove qui
    # sotto leggerebbero il file sbagliato.
    env["AIROUTER_CATALOG_PATH"] = os.path.join(DIR_LOG, "catalogo")
    logf = open(LOG_ISTANZA, "w")
    proc = subprocess.Popen([sys.executable, PROXY], stdout=logf, stderr=subprocess.STDOUT, env=env)
    for _ in range(30):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORTA_DIN}/health", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            continue
    proc.kill()
    raise SystemExit(f"istanza isolata non partita — log:\n{log_istanza()[-2000:]}")


# ── Le prove, una per fix ────────────────────────────────────────────────────

def t1_ruolo(porta):
    """6d458b6: THINK e ACT della catena GLM dopo la rinomina a 5.3."""
    s, t = chiama(porta, {"model": "claude-opus-5", "max_tokens": 24,
                          "messages": [{"role": "user", "content": "di solo: ok"}]})
    registra("T1a THINK claude-opus-5 -> glm-5.3", s == 200 and modello_di(t) == "glm-5.3",
             f"status={s} model={modello_di(t)}")
    s, t = chiama(porta, {"model": "claude-haiku-4-5-20251001", "max_tokens": 24,
                          "messages": [{"role": "user", "content": "di solo: ok"}]})
    registra("T1b ACT claude-haiku -> glm-4.7", s == 200 and modello_di(t) == "glm-4.7",
             f"status={s} model={modello_di(t)}")


def t2_modello_richiesto(porta):
    """74672c0: override=None non deve piu' significare 'usa il MID'."""
    casi = [("glm-5.3", "glm-5.3"), ("GLM-5.3", "glm-5.3"), ("glm-4.7", "glm-4.7")]
    for chiesto, atteso in casi:
        s, t = chiama(porta, {"model": chiesto, "max_tokens": 24,
                              "messages": [{"role": "user", "content": "di solo: ok"}]})
        registra(f"T2 modello richiesto {chiesto} -> {atteso}",
                 s == 200 and modello_di(t) == atteso, f"status={s} model={modello_di(t)}")
    # Un nome che non e' un modello GLM deve ricadere sul default di modalita'.
    s, t = chiama(porta, {"model": "glm-inesistente-9000", "max_tokens": 24,
                          "messages": [{"role": "user", "content": "di solo: ok"}]})
    registra("T2 modello ignoto -> default MID", s == 200 and modello_di(t) == "glm-4.7",
             f"status={s} model={modello_di(t)}")


def t3_max_tokens(porta):
    """af1062f: 128K di output non vanno piu' tagliati a 32.768."""
    prima = log_istanza().count("clamp max_tokens")
    s, t = chiama(porta, {"model": "claude-opus-5", "max_tokens": 131072,
                          "messages": [{"role": "user", "content": "di solo: ok"}]})
    dopo = log_istanza().count("clamp max_tokens")
    registra("T3 max_tokens=131072 accettato senza clamp",
             s == 200 and dopo == prima, f"status={s} clamp_nuovi={dopo - prima}")
    # Il tetto del modello di visione resta 32.768: li' il clamp DEVE scattare.
    prima = log_istanza().count("clamp max_tokens")
    s, t = chiama(porta, {"model": "glm-4.6V", "max_tokens": 131072,
                          "messages": [{"role": "user", "content": "di solo: ok"}]})
    dopo = log_istanza().count("clamp max_tokens")
    registra("T3 vision: max_tokens oltre 32.768 viene abbassato",
             dopo > prima, f"status={s} clamp_nuovi={dopo - prima}")


def t4_immagini(porta):
    """22c29cc: le immagini non vanno a un modello text-only."""
    corpo = {"model": "claude-opus-5", "max_tokens": 64, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_1x1()}},
        {"type": "text", "text": "che colore? una parola"}]}]}
    s, t = chiama(porta, corpo)
    modello = modello_di(t).lower()
    registra("T4 immagine dirottata sul modello che vede",
             s == 200 and modello.startswith("glm-4.6v"), f"status={s} model={modello}")
    registra("T4 il dirottamento e' tracciato a log",
             "GLM vision:" in log_istanza(), "cerca 'GLM vision:'")
    # Controprova: senza immagini si resta sul modello di ruolo.
    s, t = chiama(porta, {"model": "claude-opus-5", "max_tokens": 24,
                          "messages": [{"role": "user", "content": "di solo: ok"}]})
    registra("T4 controprova: solo testo resta su glm-5.3", modello_di(t) == "glm-5.3",
             f"model={modello_di(t)}")


def t5_streaming(porta):
    """Il peek NON deve bufferizzare: primo byte presto, sequenza SSE integra."""
    corpo = json.dumps({"model": "claude-opus-5", "max_tokens": 64, "stream": True,
                        "messages": [{"role": "user", "content": "conta: uno due tre"}]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{porta}/v1/messages", data=corpo,
                                 headers={"Content-Type": "application/json",
                                          "anthropic-version": "2023-06-01"})
    inizio = time.time()
    primo_byte = None
    visto = b""
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            while True:
                pezzo = r.read(1)
                if not pezzo:
                    break
                if primo_byte is None:
                    primo_byte = time.time() - inizio
                visto += pezzo
                if len(visto) > 4096 and b"content_block" in visto:
                    break
            resto = r.read(4096)
            visto += resto or b""
    except Exception as e:
        registra("T5 streaming", False, f"{type(e).__name__}: {e}")
        return
    testo = _decodifica(visto, "")
    registra("T5a TTFB entro 30s (niente buffering dell'intero stream)",
             primo_byte is not None and primo_byte < 30, f"ttfb={primo_byte:.2f}s" if primo_byte else "nessun byte")
    registra("T5b la sequenza SSE inizia da message_start",
             testo.lstrip().startswith("event: message_start") or "message_start" in testo[:200],
             testo[:60].replace("\n", "\\n"))
    registra("T5c i byte sbirciati non sono duplicati",
             testo.count("event: message_start") == 1,
             f"message_start x{testo.count('event: message_start')}")
    registra("T5d arriva contenuto vero", "content_block" in testo, "")


def t6_altre_modalita():
    """mix-gm e mix-ag: la catena resta quella della modalita'."""
    s, t = chiama(PORTE["mix-gm"], {"model": "claude-opus-5", "max_tokens": 24,
                                    "messages": [{"role": "user", "content": "di solo: ok"}]})
    registra("T6a mix-gm THINK -> glm-5.3", s == 200 and modello_di(t) == "glm-5.3",
             f"status={s} model={modello_di(t)}")
    s, t = chiama(PORTE["mix-ag"], {"model": "claude-haiku-4-5-20251001", "max_tokens": 24,
                                    "messages": [{"role": "user", "content": "di solo: ok"}]})
    registra("T6b mix-ag ACT -> glm-4.7", s == 200 and modello_di(t) == "glm-4.7",
             f"status={s} model={modello_di(t)}")


def t7_carico(porta, n):
    """Concorrenza: il limiter e il peek non devono incepparsi sotto carico."""
    def una(i):
        return chiama(porta, {"model": "claude-haiku-4-5-20251001", "max_tokens": 16,
                              "messages": [{"role": "user", "content": f"di solo: {i}"}]})
    inizio = time.time()
    with ThreadPoolExecutor(max_workers=n) as ex:
        risultati = list(ex.map(una, range(n)))
    durata = time.time() - inizio
    ok = sum(1 for s, _ in risultati if s == 200)
    rate = sum(1 for s, t in risultati if s == 429 or "rate_limit" in t)
    registra(f"T7 {n} richieste concorrenti senza errori del router",
             ok + rate == n, f"200={ok} rate_limit={rate} durata={durata:.1f}s")


def t9_local_isolamento_tool():
    """9e354c8 + isolamento: al modello locale non arrivano tool di altri provider.

    Il backend locale accetta max_tokens fino a 200.000 (misurato il 2026-08-19
    contro LiteLLM :4000: 200 e stop_reason=end_turn), quindi qui NON serve un
    clamp come su glm/qwen: sarebbe un numero inventato.
    """
    corpo = {"model": "claude-opus-5", "max_tokens": 24,
             "messages": [{"role": "user", "content": "di solo: ok"}],
             "tools": [{"name": "web_search", "type": "web_search_20250305"},
                       {"name": "mcp__zai__web_search_prime", "input_schema": {"type": "object"}},
                       {"name": "Bash", "input_schema": {"type": "object"}}]}
    s, t = chiama(PORTE["local"], corpo, timeout=180)
    registra("T9a mode local risponde con tool stranieri nel body", s == 200, f"status={s} {t[:80]}")
    # AIROUTER_CATALOG_PATH punta a un FILE, non a una directory: cercare
    # debug-events.jsonl dentro quel percorso non trovava nulla, e la prova
    # falliva mentre il fix funzionava.
    eventi = ""
    try:
        with open(os.path.join(DIR_LOG, "catalogo"), errors="replace") as f:
            eventi = f.read()
    except Exception:
        pass
    strip_local = [r for r in eventi.splitlines()
                   if "tool_isolation_strip" in r and '"local"' in r]
    registra("T9b i tool stranieri sono stati tolti per il backend locale",
             bool(strip_local), strip_local[0][:130] if strip_local else "nessun evento tool_isolation_strip/local")
    # La riga di forward_local deve nominare il modello SERVITO. Quella del proxy
    # ("tunnel local: model=claude-opus-5") nomina il richiesto ed e' corretta cosi':
    # dice che cosa ha chiesto il client, non chi ha risposto.
    righe_fwd = [r for r in log_istanza().splitlines() if "forward_local" in r]
    registra("T9c la diagnostica local nomina il modello servito",
             bool(righe_fwd) and all("code-max" in r or "code-fast" in r for r in righe_fwd)
             and not any("claude-" in r for r in righe_fwd),
             righe_fwd[0][-70:] if righe_fwd else "nessuna riga forward_local")


def t8_stabilita(proc):
    """Il processo non deve essere morto durante lo stress."""
    registra("T8 istanza viva a fine stress", proc.poll() is None,
             f"returncode={proc.returncode}")
    brutte = [r for r in log_istanza().splitlines()
              if "Traceback" in r or "UnboundLocalError" in r or "AttributeError" in r]
    registra("T8 nessuna eccezione non gestita nel log", not brutte,
             brutte[0][:120] if brutte else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--carico", type=int, default=8)
    args = ap.parse_args()

    print("═══ STRESS fix 18-19 agosto — istanza isolata su 8795-8799 ═══")
    proc = avvia()
    print(f"  istanza pid={proc.pid}, log {LOG_ISTANZA}")
    try:
        porta = PORTE["glm"]
        t1_ruolo(porta)
        t2_modello_richiesto(porta)
        t3_max_tokens(porta)
        t4_immagini(porta)
        t5_streaming(porta)
        t6_altre_modalita()
        t7_carico(porta, args.carico)
        t9_local_isolamento_tool()
        t8_stabilita(proc)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    falliti = [n for n, ok, _ in esiti if not ok]
    print(f"\n═══ {len(esiti) - len(falliti)}/{len(esiti)} PASS ═══")
    if falliti:
        print("falliti: " + ", ".join(falliti))
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
