"""Le modalita' con ACT su un provider si derivano dal routing, non si scrivono a mano (F3).

Il proxy teneva `{"minimax","mix-am","mix-gm"}` letterale per decidere se applicare
lo shrink preventivo verso il collo di bottiglia MiniMax. Quando sono nate mix-am-2
e mix-gm-2 — routing identico alle basi — il set non e' stato aggiornato, e la
modalita' con il 47% del traffico e' rimasta scoperta.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from role_routing import modes_with_act_provider, VALID_MODES, resolve_route

PROXY = Path(__file__).resolve().parents[2] / "src" / "ai-router-proxy.py"


def test_le_varianti_2_sono_incluse():
    minimax = modes_with_act_provider("minimax")
    assert {"minimax", "mix-am", "mix-am-2", "mix-gm", "mix-gm-2"} <= minimax


def test_non_include_chi_non_esegue_su_minimax():
    minimax = modes_with_act_provider("minimax")
    for m in ("anthropic", "glm", "qwen", "local", "mix-al", "mix-ag", "mix-ag-2"):
        assert m not in minimax, m


def test_coerente_con_resolve_route():
    """Controprova indipendente: per ogni modalita' dichiarata ACT-su-X, la rotta
    del modello esecutore deve davvero finire su X."""
    for provider in ("minimax", "glm", "anthropic", "local"):
        for mode in modes_with_act_provider(provider):
            got, _ = resolve_route(mode, "claude-haiku-4-5-20251001")
            assert got == provider, f"{mode}: atteso {provider}, ottenuto {got}"


def test_ogni_modalita_valida_ha_un_provider_act():
    coperte = set()
    for p in ("anthropic", "minimax", "glm", "qwen", "local"):
        coperte |= modes_with_act_provider(p)
    assert coperte == set(VALID_MODES)


def test_il_proxy_non_riscrive_il_set_a_mano():
    """Se qualcuno rimette l'elenco letterale, il buco si riapre in silenzio."""
    src = PROXY.read_text()
    assert "_MINIMAX_BACKEND_MODES = modes_with_act_provider(\"minimax\")" in src
    assert not re.search(r'_MINIMAX_BACKEND_MODES\s*=\s*\{', src)


def test_import_a_livello_di_modulo():
    """Dentro handle() il nome diventerebbe locale e il primo uso, piu' in alto,
    solleverebbe UnboundLocalError: e' gia' successo con get_safe_input_limit."""
    src = PROXY.read_text()
    riga_import = src.index("from role_routing import modes_with_act_provider")
    riga_uso = src.index("_MINIMAX_BACKEND_MODES = modes_with_act_provider")
    assert riga_import < riga_uso
    # l'import non deve stare rientrato (cioe' dentro una funzione)
    inizio = src.rfind("\n", 0, riga_import) + 1
    assert src[inizio:riga_import] == ""
