"""Il target dello shrink GLM segue il modello risolto, non una costante (F2).

Prima il target era 600.000 byte per chiunque, tarato sul modello piu' piccolo.
In mix-gm il THINK e' glm-5.2 (1M di token): comprimere il suo body a 600 KB
buttava via un terzo della conversazione per un limite inesistente su quel
modello. Misurato il 2026-08-16: 1.371 shrink preventivi.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


@pytest.fixture
def glm(monkeypatch):
    """glm_backend ricaricato SENZA l'override d'ambiente.

    La costante si legge all'import: senza il purge di sys.modules un altro test
    che ha gia' importato il modulo con l'override attivo falserebbe questi.
    """
    monkeypatch.delenv("AIROUTER_GLM_CONTEXT_SHRINK_TARGET", raising=False)
    for m in ("glm_backend", "model_context_map", "token_counter"):
        sys.modules.pop(m, None)
    mod = importlib.import_module("glm_backend")
    yield mod
    for m in ("glm_backend", "model_context_map", "token_counter"):
        sys.modules.pop(m, None)


def test_glm_5_2_non_viene_compresso_a_600k(glm):
    """Il caso che il fix esiste per risolvere."""
    t = glm.glm_shrink_target_for("glm-5.2")
    assert t > 3_000_000, t


def test_glm_4_7_resta_prudente(glm):
    """Il modello piccolo non deve guadagnare contesto che non ha: ~640 KB."""
    t = glm.glm_shrink_target_for("glm-4.7")
    assert 600_000 <= t <= 700_000, t


def test_modello_sconosciuto_ricade_sul_valore_storico(glm):
    """Mai sotto i 600.000 di prima: sbagliare in difetto e' peggio."""
    assert glm.glm_shrink_target_for("modello-mai-visto") >= 600_000
    assert glm.glm_shrink_target_for(None) >= 600_000
    assert glm.glm_shrink_target_for("") >= 600_000


def test_ordinamento_coerente_col_contesto(glm):
    """Piu' contesto ha il modello, piu' alto e' il target. Controprova indipendente
    dai numeri esatti: se un giorno la mappa cambia, la relazione deve reggere."""
    from model_context_map import get_context_limit
    modelli = ["glm-4.7", "glm-5-turbo", "glm-5.2"]
    per_ctx = sorted(modelli, key=get_context_limit)
    per_target = sorted(modelli, key=glm.glm_shrink_target_for)
    assert [get_context_limit(m) for m in per_ctx] == sorted(get_context_limit(m) for m in modelli)
    assert per_target[-1] == per_ctx[-1] == "glm-5.2"


def test_override_ambiente_vince(monkeypatch):
    monkeypatch.setenv("AIROUTER_GLM_CONTEXT_SHRINK_TARGET", "123456")
    for m in ("glm_backend",):
        sys.modules.pop(m, None)
    mod = importlib.import_module("glm_backend")
    try:
        assert mod.glm_shrink_target_for("glm-5.2") == 123456
    finally:
        sys.modules.pop("glm_backend", None)


def test_forward_glm_usa_il_target_del_modello(glm):
    """Controprova sul call site: la costante non deve piu' comparire nel confronto."""
    src = (Path(__file__).resolve().parents[2] / "src" / "glm_backend.py").read_text()
    assert "if len(body) > GLM_SHRINK_TARGET_BYTES:" not in src
    assert "_shrink_target = glm_shrink_target_for(upstream_model or model)" in src
