import json
import sys
import importlib
import time
from pathlib import Path

import pytest

from conftest import wait_for_sidecar_text


def _import_router_utils():
    sys.modules.pop('router_utils', None)
    src_path = str(Path(__file__).resolve().parents[2] / 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    return importlib.import_module('router_utils')


def _import_router_debug():
    sys.modules.pop('router_debug', None)
    src_path = str(Path(__file__).resolve().parents[2] / 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    return importlib.import_module('router_debug')


class TestCampiLatenza:

    def test_campi_presenti_quando_valorizzati(self, tmp_path, monkeypatch):
        router_utils = _import_router_utils()
        sidecar_path = tmp_path / 'usage.jsonl'
        monkeypatch.setattr(router_utils, 'USAGE_SIDECAR', sidecar_path)

        router_utils.log_router_usage(
            chat_id='c',
            orig='o',
            final='f',
            usage={'input_tokens': 1, 'output_tokens': 2},
            mode='chat',
            ttfb_ms=123.456,
            total_ms=7890.12,
        )

        entries = wait_for_sidecar_text(sidecar_path).strip().split('\n')
        entry = json.loads(entries[-1])

        assert 'ttfb_ms' in entry
        assert entry['ttfb_ms'] == 123.5
        assert 'total_ms' in entry
        assert entry['total_ms'] == 7890.1

    def test_campi_assenti_se_none(self, tmp_path, monkeypatch):
        router_utils = _import_router_utils()
        sidecar_path = tmp_path / 'usage.jsonl'
        monkeypatch.setattr(router_utils, 'USAGE_SIDECAR', sidecar_path)

        router_utils.log_router_usage(
            chat_id='c',
            orig='o',
            final='f',
            usage={'input_tokens': 1, 'output_tokens': 2},
            mode='chat',
        )

        entries = wait_for_sidecar_text(sidecar_path).strip().split('\n')
        entry = json.loads(entries[-1])

        assert 'ttfb_ms' not in entry
        assert 'total_ms' not in entry

    def test_solo_ttfb(self, tmp_path, monkeypatch):
        router_utils = _import_router_utils()
        sidecar_path = tmp_path / 'usage.jsonl'
        monkeypatch.setattr(router_utils, 'USAGE_SIDECAR', sidecar_path)

        router_utils.log_router_usage(
            chat_id='c',
            orig='o',
            final='f',
            usage={'input_tokens': 1, 'output_tokens': 2},
            mode='chat',
            ttfb_ms=50.0,
        )

        entries = wait_for_sidecar_text(sidecar_path).strip().split('\n')
        entry = json.loads(entries[-1])

        assert 'ttfb_ms' in entry
        assert entry['ttfb_ms'] == 50.0
        assert 'total_ms' not in entry

    def test_zero_e_un_valore_valido(self, tmp_path, monkeypatch):
        router_utils = _import_router_utils()
        sidecar_path = tmp_path / 'usage.jsonl'
        monkeypatch.setattr(router_utils, 'USAGE_SIDECAR', sidecar_path)

        router_utils.log_router_usage(
            chat_id='c',
            orig='o',
            final='f',
            usage={'input_tokens': 1, 'output_tokens': 2},
            mode='chat',
            ttfb_ms=0.0,
        )

        entries = wait_for_sidecar_text(sidecar_path).strip().split('\n')
        entry = json.loads(entries[-1])

        assert 'ttfb_ms' in entry
        assert entry['ttfb_ms'] == 0.0

    def test_accetta_interi_e_stringhe_numeriche(self, tmp_path, monkeypatch):
        router_utils = _import_router_utils()
        sidecar_path = tmp_path / 'usage.jsonl'
        monkeypatch.setattr(router_utils, 'USAGE_SIDECAR', sidecar_path)

        router_utils.log_router_usage(
            chat_id='c',
            orig='o',
            final='f',
            usage={'input_tokens': 1, 'output_tokens': 2},
            mode='chat',
            ttfb_ms=5,
            total_ms='12.34',
        )

        entries = wait_for_sidecar_text(sidecar_path).strip().split('\n')
        entry = json.loads(entries[-1])

        assert entry['ttfb_ms'] == 5.0
        assert entry['total_ms'] == 12.3

    def test_campi_esistenti_non_cambiano(self, tmp_path, monkeypatch):
        router_utils = _import_router_utils()
        sidecar_path = tmp_path / 'usage.jsonl'
        monkeypatch.setattr(router_utils, 'USAGE_SIDECAR', sidecar_path)

        router_utils.log_router_usage(
            chat_id='c',
            orig='o',
            final='f',
            usage={
                'input_tokens': 10,
                'output_tokens': 20,
                'cache_read': 5,
                'cache_creation': 3,
                'stop_reason': 'stop',
            },
            mode='chat',
            client='cli',
            status=200,
        )

        entries = wait_for_sidecar_text(sidecar_path).strip().split('\n')
        entry = json.loads(entries[-1])

        storici = [
            'ts', 'chat', 'orig', 'final', 'mode', 'client', 'status',
            'input_tokens', 'output_tokens', 'cache_read', 'cache_creation',
            'stop_reason', 'text_blocks', 'thinking_blocks', 'tool_use_blocks',
        ]
        for campo in storici:
            assert campo in entry, f'Campo storico {campo} mancante'

    def test_ordine_campi_latenza_in_coda(self, tmp_path, monkeypatch):
        router_utils = _import_router_utils()
        sidecar_path = tmp_path / 'usage.jsonl'
        monkeypatch.setattr(router_utils, 'USAGE_SIDECAR', sidecar_path)

        router_utils.log_router_usage(
            chat_id='c',
            orig='o',
            final='f',
            usage={
                'input_tokens': 1,
                'output_tokens': 2,
                # int e non lista: log_router_usage fa int(...) su questo campo e
                # una lista solleverebbe TypeError, che il try/except del logger
                # ingoia — l'entry non verrebbe scritta affatto.
                'tool_use_blocks': 1,
            },
            mode='chat',
            ttfb_ms=10.0,
            total_ms=20.0,
        )

        entries = wait_for_sidecar_text(sidecar_path).strip().split('\n')
        entry = json.loads(entries[-1])
        keys = list(entry.keys())

        idx_ttfb = keys.index('ttfb_ms')
        idx_tool = keys.index('tool_use_blocks')
        idx_total = keys.index('total_ms')

        assert idx_ttfb > idx_tool, 'ttfb_ms deve essere dopo tool_use_blocks'
        assert idx_total > idx_tool, 'total_ms deve essere dopo tool_use_blocks'


class TestMiddlewareTStart:
    # Il marker sta sulla classe e non a livello di modulo: i test di
    # TestCampiLatenza sono sincroni e un pytestmark globale li marcherebbe
    # asyncio, generando un warning per ciascuno.
    pytestmark = pytest.mark.asyncio

    async def test_middleware_marca_t_start(self, tmp_path, monkeypatch):
        router_debug = _import_router_debug()

        class FakeRequest:
            def __init__(self):
                self._data = {}
                self.path = '/v1/messages'
                self.headers = {}
                self.remote = '127.0.0.1'

            def __setitem__(self, key, value):
                self._data[key] = value

            def __getitem__(self, key):
                return self._data[key]

        sentinel = object()
        called = False

        async def handler(req):
            nonlocal called
            called = True
            return sentinel

        req = FakeRequest()
        result = await router_debug.debug_auth_middleware(req, handler)

        assert 't_start' in req._data
        assert isinstance(req._data['t_start'], float)
        assert req._data['t_start'] > 0
        assert result is sentinel
        assert called

    async def test_t_start_precede_la_risposta(self, tmp_path, monkeypatch):
        router_debug = _import_router_debug()

        class FakeRequest:
            def __init__(self):
                self._data = {}

            def __setitem__(self, key, value):
                self._data[key] = value

            def __getitem__(self, key):
                return self._data[key]

        async def handler(req):
            return 'response'

        req = FakeRequest()
        await router_debug.debug_auth_middleware(req, handler)
        t_after = time.monotonic()

        assert req._data['t_start'] <= t_after

    async def test_request_non_sottoscrivibile_non_rompe(self, tmp_path, monkeypatch):
        router_debug = _import_router_debug()

        class BrokenRequest:
            path = '/v1/messages'
            headers = {}
            remote = '127.0.0.1'

            def __setitem__(self, key, value):
                raise TypeError('not subscriptable')

        handler_called = False

        async def handler(req):
            nonlocal handler_called
            handler_called = True
            return 'ok'

        req = BrokenRequest()
        result = await router_debug.debug_auth_middleware(req, handler)

        assert handler_called
        assert result == 'ok'
