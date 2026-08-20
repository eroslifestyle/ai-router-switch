#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

TEST_DIR = tempfile.mkdtemp(prefix="sidecar_test_")
os.environ["AIROUTER_LOGS_DIR"] = TEST_DIR
for key in list(sys.modules.keys()):
    if "router" in key or "paths" in key:
        del sys.modules[key]
from router_constants import USAGE_SIDECAR

def _entry(idx):
    return {
        "ts": time.time(), "mode": "mix-am", "final": "minimax",
        "provider": "minimax", "input_tokens": 5000+idx, "output_tokens": 200+idx,
        "cache_read": 3000+idx, "cache_creation": 1000+idx, "body_bytes": 10000+idx,
        "tool_use_blocks": 5, "text_blocks": 3, "stop_reason": "end_turn",
        "task_class": "coding", "tools_bytes": 8000, "tools_mcp_bytes": 4000,
        "extra_padding": "x" * 1800}

def test_la_scrittura_e_atomica_con_scrittori_concorrenti():
    tmp = Path(tempfile.mktemp(suffix=".jsonl"))
    try:
        n_proc, n_righe, totali_attesi = 8, 30, 240
        pids = []
        for p in range(n_proc):
            pid = os.fork()
            if pid == 0:
                fd = os.open(str(tmp), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                for i in range(n_righe):
                    e = _entry(p * n_righe + i)
                    riga_bytes = (json.dumps(e) + "\n").encode("utf-8")
                    os.write(fd, riga_bytes)
                os.close(fd); os._exit(0)
            else:
                pids.append(pid)
        for pid in pids:
            os.waitpid(pid, 0)
        with open(tmp, "rb") as f:
            contenuto = f.read()
        righe = [l for l in contenuto.splitlines(keepends=True) if l.strip()]
        errori = []
        for i, riga in enumerate(righe):
            try:
                json.loads(riga.decode("utf-8"))
            except Exception as e:
                errori.append("riga " + str(i) + ": " + repr(e))
        assert not errori, str(len(errori)) + " spezzate: " + "\n".join(errori[:5])
        assert len(righe) == totali_attesi
    finally:
        tmp.unlink(missing_ok=True)

def _mock_args(giorni=None, ore=None, mode=None):
    class A: pass
    a = A(); a.giorni = giorni; a.ore = ore; a.mode = mode; return a

def test_una_riga_corrotta_non_falsa_i_conteggi():
    tmp = Path(tempfile.mktemp(suffix=".jsonl"))
    NL = "\n".encode()
    corrupt1 = ("###CORRUPT### non-JSON\n").encode("utf-8")
    corrupt2 = ("{ chiave senza virgolette }\n").encode("utf-8")
    corrupt3 = ("{unclosed:\n").encode("utf-8")
    corrupt4 = ("trailing garbage: 123}\n").encode("utf-8")
    corrupt5 = ("\n").encode("utf-8")
    try:
        with open(tmp, "wb") as f:
            for i in range(20):
                v = {"ts": 1000+i, "mode": "mix-am", "final": "minimax"}
                f.write(json.dumps(v).encode() + NL)
            f.write(corrupt1)
            f.write(corrupt2)
            f.write(corrupt3)
            f.write(corrupt4)
            f.write(corrupt5)
        import sviluppo.tools.airouter_info as ai
        orig = ai.SIDECAR; ai.SIDECAR = tmp
        try:
            args = _mock_args(giorni=None, mode=None)
            righe, scartate, _ = ai._leggi_sidecar(args)
            assert len(righe) == 20, "20 validi attesi, " + str(len(righe)) + " letti"
            assert scartate == 5, "5 scartati attesi, " + str(scartate) + " segnalati"
        finally:
            ai.SIDECAR = orig
    finally:
        tmp.unlink(missing_ok=True)

def test_legge_anche_le_generazioni_ruotate():
    import sviluppo.tools.airouter_info as ai
    tmpdir = Path(tempfile.mkdtemp(prefix="sidecar_test_"))
    try:
        with open(tmpdir / "router-usage.jsonl.1", "w") as f:
            for i in range(5):
                e = {"ts": time.time() - 86400 * 5 + i, "mode": "mix-am", "final": "minimax",
                     "input_tokens": 100, "output_tokens": 50}
                f.write(json.dumps(e) + "\n")
        with open(tmpdir / "router-usage.jsonl", "w") as f:
            for i in range(3):
                e = {"ts": time.time() - 3600 + i, "mode": "mix-am", "final": "minimax",
                     "input_tokens": 200, "output_tokens": 80}
                f.write(json.dumps(e) + "\n")
        orig = ai.SIDECAR
        ai.SIDECAR = tmpdir / "router-usage.jsonl"
        try:
            class A7: giorni = 7; ore = None; mode = None
            class A1: giorni = 1; ore = None; mode = None
            righe7, _, info7 = ai._leggi_sidecar(A7())
            assert len(righe7) == 8, f"8 entry totali attese, {len(righe7)} lette"
            assert len(info7) >= 2, f"almeno 2 file devono essere letti per 7 giorni: {list(info7.keys())}"
            righe1, _, info1 = ai._leggi_sidecar(A1())
            assert len(righe1) == 3, f"3 entry di oggi attese, {len(righe1)} lette"
            assert "router-usage.jsonl.1" not in info1, f".1 non deve essere aperto per 1 giorno"
        finally:
            ai.SIDECAR = orig
    finally:
        import shutil; shutil.rmtree(tmpdir, ignore_errors=True)

def test_il_comando_tool_separa_costo_reale_da_ingenuo():
    import io, contextlib
    import sviluppo.tools.airouter_info as ai
    tmpdir = Path(tempfile.mkdtemp(prefix="sidecar_test_"))
    tmp = tmpdir / "router-usage.jsonl"
    # 6 richieste: 4 con cache_read>0 (cachate), 2 con cache_read=0 (fresche)
    # tools_bytes=8000 -> tok=2000 per richiesta
    for i in range(6):
        cachata = 1 if i < 4 else 0
        e = {
            "ts": time.time() - i * 3600,
            "mode": "mix-am", "final": "minimax",
            "input_tokens": 5000, "output_tokens": 200,
            "cache_read": 3000 if cachata else 0,
            "cache_creation": 1000,
            "tools_bytes": 8000,
            "tools_mcp_bytes": 4000,
            "tools_mcp_servers": {"github": 4000},
        }
        with open(tmp, "a") as f:
            f.write(json.dumps(e) + "\n")

    orig = ai.SIDECAR
    ai.SIDECAR = tmp
    try:
        class A1: giorni = 1; ore = None; mode = None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ai.cmd_tool(A1())
        out = buf.getvalue()

        # Costo ingenuo: 6 richieste × 2000 tok = 12000
        assert "12k" in out, f"ingenuo deve essere ~12k: {out}"
        # Costo reale: 2 richieste × 2000 tok = 4000
        assert "4k" in out, f"reale deve essere ~4k: {out}"
        # Il risparmiato deve essere > 0 (le cachate hanno ridotto il costo)
        assert "risparmiato" in out.lower(), f"deve mostrare risparmiato: {out}"
        # Breakdown server deve esserci
        assert "github" in out.lower(), f"deve mostrare il server MCP: {out}"
    finally:
        ai.SIDECAR = orig
        import shutil; shutil.rmtree(tmpdir, ignore_errors=True)


def test_avvisa_se_la_finestra_supera_i_dati_disponibili():
    import io, contextlib
    import sviluppo.tools.airouter_info as ai
    tmpdir = Path(tempfile.mkdtemp(prefix="sidecar_test_"))
    try:
        with open(tmpdir / "router-usage.jsonl", "w") as f:
            for i in range(3):
                e = {"ts": time.time() - 86400 * 2 + i, "mode": "mix-am", "final": "minimax",
                     "input_tokens": 200, "output_tokens": 80}
                f.write(json.dumps(e) + "\n")
        orig = ai.SIDECAR
        ai.SIDECAR = tmpdir / "router-usage.jsonl"
        try:
            class A30: giorni = 30; ore = None; mode = None
            _, _, info = ai._leggi_sidecar(A30())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ai._avvisa_se_finestra_supera_dati(A30(), info)
            output = buf.getvalue()
            assert "⚠" in output, f"Avviso atteso, output: {output}"
            assert "30" in output or "giorni" in output, f"Deve citare i 30 giorni: {output}"
        finally:
            ai.SIDECAR = orig
    finally:
        import shutil; shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    ok = True
    tests = [
        ("scrittura atomica", test_la_scrittura_e_atomica_con_scrittori_concorrenti),
        ("lettura tollerante", test_una_riga_corrotta_non_falsa_i_conteggi),
        ("legge generazioni ruotate", test_legge_anche_le_generazioni_ruotate),
        ("avvisa se finestra supera dati", test_avvisa_se_la_finestra_supera_i_dati_disponibili),
    ]
    for nome, fn in tests:
        r = subprocess.run([sys.executable, "-c",
            "from sviluppo.tests.test_sidecar_integrita import " + fn.__name__ + ";" + fn.__name__ + "()"],
            capture_output=True, text=True, cwd="/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch")
        if r.returncode:
            print("FAIL " + nome + ": " + r.stderr.strip().splitlines()[-1])
            ok = False
        else:
            print("OK   " + nome)
    sys.exit(0 if ok else 1)
