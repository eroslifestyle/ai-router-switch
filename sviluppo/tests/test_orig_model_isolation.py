"""Regressione: il sidecar non deve piu' scambiare orig_model fra richieste.

Sintomo (misurato 2026-08-08, mode=mix-am): 64 richieste registrate come
``claude-haiku -> claude-direct`` (delega haiku finita ad Anthropic) che in
realta' erano ``opus -> Anthropic``. Causa: ``_request_orig_model`` era chiavato
sul fingerprint della chat; quando il fingerprint era ``default`` (session-id
assente, condiviso) l'orig scritto da una richiesta haiku in volo veniva letto
dalla richiesta opus successiva verso Anthropic, e ``_orig = orig_model or
body_model`` preferiva l'orig stale al modello del body.

Fix: chiave ``id(request)`` (univoca per richiesta, stesso oggetto in
``remap_body_for_minimax`` e nel ``relay``). Questo test fallirebbe se qualcuno
riportasse la chiave sul fingerprint condiviso.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from minimax_body import remap_body_for_minimax  # noqa: E402


class _FakeRequest:
    """Basta che due istanze abbiano id() distinti."""


def test_orig_model_isolato_per_richiesta_anche_con_fingerprint_condiviso():
    store = {}
    req_haiku = _FakeRequest()   # path MiniMax: scrive il proprio orig
    req_opus = _FakeRequest()    # path Anthropic: NON scrive

    # Le due richieste "condividono" il fingerprint default (session-id assente).
    remap_body_for_minimax(
        json.dumps({"model": "claude-haiku-4-5", "messages": []}).encode(),
        request=req_haiku,
        orig_model_store=store,
        resolve_fp=lambda _r: "default",
        target_model="MiniMax-M2.7",
    )

    # Il relay della richiesta opus fa pop sul proprio id: deve NON trovare
    # l'orig dell'haiku (che appartiene a un'altra richiesta).
    val_opus = store.pop(str(id(req_opus)), None)
    # Il relay dell'haiku ritrova il proprio orig, intatto.
    val_haiku = store.pop(str(id(req_haiku)), None)

    assert val_opus is None, f"contaminazione residua: opus ha letto {val_opus!r}"
    assert val_haiku == "claude-haiku-4-5", f"orig haiku perso: {val_haiku!r}"


def test_chiave_non_e_piu_il_fingerprint_condiviso():
    # Retromarcia esplicita: con la chiave fingerprint, due richieste distinte
    # con fingerprint "default" colliderebbero. Con id(request) no.
    store = {}
    r1, r2 = _FakeRequest(), _FakeRequest()
    remap_body_for_minimax(
        json.dumps({"model": "claude-haiku-4-5", "messages": []}).encode(),
        request=r1, orig_model_store=store,
        resolve_fp=lambda _r: "default", target_model="MiniMax-M2.7",
    )
    # Nessuna entry con chiave "default" (vecchia chiave condivisa).
    assert "default" not in store, "la chiave e' tornata al fingerprint condiviso"
    assert str(id(r1)) in store
