"""Regressione: _pipeline_minimax_orchestrate non deve raddoppiare il retry budget.

Root cause (audit 2026-09-02, sviluppo/audit/2026-09-02-audit-velocita-reale/REPORT.md
§4.3): la funzione chiama forward_minimax() due volte in sequenza — preflight con
shrink (contesto troppo grande) e tentativo principale — e nessuna delle due
passava retry_budget_sec, quindi ognuna riceveva MINIMAX_RETRY_CAP_SEC pieno.
Nel caso peggiore il turno restava appeso fino a 2x il tetto dichiarato (misurato:
mediana 125s sui fallimenti mix-am, contro un tetto documentato di 90s).

Il fix condivide un'unica finestra di tempo (_budget_left()) fra le due chiamate:
questo test dimostra che il secondo forward_minimax riceve un budget ridotto
proporzionalmente al tempo già speso dal primo, invece di un budget pieno.
"""

import json
import pathlib
import sys
import time
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

import pipeline_minimax  # noqa: E402
from router_constants import MINIMAX_RETRY_CAP_SEC  # noqa: E402

pytestmark = pytest.mark.asyncio


class _FakeRequest:
    headers = {}
    remote = "127.0.0.1"
    path = "/v1/messages"

    def get(self, key, default=None):
        return default


class _FakeUpstream:
    def __init__(self, status):
        self.status = status

    async def release(self):
        pass


async def test_budget_condiviso_tra_preflight_e_tentativo_principale(monkeypatch):
    """Il secondo forward_minimax NON deve ricevere un budget pieno se il primo
    ha già consumato tempo: la somma dei due budget deve restare <= al tetto."""

    orig = {"model": "MiniMax-M2.7", "messages": [{"role": "user", "content": "ciao"}]}
    body = json.dumps(orig).encode() * 50000  # forza _is_context_too_large_for_minimax=True

    # Simula il tempo che passa: ogni chiamata a time.monotonic() avanza di 50s,
    # cosi' il preflight "costa" 50s prima che il main venga chiamato.
    fake_clock = {"t": 0.0}

    def fake_monotonic():
        fake_clock["t"] += 50.0
        return fake_clock["t"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    # _try_shrink_body: ritorna un body diverso (shrink "riuscito") cosi' si
    # entra nel ramo preflight.
    async def fake_shrink(orig_dict, target_bytes):
        return b"shrunk-body"

    monkeypatch.setattr(pipeline_minimax, "_try_shrink_body", fake_shrink)

    budgets_ricevuti = []

    async def fake_forward_minimax(request, body_arg, session, retry_budget_sec=None,
                                    act_timeout_sec=None, model_override=None):
        budgets_ricevuti.append(retry_budget_sec)
        # preflight fallisce (status >= 400) per far cadere nel tentativo principale
        return _FakeUpstream(400)

    fake_module = types.ModuleType("forward_minimax")
    fake_module.forward_minimax = fake_forward_minimax
    monkeypatch.setitem(sys.modules, "forward_minimax", fake_module)

    async def fake_relay(*a, **kw):
        return "RELAYED"

    result = await pipeline_minimax._pipeline_minimax_orchestrate(
        _FakeRequest(), body, session=None, orig=orig, relay=fake_relay,
    )

    assert len(budgets_ricevuti) == 2, "attesi esattamente 2 tentativi (preflight + main)"
    budget_preflight, budget_main = budgets_ricevuti

    # PRIMA del fix: entrambi None -> forward_minimax userebbe MINIMAX_RETRY_CAP_SEC
    # pieno per OGNI chiamata (bug 2x). DOPO il fix: nessuno dei due e' None, e il
    # secondo e' sempre <= al primo perche' condividono la stessa finestra di tempo.
    assert budget_preflight is not None and budget_main is not None
    assert budget_preflight < MINIMAX_RETRY_CAP_SEC, (
        "il preflight deve gia' vedere il tempo speso da _t0, non un budget pieno"
    )
    assert budget_main <= budget_preflight, (
        "il tentativo principale deve ricevere MENO (o uguale, se il tempo e' "
        "trascurabile) budget del preflight, mai un budget pieno indipendente "
        "(regressione del bug 2x che produceva la mediana 125s su mix-am)"
    )
    assert budget_main == 0.0, (
        "con 100s gia' spesi su un tetto di 90s il budget residuo deve essere "
        "clampato a 0 (fail-fast), non ripartire da zero per un altro giro pieno"
    )


async def test_budget_pieno_quando_contesto_non_e_troppo_grande(monkeypatch):
    """Caso comune (nessun preflight): il tentativo unico riceve il budget quasi
    pieno, nessuna regressione sul path felice."""
    orig = {"model": "MiniMax-M2.7", "messages": [{"role": "user", "content": "ciao"}]}
    body = json.dumps(orig).encode()  # piccolo: _is_context_too_large_for_minimax=False

    fake_clock = {"t": 0.0}

    def fake_monotonic():
        fake_clock["t"] += 0.01
        return fake_clock["t"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    budgets_ricevuti = []

    async def fake_forward_minimax(request, body_arg, session, retry_budget_sec=None,
                                    act_timeout_sec=None, model_override=None):
        budgets_ricevuti.append(retry_budget_sec)
        return _FakeUpstream(200)

    fake_module = types.ModuleType("forward_minimax")
    fake_module.forward_minimax = fake_forward_minimax
    monkeypatch.setitem(sys.modules, "forward_minimax", fake_module)

    async def fake_relay(*a, **kw):
        return "RELAYED"

    await pipeline_minimax._pipeline_minimax_orchestrate(
        _FakeRequest(), body, session=None, orig=orig, relay=fake_relay,
    )

    assert len(budgets_ricevuti) == 1
    assert budgets_ricevuti[0] == pytest.approx(MINIMAX_RETRY_CAP_SEC, abs=0.5)
