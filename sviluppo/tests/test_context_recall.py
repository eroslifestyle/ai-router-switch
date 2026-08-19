"""Il richiamo per pertinenza: i vecchi messaggi utili rientrano, gli inutili no.

La finestra scorrevole tiene i messaggi PIU' RECENTI, ma "recente" e "utile" non
coincidono: la decisione presa duecento messaggi fa e' spesso quella che serve
adesso. Qui si verifica che venga ripescata, che sia etichettata come estratto
(non spacciata per conversazione recente) e che l'archivio su disco copra il caso
in cui il client ha gia' buttato la storia con /compact.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import context_recall  # noqa: E402
from context_recall import BLOCK_HEADER, _bm25_rank, build_recall_block, query_text  # noqa: E402

DECISIONE = ("La decisione presa: il timeout del backend Vulkan va portato a 240 secondi "
             "perche' il prefill di quarantamila token su GPU integrata supera i tre minuti.")
DOMANDA = "Ricordami che timeout avevamo deciso per il backend Vulkan e perche'."
RUMORE = "nota di contorno senza alcuna informazione rilevante ripetuta a lungo " * 4


def _msg(role, text):
    return {"role": role, "content": text}


def _archivio_isolato(monkeypatch_dir: Path):
    context_recall.ARCHIVE_DIR = monkeypatch_dir


# ── selezione ───────────────────────────────────────────────────────────────

def test_bm25_preferisce_il_documento_pertinente():
    docs = [RUMORE, DECISIONE, RUMORE + " altro"]
    assert _bm25_rank(DOMANDA, docs, 1) == [1]


def test_bm25_su_query_vuota_non_inventa_risultati():
    assert _bm25_rank("", [DECISIONE], 3) == []
    assert _bm25_rank(DOMANDA, [], 3) == []


def test_query_e_l_ultimo_messaggio_utente_con_testo():
    msgs = [_msg("user", "vecchia"), _msg("assistant", "risposta"), _msg("user", DOMANDA)]
    assert query_text(msgs) == DOMANDA


def test_il_blocco_richiama_la_decisione_e_la_etichetta():
    dropped = [_msg("user", DECISIONE)] + [_msg("assistant", RUMORE) for _ in range(20)]
    msgs = dropped + [_msg("user", DOMANDA)]

    with tempfile.TemporaryDirectory() as tmp:
        _archivio_isolato(Path(tmp))
        blocco = build_recall_block("sid:t1", msgs, dropped, budget_chars=8_000)

    assert "240 secondi" in blocco
    assert blocco.startswith(BLOCK_HEADER), "va dichiarato come estratto, non come turno recente"


def test_senza_nulla_di_pertinente_il_blocco_resta_vuoto():
    dropped = [_msg("assistant", RUMORE) for _ in range(10)]
    msgs = dropped + [_msg("user", "zqxjkv wpbrfm nessuna corrispondenza possibile")]
    with tempfile.TemporaryDirectory() as tmp:
        _archivio_isolato(Path(tmp))
        assert build_recall_block("sid:t2", msgs, dropped, budget_chars=8_000) == ""


def test_il_budget_e_rispettato():
    dropped = [_msg("user", DECISIONE + f" variante {i}") for i in range(10)]
    msgs = dropped + [_msg("user", DOMANDA)]
    with tempfile.TemporaryDirectory() as tmp:
        _archivio_isolato(Path(tmp))
        blocco = build_recall_block("sid:t3", msgs, dropped, budget_chars=600)
    assert len(blocco) <= 600 + len(BLOCK_HEADER), len(blocco)


# ── archivio: il caso /compact ──────────────────────────────────────────────

def test_dopo_compact_la_decisione_arriva_dall_archivio():
    """Il client ha buttato la storia: nel corpo non c'e' piu' nulla da scartare."""
    dropped = [_msg("user", DECISIONE)] + [_msg("assistant", RUMORE) for _ in range(5)]
    with tempfile.TemporaryDirectory() as tmp:
        _archivio_isolato(Path(tmp))
        context_recall.archive("sid:compact", dropped)

        # Turno successivo al /compact: solo la nuova domanda, zero scartati.
        blocco = build_recall_block("sid:compact", [_msg("user", DOMANDA)], [], budget_chars=8_000)

    assert "240 secondi" in blocco, "l'archivio e' l'unica copia rimasta"


def test_l_archivio_non_duplica_lo_stesso_messaggio():
    dropped = [_msg("user", DECISIONE)]
    with tempfile.TemporaryDirectory() as tmp:
        _archivio_isolato(Path(tmp))
        for _ in range(5):
            context_recall.archive("sid:dup", dropped)
        archivio = next(p for p in Path(tmp).iterdir())
        righe = [r for r in archivio.read_text(encoding="utf-8").splitlines() if r.strip()]
    assert len(righe) == 1, righe


def test_i_messaggi_troppo_corti_non_finiscono_in_archivio():
    with tempfile.TemporaryDirectory() as tmp:
        _archivio_isolato(Path(tmp))
        context_recall.archive("sid:corti", [_msg("user", "ok")])
        assert not any(Path(tmp).iterdir()), "un 'ok' non e' conoscenza da conservare"


def test_il_richiamo_si_puo_spegnere():
    dropped = [_msg("user", DECISIONE)]
    msgs = dropped + [_msg("user", DOMANDA)]
    originale = context_recall.RECALL_ENABLED
    try:
        context_recall.RECALL_ENABLED = False
        with tempfile.TemporaryDirectory() as tmp:
            _archivio_isolato(Path(tmp))
            assert build_recall_block("sid:off", msgs, dropped, budget_chars=8_000) == ""
    finally:
        context_recall.RECALL_ENABLED = originale


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
