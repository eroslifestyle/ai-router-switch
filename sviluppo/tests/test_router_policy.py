import json
import sys
import time
import pytest
sys.path.insert(0, 'src')
import router_policy as rp


@pytest.fixture
def policy_file(tmp_path):
    original_policy_file = rp.POLICY_FILE
    original_cache = rp._cache.copy()
    temp_file = tmp_path / "router-policy.json"
    rp.POLICY_FILE = temp_file
    rp._cache = {"ts": 0.0, "data": {}}

    def write_policy(policy_data):
        with open(rp.POLICY_FILE, 'w') as f:
            json.dump(policy_data, f)
        rp._cache = {"ts": 0.0, "data": {}}

    yield write_policy

    rp.POLICY_FILE = original_policy_file
    rp._cache = original_cache


def test_modello_degradato_sopra_soglia(policy_file):
    """Verifica che un modello con ewma >= 0.5 venga riconosciuto come degradato."""
    policy = {
        "degraded": {
            "glm-5.3": {
                "task_class": "coding",
                "ewma": 0.71
            }
        }
    }
    policy_file(policy)
    result = rp.is_degraded("glm-5.3", "coding")
    assert result is True, (
        f"Atteso: True (ewma 0.71 >= soglia 0.5), "
        f"Ottenuto: {result}"
    )


def test_task_class_diversa_non_degrada(policy_file):
    """Verifica che una task_class diversa non attivi il degraded per lo stesso modello."""
    policy = {
        "degraded": {
            "glm-5.3": {
                "task_class": "coding",
                "ewma": 0.71
            }
        }
    }
    policy_file(policy)
    result_chat = rp.is_degraded("glm-5.3", "chat")
    assert result_chat is False, (
        f"Atteso: False (task_class 'chat' diversa da 'coding'), "
        f"Ottenuto: {result_chat}"
    )
    result_default = rp.is_degraded("glm-5.3")
    assert result_default is False, (
        f"Atteso: False (task_class default '*' diversa da 'coding'), "
        f"Ottenuto: {result_default}"
    )


def test_modello_assente_non_degrada(policy_file):
    """Verifica che un modello non presente nella policy non sia degradato."""
    policy_file({})
    result = rp.is_degraded("MiniMax-M2.7", "coding")
    assert result is False, (
        f"Atteso: False (modello assente dalla policy), "
        f"Ottenuto: {result}"
    )


def test_wildcard_si_applica_a_ogni_task_class(policy_file):
    """Verifica che task_class '*' si applichi a ogni task_class."""
    policy = {
        "degraded": {
            "test-model": {
                "task_class": "*",
                "ewma": 0.8
            }
        }
    }
    policy_file(policy)
    result_coding = rp.is_degraded("test-model", "coding")
    assert result_coding is True, (
        f"Atteso: True (wildcard '*' per task 'coding'), "
        f"Ottenuto: {result_coding}"
    )
    result_chat = rp.is_degraded("test-model", "chat")
    assert result_chat is True, (
        f"Atteso: True (wildcard '*' per task 'chat'), "
        f"Ottenuto: {result_chat}"
    )
    result_default = rp.is_degraded("test-model")
    assert result_default is True, (
        f"Atteso: True (wildcard '*' per task default '*'), "
        f"Ottenuto: {result_default}"
    )


def test_soglia_esatta(policy_file):
    """Verifica il bordo della soglia a 0.5 (>= 0.5 restituisce True)."""
    policy_049 = {
        "degraded": {
            "model": {"task_class": "*", "ewma": 0.49}
        }
    }
    policy_file(policy_049)
    result_049 = rp.is_degraded("model")
    assert result_049 is False, (
        f"Atteso: False (ewma 0.49 < 0.5), "
        f"Ottenuto: {result_049}"
    )

    policy_050 = {
        "degraded": {
            "model": {"task_class": "*", "ewma": 0.5}
        }
    }
    policy_file(policy_050)
    result_050 = rp.is_degraded("model")
    assert result_050 is True, (
        f"Atteso: True (ewma 0.5 >= 0.5), "
        f"Ottenuto: {result_050}"
    )

    policy_051 = {
        "degraded": {
            "model": {"task_class": "*", "ewma": 0.51}
        }
    }
    policy_file(policy_051)
    result_051 = rp.is_degraded("model")
    assert result_051 is True, (
        f"Atteso: True (ewma 0.51 >= 0.5), "
        f"Ottenuto: {result_051}"
    )


def test_fail_open_su_file_mancante(tmp_path):
    """Verifica fail-open quando il file di policy non esiste."""
    missing_file = tmp_path / "nonexistent.json"
    original_policy_file = rp.POLICY_FILE
    original_cache = rp._cache.copy()
    rp.POLICY_FILE = missing_file
    rp._cache = {"ts": 0.0, "data": {}}

    result = rp.get_policy()
    assert result == {}, (
        f"Atteso: {{}} (fail-open su file mancante), "
        f"Ottenuto: {result}"
    )

    degraded_result = rp.is_degraded("qualsiasi", "coding")
    assert degraded_result is False, (
        f"Atteso: False (policy vuota), "
        f"Ottenuto: {degraded_result}"
    )

    rp.POLICY_FILE = original_policy_file
    rp._cache = original_cache


def test_fail_open_su_json_invalido(policy_file):
    """Verifica fail-open quando il file contiene JSON invalido."""
    invalid_json = "{ questo non e json"
    with open(rp.POLICY_FILE, 'w') as f:
        f.write(invalid_json)
    rp._cache = {"ts": 0.0, "data": {}}

    result = rp.get_policy()
    assert result == {}, (
        f"Atteso: {{}} (fail-open su JSON invalido), "
        f"Ottenuto: {result}"
    )


def test_cache_ttl_evita_riletture(policy_file):
    """Verifica che il cache TTL eviti riletture del file."""
    policy = {
        "degraded": {
            "cached-model": {"task_class": "*", "ewma": 0.9}
        }
    }
    policy_file(policy)
    first_result = rp.get_policy()
    assert first_result == policy, (
        f"Atteso: {policy}, "
        f"Ottenuto: {first_result}"
    )

    with open(rp.POLICY_FILE, 'w') as f:
        json.dump({}, f)

    cached_result = rp.get_policy()
    assert cached_result == policy, (
        f"Atteso: {policy} (dalla cache, TTL non scaduto), "
        f"Ottenuto: {cached_result}"
    )

    rp._cache["ts"] = time.time() - 10
    forced_result = rp.get_policy()
    assert forced_result == {}, (
        f"Atteso: {{}} (file riscritto, cache scaduta), "
        f"Ottenuto: {forced_result}"
    )


def test_ewma_non_numerica_non_esplode(policy_file):
    """Verifica comportamento con ewma non numerica (stringa 'abc')."""
    policy = {
        "degraded": {
            "broken-model": {"task_class": "*", "ewma": "abc"}
        }
    }
    policy_file(policy)
    with pytest.raises(ValueError):
        rp.is_degraded("broken-model")
