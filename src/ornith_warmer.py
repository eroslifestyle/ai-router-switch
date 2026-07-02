#!/usr/bin/env python3
"""
ornith-warmer — daemon che mantiene ornith-35b sempre caldo in Ollama.

Logica:
1. All'avvio: invia keep-alive a ornith-35b (Ollama carica in VRAM).
2. Ogni WARM_INTERVAL secondi: ping leggero per non far scadere il KEEP_ALIVE.
3. Ogni CHECK_INTERVAL secondi: controlla quanta VRAM usano i modelli caricati.
   Se la VRAM è pressata (modelli generativi caricati) → non fa nulla (Ollama
   gestisce autonomamente lo swap out). Ma dopo IDLE_TIMEOUT senza modelli
   generativi in ps → ricarica ornith-35b.
4. Resiliente: riprova indefinitamente su errori Ollama (connessione rifiutata, timeout).

Modelli "generativi" (omnia/media): pattern configurabile via env ORNITH_GENERATIVE_PATTERN.
"""
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

OLLAMA_BASE = os.environ.get("AIROUTER_OLLAMA_BASE", "http://127.0.0.1:11434")
ORNITH_MODEL = os.environ.get("AIROUTER_ORNITH_MODEL", "ornith-35b")
WARM_INTERVAL = int(os.environ.get("ORNITH_WARM_INTERVAL", "240"))   # ping ogni 4 min
CHECK_INTERVAL = int(os.environ.get("ORNITH_CHECK_INTERVAL", "30"))  # check ps ogni 30s
IDLE_TIMEOUT = int(os.environ.get("ORNITH_IDLE_TIMEOUT", "120"))     # reload dopo 2 min idle
GENERATIVE_PATTERN = os.environ.get(
    "ORNITH_GENERATIVE_PATTERN",
    "flux,sdxl,stable-diffusion,tts,whisper,wav2vec,reel,omnia,fal"
)
LOG_FILE = os.path.expanduser("~/.claude/logs/ornith-warmer.log")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
_gen_patterns = [p.strip().lower() for p in GENERATIVE_PATTERN.split(",") if p.strip()]
_running = True
_last_generative_seen = 0.0


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _request(path: str, body: bytes | None = None, timeout: int = 10):
    url = OLLAMA_BASE + path
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ollama_ps() -> list[dict]:
    """Ritorna lista modelli caricati in Ollama (/api/ps)."""
    try:
        return _request("/api/ps").get("models", [])
    except Exception:
        return []


def ornith_is_loaded() -> bool:
    for m in ollama_ps():
        if ORNITH_MODEL in (m.get("name") or m.get("model") or ""):
            return True
    return False


def generative_is_loaded() -> bool:
    for m in ollama_ps():
        name = (m.get("name") or m.get("model") or "").lower()
        if any(p in name for p in _gen_patterns):
            return True
    return False


def warm_ornith():
    """Invia prompt leggero per caricare/mantenere ornith-35b in VRAM."""
    body = json.dumps({
        "model": ORNITH_MODEL,
        "prompt": "ping",
        "stream": False,
        "keep_alive": "-1",       # -1 = forever (non scarica mai per timeout)
        "options": {"num_predict": 1},
    }).encode()
    try:
        _request("/api/generate", body=body, timeout=120)
        log(f"warm OK: {ORNITH_MODEL} caricato (keep_alive=-1)")
        return True
    except Exception as e:
        log(f"warm FAIL: {e}")
        return False


def signal_handler(sig, frame):
    global _running
    log(f"SIGTERM/SIGINT ricevuto ({sig}), uscita pulita")
    _running = False


def main():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log(f"ornith-warmer avviato: model={ORNITH_MODEL} warm_interval={WARM_INTERVAL}s "
        f"check_interval={CHECK_INTERVAL}s idle_timeout={IDLE_TIMEOUT}s")

    # Boot: warm immediato
    for attempt in range(1, 6):
        if warm_ornith():
            break
        log(f"boot attempt {attempt}/5 fallito, retry in 15s")
        time.sleep(15)
    else:
        log("WARN: boot warm fallito dopo 5 tentativi, continuo comunque")

    last_warm = time.time()
    global _last_generative_seen

    while _running:
        time.sleep(CHECK_INTERVAL)
        if not _running:
            break

        now = time.time()
        gen_active = generative_is_loaded()

        if gen_active:
            _last_generative_seen = now
            log(f"generativo attivo → ornith in standby (scarico OK se Ollama ha bisogno)")
        else:
            # Nessun modello generativo: verifica ornith caldo
            idle_since = now - _last_generative_seen
            ornith_ok = ornith_is_loaded()

            if not ornith_ok:
                if _last_generative_seen == 0.0 or idle_since >= IDLE_TIMEOUT:
                    log(f"ornith non in memoria (idle {idle_since:.0f}s) → reload")
                    warm_ornith()
                    last_warm = now
                else:
                    log(f"ornith non in memoria ma idle solo {idle_since:.0f}s < {IDLE_TIMEOUT}s → aspetto")
            else:
                # Ornith caricato: ping periodico per mantenere keep_alive
                if now - last_warm >= WARM_INTERVAL:
                    log(f"ping periodico ({WARM_INTERVAL}s)")
                    warm_ornith()
                    last_warm = now

    log("ornith-warmer fermato.")


if __name__ == "__main__":
    main()
