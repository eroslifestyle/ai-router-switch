"""Cooldown anti-retry-storm su 401/403 dall'upstream Qwen (2026-09-24).

Un 401 e' permanente: il cooldown evita di rimartellare l'upstream, e si
azzera se la chiave cambia. Nessun valore di chiave nei log.
"""
import sys
import time

sys.path.insert(0, 'src')
import qwen_backend


def test_cooldown_scatta_dopo_un_401():
    qwen_backend._qwen_auth_reject.clear()
    qwen_backend.qwen_auth_mark_rejected("chiave-finta-a", time.time())
    assert qwen_backend.qwen_auth_is_cooling_down("chiave-finta-a", time.time())
    assert not qwen_backend.qwen_auth_is_cooling_down("chiave-finta-b", time.time())


def test_cooldown_scade():
    qwen_backend._qwen_auth_reject.clear()
    vecchio = time.time() - qwen_backend.QWEN_AUTH_COOLDOWN_SEC - 1
    qwen_backend.qwen_auth_mark_rejected("chiave-finta-a", vecchio)
    assert not qwen_backend.qwen_auth_is_cooling_down("chiave-finta-a", time.time())


def test_cambio_chiave_azzera_il_cooldown():
    qwen_backend._qwen_auth_reject.clear()
    qwen_backend.qwen_auth_mark_rejected("chiave-finta-a", time.time())
    # Nuova chiave rinnvata: il cooldown sulla vecchia non deve bloccarla
    assert not qwen_backend.qwen_auth_is_cooling_down("nuova-chiave", time.time())
    # E il mark sulla nuova sostituisce la vecchia (una sola entry attiva)
    qwen_backend.qwen_auth_mark_rejected("nuova-chiave", time.time())
    assert len(qwen_backend._qwen_auth_reject) == 1
    assert not qwen_backend.qwen_auth_is_cooling_down("chiave-finta-a", time.time())


def test_fingerprint_non_contiene_la_chiave():
    fp = qwen_backend._key_fingerprint("super-segreto-12345")
    assert "super-segreto" not in fp
    assert "12345" not in fp


def test_source_hint_non_esponga_valori(monkeypatch=None):
    qwen_backend._qwen_auth_reject.clear()
    if monkeypatch is not None:
        monkeypatch.setenv("QWEN_API_KEY", "valore-segreto")
    hint = qwen_backend.qwen_key_source_hint("qualsiasi-chiave")
    assert "valore" not in hint or "segreto" not in hint
    # Se la env non e' impostata, il hint resta generico e non contiene chiavi
    assert "qualsiasi-chiave" not in hint


if __name__ == "__main__":
    test_cooldown_scatta_dopo_un_401()
    test_cooldown_scade()
    test_cambio_chiave_azzera_il_cooldown()
    test_fingerprint_non_contiene_la_chiave()
    test_source_hint_non_esponga_valori()
    print("OK: 5/5 test passati")
