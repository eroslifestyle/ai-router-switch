#!/usr/bin/env python3
"""
AI Router Proxy - Modulo Resilienza (RESILIENZA 2026-06-30)

Gestisce in particolare il ciclo di vita OAuth subscription:
- Al boot: valida .credentials.json (presenza + scadenza)
- Self-test: POST reale a api.anthropic.com/v1/messages per verificare che il
  token OAuth sia VIVO (non solo strutturalmente valido)
- Se OAuth manca/scaduto: imposta stato DEGRADED — il proxy accetta solo
  health/router_health/resilience endpoint, mentre /v1/messages torna 503
  con istruzioni chiare per il re-login
- Watchdog che rilegge .credentials.json periodicamente: quando l'utente
  fa `claude login` nel terminale (comando nativo Claude Code), il file
  viene aggiornato e il proxy esce automaticamente da DEGRADED
- Watchdog interno (heartbeat) + crash dump su SIGTERM/KILL/ABRT

Dopo un reset leobox / reboot:
1. proxy riparte → boot_validate dice OAuth MISSING → va in DEGRADED
2. utente fa `claude login` nel terminale → credentials.json aggiornato
3. proxy (entro 30s) rilegge file → OAuth OK → esce da DEGRADED → operativo
"""
import os
import sys
import json
import time
import signal
import threading
import traceback

import paths

STATE_DIR = paths.state_dir()
STATE_FILE = STATE_DIR / "ai-router-resilience-state.json"
CRASH_DIR = STATE_DIR / "ai-router-crash-dumps"
CRED_FILE = paths.credentials_file()


class Resilience:
    """Resilience layer per l'AI Router Proxy.

    Stati operativi:
    - BOOTING: inizializzazione
    - OK: tutto funzionante
    - DEGRADED: OAuth Anthropic mancante/scaduto. Proxy accetta solo health
      endpoints; per /v1/messages torna 503 con guida al re-login
    - RECOVERING: appena usciti da degraded, in attesa di conferma upstream
    """

    STATE_BOOTING = "BOOTING"
    STATE_OK = "OK"
    STATE_DEGRADED = "DEGRADED"
    STATE_RECOVERING = "RECOVERING"

    # Quanti secondi tra un self-test OAuth periodico (rilegge .credentials.json
    # per rilevare login utente)
    SELF_TEST_PERIOD_S = 30
    # Quanti secondi di tolleranza prima del primo self-test OAuth completo
    INITIAL_GRACE_S = 5
    # Loop-probe: event loop fermo da più di N secondi = stall → stack dump
    LOOP_STALL_DUMP_S = 10
    # Minimo intervallo tra due stack dump di stall (anti-spam)
    LOOP_STALL_DUMP_COOLDOWN_S = 60

    def __init__(self, port: int, log_fn=None, get_pid=None):
        self.port = port
        self.log = log_fn or (lambda m: None)
        self.get_pid = get_pid or (lambda: os.getpid())
        self._boot_ts = time.time()
        self._state = self.STATE_BOOTING
        self._oauth_tok = ""  # cache del token letto
        self._last_oauth_check_ts = 0.0
        self._self_test_thread: threading.Thread | None = None
        self._wd_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop = None  # event loop del proxy (attach_loop)
        self._loop_beat_ts = 0.0  # ultimo beat eseguito DAL loop
        self._loop_stalled = False
        self._last_stall_dump_ts = 0.0
        self._faulthandler_file = None
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CRASH_DIR.mkdir(parents=True, exist_ok=True)

    # ── OAuth helpers ────────────────────────────────────────────────────
    def _read_oauth(self) -> tuple[str, dict]:
        """Ritorna (token_str, meta_dict). token vuoto se assente/invalido."""
        try:
            with open(CRED_FILE) as f:
                creds = json.load(f)
            oauth = creds.get("claudeAiOauth", {}) or {}
            tok = oauth.get("accessToken", "") or ""
            return tok, {
                "expiresAt": oauth.get("expiresAt", 0),
                "subscriptionType": oauth.get("subscriptionType", "?"),
                "hasRefresh": bool(oauth.get("refreshToken")),
            }
        except Exception as e:
            return "", {"error": type(e).__name__}

    def _oauth_expires_in_h(self, meta: dict) -> float | None:
        exp = meta.get("expiresAt", 0)
        if not exp:
            return None
        return (exp - time.time() * 1000) / 1000 / 3600

    def is_oauth_structurally_ok(self) -> bool:
        """OAuth subscription: token presente e non scaduto (strutturale)."""
        tok, meta = self._read_oauth()
        if not tok.startswith("sk-ant-oat"):
            return False
        h = self._oauth_expires_in_h(meta)
        if h is None or h < 0:
            return False
        return True

    def self_test_oauth(self, session=None, timeout_s: float = 8.0) -> tuple[bool, str]:
        """POST reale a Anthropic /v1/messages per verificare token OAuth VIVO.

        Ritorna (ok, message). ok=True se 2xx, altrimenti False + dettaglio.
        Usa una sessione aiohttp se passata (per evitare setup doppio); altrimenti
        ne crea una locale (più lento ma funziona anche da script standalone).
        """
        tok, meta = self._read_oauth()
        if not tok:
            return False, "oauth_token_missing"
        if not self.is_oauth_structurally_ok():
            h = self._oauth_expires_in_h(meta)
            return False, f"oauth_token_expired ({-h:.1f}h ago)" if h and h < 0 else "oauth_token_struct_invalid"

        import asyncio
        try:
            return asyncio.run(self._async_self_test(tok, session, timeout_s))
        except RuntimeError:
            # già in loop, creiamo task sincrono
            return asyncio.get_event_loop().run_until_complete(
                self._async_self_test(tok, session, timeout_s)
            )

    async def _async_self_test(self, tok: str, session=None, timeout_s: float = 8.0):
        from aiohttp import ClientSession, ClientTimeout
        sess = session
        owns = False
        if sess is None:
            sess = ClientSession(timeout=ClientTimeout(total=timeout_s))
            owns = True
        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "Authorization": f"Bearer {tok}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
                "content-type": "application/json",
            }
            body = {
                "model": "claude-haiku-4-5",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "."}],
            }
            async with sess.post(url, headers=headers, json=body) as r:
                if 200 <= r.status < 300:
                    return True, f"ok status={r.status}"
                txt = (await r.text())[:200]
                return False, f"status={r.status} body={txt}"
        except Exception as e:
            return False, f"exception={type(e).__name__}: {e}"
        finally:
            if owns:
                await sess.close()

    # ── Boot validation ─────────────────────────────────────────────────
    def boot_validate(self, run_self_test: bool = True) -> bool:
        """Validazione stato al boot. Vero se il proxy può partire in stato OK."""
        problems = []

        # 1) OAuth strutturale
        tok, meta = self._read_oauth()
        if not tok:
            problems.append("oauth_missing")
        else:
            h = self._oauth_expires_in_h(meta)
            if h is None:
                problems.append("oauth_no_exp")
            elif h < 0:
                problems.append(f"oauth_expired ({-h:.1f}h ago)")

        # 2) secrets.sh accessibile
        secrets_sh = paths.secrets_script()
        if not secrets_sh.exists():
            problems.append("secrets_sh_missing")
        # La chiave vive accanto a secrets.sh, che la definisce come "$DIR/.master.key"
        master_key = secrets_sh.parent / ".master.key"
        if not master_key.exists():
            problems.append("master_key_missing")

        # 3) Cache state pulita
        self._invalidate_stale()

        # 4) Determina stato iniziale
        oauth_ok = "oauth_" not in " ".join(problems) or (
            "oauth_expired" not in " ".join(problems)
            and "oauth_missing" not in " ".join(problems)
            and "oauth_no_exp" not in " ".join(problems)
        )

        if oauth_ok:
            self._state = self.STATE_OK
        else:
            self._state = self.STATE_DEGRADED
        self._oauth_tok = tok

        # 5) Scrivi state
        self._write_state({
            "boot_ts": self._boot_ts,
            "pid": self.get_pid(),
            "port": self.port,
            "state": self._state,
            "oauth_present": bool(tok),
            "oauth_subscription": meta.get("subscriptionType", "?"),
            "oauth_expires_in_h": self._oauth_expires_in_h(meta) if meta.get("expiresAt") else None,
            "oauth_has_refresh": meta.get("hasRefresh", False),
            "problems": problems,
        })

        if self._state == self.STATE_DEGRADED:
            self.log(f"resilience: BOOT in modalità DEGRADED — {problems}")
            self.log("resilience: per ripristinare: `claude login` (OAuth subscription via terminale)")
        else:
            self.log(f"resilience: BOOT OK (OAuth subscription={meta.get('subscriptionType', '?')}, expires={self._oauth_expires_in_h(meta):.1f}h)")

        # 6) Self-test OAuth se non siamo già in degraded per altri motivi
        if run_self_test and self._state == self.STATE_OK:
            ok, msg = self.self_test_oauth(timeout_s=10.0)
            if ok:
                self.log(f"resilience: self-test OAuth OK ({msg})")
            else:
                self.log(f"resilience: self-test OAuth FALLITO ({msg}) — passo a DEGRADED")
                self._state = self.STATE_DEGRADED
                problems.append(f"oauth_live_test_failed: {msg}")

        return self._state == self.STATE_OK

    def _invalidate_stale(self):
        for name in ("ai-router-chats.json.tmp", "ai-router-resilience-state.json.tmp"):
            stale = paths.config_home() / name
            if stale.exists():
                try:
                    stale.unlink()
                except Exception:
                    pass

    # ── Self-test periodico: auto-recovery da DEGRADED ──────────────────
    def start_periodic_self_test(self, session=None):
        """Thread che ogni SELF_TEST_PERIOD_S verifica OAuth.

        Se credentials.json è cambiato (es utente ha fatto `claude login`),
        self-test per confermare e esce da DEGRADED.

        NOTA: il parametro session è ignorato di proposito. Il thread daemon
        non può usare la session del main event loop (conflitto loop), quindi
        crea la propria sessione locale per il self-test.
        """
        if self._self_test_thread and self._self_test_thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(
            target=self._self_test_loop,
            name="resilience-self-test", daemon=True,
        )
        t.start()
        self._self_test_thread = t
        self.log("resilience: self-test periodico avviato (ogni %ds)" % self.SELF_TEST_PERIOD_S)

    def _self_test_loop(self):
        time.sleep(self.INITIAL_GRACE_S)
        while not self._stop.wait(self.SELF_TEST_PERIOD_S):
            try:
                self._tick_self_test()
            except Exception as e:
                self.log(f"resilience: self-test tick EXC {type(e).__name__}: {e}")

    def _tick_self_test(self):
        tok, meta = self._read_oauth()
        if tok == self._oauth_tok and self._state == self.STATE_OK:
            # nessuna novità, skip self-test live
            return

        # Token cambiato o mancante, oppure siamo in DEGRADED
        if not tok.startswith("sk-ant-oat"):
            if self._state != self.STATE_DEGRADED:
                self.log("resilience: OAuth rimosso → DEGRADED")
                self._state = self.STATE_DEGRADED
                self._write_state_now()
            return

        if not self.is_oauth_structurally_ok():
            if self._state != self.STATE_DEGRADED:
                self.log("resilience: OAuth scaduto → DEGRADED")
                self._state = self.STATE_DEGRADED
                self._write_state_now()
            return

        # Token presente e non scaduto: facciamo self-test live (sessione LOCALE)
        ok, msg = self.self_test_oauth(session=None, timeout_s=10.0)
        if ok:
            if self._state != self.STATE_OK:
                self.log(f"resilience: AUTO-RECOVERY OAuth OK ({msg}) → OK state")
                self._state = self.STATE_OK
                self._oauth_tok = tok
                self._write_state_now()
            else:
                self._oauth_tok = tok
        else:
            if self._state == self.STATE_OK:
                self.log(f"resilience: OAuth era OK ma live test FALLISCE ({msg}) → DEGRADED")
                self._state = self.STATE_DEGRADED
                self._write_state_now()
            else:
                # siamo già DEGRADED ma token "strutturalmente" ok e live test KO
                # probabilmente il server Anthropic è temporaneamente giù o c'è
                # errore di rete. Log ma rimani DEGRADED — riproviamo al prossimo tick.
                self.log(f"resilience: live test fallito ({msg}), prossimo tick tra %ds" % self.SELF_TEST_PERIOD_S)

    def _write_state_now(self):
        """Scrive state con i dati correnti."""
        tok, meta = self._read_oauth()
        self._write_state({
            "state": self._state,
            "ts": time.time(),
            "pid": self.get_pid(),
            "port": self.port,
            "oauth_present": bool(tok),
            "oauth_subscription": meta.get("subscriptionType", "?"),
            "oauth_expires_in_h": self._oauth_expires_in_h(meta),
            "oauth_has_refresh": meta.get("hasRefresh", False),
        })

    # ── Watchdog interno (heartbeat) ─────────────────────────────────────
    def start_heartbeat(self):
        if self._wd_thread and self._wd_thread.is_alive():
            return
        self._stop.clear()
        self._wd_thread = threading.Thread(
            target=self._heartbeat_loop, name="resilience-heartbeat", daemon=True,
        )
        self._wd_thread.start()

    def attach_loop(self, loop):
        """Registra l'event loop del proxy per la sonda anti-freeze.

        L'heartbeat su thread NON rileva un event loop bloccato (il thread
        continua a battere anche con il loop congelato — freeze 2026-07-21/22).
        La sonda schedula un beat sul loop via call_soon_threadsafe: se il
        beat non viene eseguito entro LOOP_STALL_DUMP_S il loop è in stall
        → faulthandler dumpa gli stack di TUTTI i thread (riga esatta del blocco).
        SIGUSR1 → dump on-demand degli stack in ogni momento.
        """
        import faulthandler
        self._loop = loop
        self._loop_beat_ts = time.time()
        try:
            self._faulthandler_file = open(CRASH_DIR / "sigusr1-stacks.txt", "a")
            faulthandler.register(signal.SIGUSR1, file=self._faulthandler_file,
                                  all_threads=True)
        except Exception as e:
            self.log(f"resilience: faulthandler SIGUSR1 non registrato: {e}")
        self.log("resilience: loop-probe anti-freeze attiva "
                 f"(stall>{self.LOOP_STALL_DUMP_S}s → stack dump, SIGUSR1 on-demand)")

    def _loop_beat(self):
        self._loop_beat_ts = time.time()
        if self._loop_stalled:
            self._loop_stalled = False
            self.log("resilience: event loop RIPRESO dopo stall")

    def _check_loop_stall(self):
        """Chiamato dal thread heartbeat: schedula beat + rileva stall."""
        if self._loop is None or self._loop.is_closed():
            return None
        try:
            self._loop.call_soon_threadsafe(self._loop_beat)
        except RuntimeError:
            return None  # loop in shutdown
        age = time.time() - self._loop_beat_ts
        if age > self.LOOP_STALL_DUMP_S:
            self._loop_stalled = True
            now = time.time()
            if now - self._last_stall_dump_ts > self.LOOP_STALL_DUMP_COOLDOWN_S:
                self._last_stall_dump_ts = now
                self._dump_loop_stall(age)
        return age

    def _dump_loop_stall(self, age: float):
        """Stack dump C-level di tutti i thread: funziona anche con GIL conteso."""
        import faulthandler
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = CRASH_DIR / f"loop-stall-{ts}.txt"
        try:
            with open(path, "w") as f:
                f.write(f"=== EVENT LOOP STALL rilevato da resilience-heartbeat ===\n"
                        f"iso={time.strftime('%Y-%m-%dT%H:%M:%S')} pid={self.get_pid()} "
                        f"beat_age={age:.1f}s uptime={int(time.time() - self._boot_ts)}s\n"
                        f"--- stack di tutti i thread (MainThread = loop bloccato) ---\n")
                f.flush()
                faulthandler.dump_traceback(file=f, all_threads=True)
            self.log(f"resilience: LOOP STALL {age:.1f}s — stack dump -> {path}")
        except Exception as e:
            self.log(f"resilience: loop-stall dump fallito: {e}")

    def _heartbeat_loop(self):
        while not self._stop.wait(2.0):
            try:
                beat_age = self._check_loop_stall()
                state = self._read_state()
                state["heartbeat_ts"] = time.time()
                state["heartbeat_uptime_s"] = int(time.time() - self._boot_ts)
                state["state"] = self._state  # sync
                if beat_age is not None:
                    state["loop_beat_age_ms"] = int(beat_age * 1000)
                    state["loop_stalled"] = self._loop_stalled
                self._write_state(state, atomic=True)
            except Exception:
                pass

    def stop(self):
        self._stop.set()

    # ── Crash dump ───────────────────────────────────────────────────────
    def dump_crash(self, reason: str, frames: list | None = None):
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = CRASH_DIR / f"crash-{ts}.json"
        try:
            payload = {
                "ts": time.time(),
                "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "pid": self.get_pid(),
                "port": self.port,
                "state_before": self._state,
                "reason": reason,
                "uptime_s": int(time.time() - self._boot_ts),
                "frames": frames or [],
                "traceback_tail": traceback.format_stack()[-30:],
            }
            path.write_text(json.dumps(payload, indent=2, default=str))
            self.log(f"resilience: crash dump saved -> {path}")
        except Exception:
            pass

    def install_signal_handlers(self):
        def handler(signum, frame):
            self.dump_crash(f"signal {signum}")
        for sig in (signal.SIGTERM, signal.SIGABRT, signal.SIGINT):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    # ── Status / helpers ─────────────────────────────────────────────────
    def state_is_ok(self) -> bool:
        return self._state == self.STATE_OK

    def state(self) -> str:
        return self._state

    def degraded_response(self) -> dict:
        """Payload JSON per 503 quando OAuth manca."""
        return {
            "type": "error",
            "error": {
                "type": "authentication_required",
                "message": (
                    "Anthropic OAuth subscription token mancante o scaduto. "
                    "Esegui `claude login` nel terminale per ri-autenticarti; "
                    "il proxy rileverà il nuovo token automaticamente entro ~30s."
                ),
                "code": "oauth_login_required",
                "remediation": {
                    "command": "claude login",
                    "expected_path": "~/.claude/.credentials.json",
                    "auto_recovery": True,
                    "expected_seconds": 30,
                },
                "state_file": str(STATE_FILE),
                "current_state": self._state,
            },
        }

    def get_status(self) -> dict:
        s = self._read_state()
        s["computed_state"] = self._state
        s["live_oauth_present"] = bool(self._oauth_tok)
        return s

    def _read_state(self) -> dict:
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
        return {}

    def _write_state(self, state: dict, atomic: bool = False):
        try:
            content = json.dumps(state, indent=2, default=str)
            if atomic:
                tmp = STATE_FILE.with_suffix(".tmp")
                tmp.write_text(content)
                tmp.replace(STATE_FILE)
            else:
                STATE_FILE.write_text(content)
        except Exception:
            pass
