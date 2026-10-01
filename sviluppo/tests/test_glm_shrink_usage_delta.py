"""Lo shrink preventivo di forward_glm deve notificare il body ridotto al proxy,
che ci ricalcola l'usage da restituire al client (bug 2026-10-01: in mix-ag il
client vedeva i token del body ridotto e non compattava mai)."""

import asyncio

import glm_backend


class _BoomSession:
    def post(self, *args, **kwargs):
        raise RuntimeError("niente rete nei test")


class _Req:
    path_qs = "/v1/messages"
    headers = {}


def test_forward_glm_chiama_on_shrink_col_body_ridotto(monkeypatch):
    async def _key():
        return "k"

    ridotti = []

    async def _shrink(body, target):
        ridotti.append(body[:target])
        return ridotti[-1]

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)
    monkeypatch.setattr(glm_backend, "glm_shrink_target_for", lambda model: 100)
    import context_shrink
    monkeypatch.setattr(context_shrink, "shrink_body_to_budget", _shrink)

    visti = []
    body = (b'{"model":"glm-4.7","max_tokens":10,"messages":[{"role":"user","content":"'
            + b"x" * 500 + b'"}]}')
    try:
        asyncio.run(glm_backend.forward_glm(
            _Req(), body, _BoomSession(), "claude-haiku-4-5", log_fn=lambda *_: None,
            upstream_model="glm-4.7", on_shrink=visti.append))
    except Exception:
        pass  # la rete fallisce di proposito: conta solo il callback
    assert ridotti and visti == ridotti


def test_forward_qwen_chiama_on_shrink_col_body_ridotto(monkeypatch):
    """Lo shrink preventivo di forward_qwen notifica il body ridotto al proxy."""
    import qwen_backend

    async def _key():
        return "k"

    ridotti = []

    async def _shrink(body, target):
        ridotti.append(body[:target])
        return ridotti[-1]

    monkeypatch.setattr(qwen_backend, "get_qwen_key", _key)
    monkeypatch.setattr(qwen_backend, "QWEN_SHRINK_TARGET_BYTES", 100)
    import context_shrink
    monkeypatch.setattr(context_shrink, "shrink_body_to_budget", _shrink)

    visti = []
    body = (b'{"model":"qwen-coder-plus","max_tokens":10,"messages":[{"role":"user","content":"'
            + b"x" * 500 + b'"}]}')
    try:
        asyncio.run(qwen_backend.forward_qwen(
            _Req(), body, _BoomSession(), "claude-haiku-4-5", log_fn=lambda *_: None,
            upstream_model="qwen-coder-plus", on_shrink=visti.append))
    except Exception:
        pass  # la rete fallisce di proposito: conta solo il callback
    assert ridotti and visti == ridotti
