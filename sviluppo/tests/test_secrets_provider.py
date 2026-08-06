import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import paths      # noqa: E402
import secrets_provider as sp   # noqa: E402

import asyncio
import os
import pytest
import shutil
import subprocess


@pytest.fixture(autouse=True)
def cleanup():
    sp.clear_cache()
    paths.reset_cache()
    yield
    sp.clear_cache()
    paths.reset_cache()


def test_env_var_name_minimax():
    """Verifica conversione dot notation a env var per minimax."""
    assert sp.env_var_name("minimax.api_key") == "MINIMAX_API_KEY"


def test_env_var_name_qwen():
    """Verifica conversione dot notation a env var per qwen."""
    assert sp.env_var_name("qwen.workspace_id") == "QWEN_WORKSPACE_ID"


def test_env_var_name_trattino():
    """Verifica che i trattini diventino underscore."""
    assert sp.env_var_name("test.key-name") == "TEST_KEY_NAME"


def test_mask_lungo():
    """Maschera valore lungo: prime 3 + ... + ultime 4."""
    result = sp.mask("abcdefghijklmnop")
    assert result == "abc...mnop"
    assert "abcdefghijklmnop" not in result


def test_mask_corto():
    """Maschera valore corto: restituisce ***."""
    assert sp.mask("abc12") == "***"


def test_mask_vuoto():
    """Maschera stringa vuota: restituisce ***."""
    assert sp.mask("") == "***"


def test_mask_valore_originale_non_visibile():
    """Verifica che il valore originale non appaia per intero."""
    originale = "segreto123456789"
    result = sp.mask(originale)
    assert originale not in result


def test_extra_env_vince(tmp_path, monkeypatch):
    """extra_env ha precedenza su env var e file."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "da-env")
    monkeypatch.setenv("MINIMAX_API_KEY", "da-altro")
    (tmp_path / ".env").write_text("LOCAL_LLM_API_KEY=da-file\n")
    result = sp.get_secret(
        "local_llm.api_key", extra_env=("LOCAL_LLM_API_KEY",)
    )
    assert result == "da-env"


def test_env_var_vince_su_file(tmp_path, monkeypatch):
    """Env var derivata dal nome vince sul file .env."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "da-env")
    (tmp_path / ".env").write_text("MINIMAX_API_KEY=da-file\n")
    result = sp.get_secret("minimax.api_key")
    assert result == "da-env"


def test_file_vince_su_script(tmp_path, monkeypatch):
    """File .env vince sullo script secrets.sh."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    script = secrets_dir / "secrets.sh"
    script.write_text("#!/bin/bash\necho da-script\n")
    script.chmod(0o755)
    (tmp_path / ".env").write_text("MINIMAX_API_KEY=da-file\n")
    result = sp.get_secret("minimax.api_key")
    assert result == "da-file"


def test_file_accetta_punto(tmp_path, monkeypatch):
    """File .env accetta chiave con punto quando maiuscola manca."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    (tmp_path / ".env").write_text("minimax.api_key=valore-punto\n")
    result = sp.get_secret("minimax.api_key")
    assert result == "valore-punto"


def test_nessuna_fonte_restituisce_vuoto(tmp_path, monkeypatch):
    """Assenza totale restituisce stringa vuota, non None."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result = sp.get_secret("minimax.api_key")
    assert result == ""
    assert result is not None


def test_parse_env_vari_casi(tmp_path, monkeypatch):
    """Parsing .env: righe vuote, commenti, spazi, apici, no uguale."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("API_KEY_1", raising=False)
    monkeypatch.delenv("API_KEY_2", raising=False)
    monkeypatch.delenv("API_KEY_3", raising=False)
    monkeypatch.delenv("API_KEY_4", raising=False)
    (tmp_path / ".env").write_text(
        "# commento\n\n  KEY1  =  value1\n"
        "KEY2=\"quoted\"\nKEY3='single'\n"
        "noequals\nKEY4=valore4\n"
    )
    assert sp.get_secret("key1") == "value1"
    assert sp.get_secret("key2") == "quoted"
    assert sp.get_secret("key3") == "single"
    assert sp.get_secret("noequals") == ""
    assert sp.get_secret("key4") == "valore4"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash non disponibile")
def test_script_trova_secrets_sh(tmp_path, monkeypatch):
    """Script secrets.sh usato quando env e .env sono vuoti."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    script = secrets_dir / "secrets.sh"
    script.write_text("#!/bin/bash\n"
                      "if [ \"$1\" = \"get\" ] && [ \"$2\" = \"minimax.api_key\" ]; then\n"  # noqa: E501
                      "  echo valore-dallo-script\n"
                      "fi\n")
    script.chmod(0o755)
    result = sp.get_secret("minimax.api_key")
    assert result == "valore-dallo-script"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash non disponibile")
def test_script_exit_1_restituisce_vuoto(tmp_path, monkeypatch):
    """Script che esce con codice 1 restituisce stringa vuota."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    script = secrets_dir / "secrets.sh"
    script.write_text("#!/bin/bash\nexit 1\n")
    script.chmod(0o755)
    result = sp.get_secret("minimax.api_key")
    assert result == ""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash non disponibile")
def test_script_stdout_vuoto_restituisce_vuoto(tmp_path, monkeypatch):
    """Script che stampa riga vuota restituisce stringa vuota."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    script = secrets_dir / "secrets.sh"
    script.write_text("#!/bin/bash\necho\n")
    script.chmod(0o755)
    result = sp.get_secret("minimax.api_key")
    assert result == ""


def test_cache_memorizza_valore(tmp_path, monkeypatch):
    """Chiamate consecutive colpiscono la cache."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "valore-cached")
    result1 = sp.get_secret("minimax.api_key")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result2 = sp.get_secret("minimax.api_key")
    assert result1 == "valore-cached"
    assert result2 == "valore-cached"


def test_cache_clear_recupera_nuovo_valore(tmp_path, monkeypatch):
    """Dopo clear_cache il valore vecchio non e piu restituito."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "vecchio")
    sp.get_secret("minimax.api_key")
    sp.clear_cache()
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result = sp.get_secret("minimax.api_key")
    assert result == ""


def test_no_cache_non_memorizza(tmp_path, monkeypatch):
    """Con use_cache=False il valore non viene memorizzato."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "temp")
    result1 = sp.get_secret("minimax.api_key", use_cache=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result2 = sp.get_secret("minimax.api_key")
    assert result1 == "temp"
    assert result2 == ""


def test_async_stesso_risultato(tmp_path, monkeypatch):
    """get_secret_async restituisce lo stesso risultato di get_secret."""
    monkeypatch.setenv("AIROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "sync-async-test")
    sync_result = sp.get_secret("minimax.api_key")
    async_result = asyncio.run(sp.get_secret_async("minimax.api_key"))
    assert sync_result == async_result
