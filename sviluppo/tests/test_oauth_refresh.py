"""Test del refresh token OAuth — sistema di rinnovamento automatico.

Test suite per il metodo try_refresh_oauth della classe Resilience.
NESSUN test tocca il file reale ~/.claude/.credentials.json.
NESSUN test fa rete vera.
"""
import importlib
import json
import pathlib
import sys
import time
import urllib.request
from unittest import mock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))


def _import_res():
	"""Ricarica ai_router_resilience: la conftest purga i moduli di src/ da sys.modules."""
	sys.modules.pop("ai_router_resilience", None)
	return importlib.import_module("ai_router_resilience")


@pytest.fixture(autouse=True)
def _vieta_rete_reale(monkeypatch):
	"""Vieta le chiamate di rete reali: fallisce con messaggio chiaro se un test le tenta."""
	def _boom(*a, **k):
		raise AssertionError("il test ha tentato una chiamata di rete reale")
	monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _make_creds(
    access_token="sk-ant-oat01-testAccessToken",
    refresh_token="sk-ant-ort01-testRefreshToken",
    expires_at_ms=None,
    refresh_token_expires_at_ms=None,
    subscription_type="max",
    rate_limit_tier="default_claude_max_20x",
):
    """Crea un dict credenziali completo per i test."""
    if expires_at_ms is None:
        expires_at_ms = int((time.time() + 8 * 3600) * 1000)  # fra 8 ore
    if refresh_token_expires_at_ms is None:
        refresh_token_expires_at_ms = int((time.time() + 30 * 24 * 3600) * 1000)  # fra 30 giorni

    return {
        "claudeAiOauth": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at_ms,
            "refreshTokenExpiresAt": refresh_token_expires_at_ms,
            "scopes": [
                "user:file_upload",
                "user:inference",
                "user:mcp_servers",
                "user:profile",
                "user:sessions:claude_code",
            ],
            "subscriptionType": subscription_type,
            "rateLimitTier": rate_limit_tier,
        }
    }


def test_refresh_riuscito_aggiorna_file(tmp_path, monkeypatch):
    """Test 1: refresh riuscito aggiorna il file e preserva le altre chiavi."""
    # Ricarica il modulo per evitare istanze old della classe
    res_mod = _import_res()

    # Setup: crea file credenziali temporaneo con token che sta per scadere
    cred_file = tmp_path / ".credentials.json"
    cred_file.write_text(json.dumps(_make_creds(
        access_token="sk-ant-oat01-oldToken",
        expires_at_ms=int((time.time() + 60) * 1000),  # scade fra 1 minuto, non fra 8 ore
        subscription_type="max",
        rate_limit_tier="default_claude_max_20x",
    )))

    # Mock della risposta HTTP
    new_access_token = "sk-ant-oat01-newAccessToken"
    expires_in_s = 25200  # 7 ore

    mock_response_data = {
        "access_token": new_access_token,
        "refresh_token": "sk-ant-ort01-newRefreshToken",
        "expires_in": expires_in_s,
    }

    def mock_urlopen(req, *args, **kwargs):
        # Verifica che la richiesta sia formattata correttamente
        assert req.get_method() == "POST"
        assert "application/json" in req.headers.get("Content-type", "")
        body = json.loads(req.data.decode("utf-8"))
        assert body["grant_type"] == "refresh_token"
        assert "refresh_token" in body
        assert body["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

        # Mock della risposta
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    monkeypatch.setattr(res_mod.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(res_mod, "CRED_FILE", cred_file)

    # Test
    resilience = res_mod.Resilience(port=8787, log_fn=lambda m: None)
    success, msg = resilience.try_refresh_oauth()

    # Verifiche
    assert success, f"refresh doveva riuscire, ma: {msg}"
    assert "rinnovato" in msg.lower()
    assert "scade" in msg.lower()

    # Rileggi il file e verifica l'aggiornamento
    creds_updated = json.loads(cred_file.read_text())
    oauth = creds_updated["claudeAiOauth"]

    # Il nuovo token deve essere presente
    assert oauth["accessToken"] == new_access_token
    # Il nuovo refresh token deve essere presente
    assert oauth["refreshToken"] == "sk-ant-ort01-newRefreshToken"
    # expiresAt deve essere stato aggiornato (e futuro)
    now_ms = int(time.time() * 1000)
    assert oauth["expiresAt"] > now_ms
    # expiresAt deve riflettere expires_in (entro margine di errore 1s)
    expected_exp_ms = now_ms + expires_in_s * 1000
    assert abs(oauth["expiresAt"] - expected_exp_ms) < 1000  # 1s di tolleranza
    # Altre chiavi preservate
    assert oauth["subscriptionType"] == "max"
    assert oauth["rateLimitTier"] == "default_claude_max_20x"
    assert "scopes" in oauth
    assert len(oauth["scopes"]) > 0


def test_refresh_token_ruotato(tmp_path, monkeypatch):
    """Test 2: il refresh token viene ruotato nella risposta."""
    res_mod = _import_res()

    cred_file = tmp_path / ".credentials.json"
    old_refresh = "sk-ant-ort01-oldRefreshToken"
    cred_file.write_text(json.dumps(_make_creds(
        refresh_token=old_refresh,
        expires_at_ms=int((time.time() + 60) * 1000),  # scade fra 1 minuto
    )))

    new_refresh = "sk-ant-ort01-newRotatedRefreshToken"
    mock_response_data = {
        "access_token": "sk-ant-oat01-newToken",
        "refresh_token": new_refresh,
        "expires_in": 25200,
    }

    def mock_urlopen(req, *args, **kwargs):
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    monkeypatch.setattr(res_mod.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(res_mod, "CRED_FILE", cred_file)

    resilience = res_mod.Resilience(port=8787, log_fn=lambda m: None)
    success, msg = resilience.try_refresh_oauth()

    assert success
    creds = json.loads(cred_file.read_text())
    assert creds["claudeAiOauth"]["refreshToken"] == new_refresh
    assert creds["claudeAiOauth"]["refreshToken"] != old_refresh


def test_refresh_token_scaduto_ritorna_false(tmp_path, monkeypatch):
    """Test 3: refresh token scaduto → ritorna False, file intatto."""
    res_mod = _import_res()

    cred_file = tmp_path / ".credentials.json"
    creds_orig = _make_creds(
        # refreshToken scaduto nel passato
        refresh_token_expires_at_ms=int((time.time() - 3600) * 1000)
    )
    cred_file.write_text(json.dumps(creds_orig))
    orig_content = cred_file.read_text()

    monkeypatch.setattr(res_mod, "CRED_FILE", cred_file)

    resilience = res_mod.Resilience(port=8787, log_fn=lambda m: None)
    success, msg = resilience.try_refresh_oauth()

    # Deve fallire
    assert not success
    assert "scaduto" in msg.lower() or "refresh token" in msg.lower()

    # File deve restare identico byte per byte
    assert cred_file.read_text() == orig_content


def test_eccezione_http_file_intatto(tmp_path, monkeypatch):
    """Test 4: eccezione HTTP → ritorna False, file intatto."""
    res_mod = _import_res()

    cred_file = tmp_path / ".credentials.json"
    creds_orig = _make_creds()
    cred_file.write_text(json.dumps(creds_orig))
    orig_content = cred_file.read_text()

    def mock_urlopen_fail(req, *args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://example.com",
            code=500,
            msg="Server error",
            hdrs={},
            fp=None
        )

    monkeypatch.setattr(res_mod.urllib.request, "urlopen", mock_urlopen_fail)
    monkeypatch.setattr(res_mod, "CRED_FILE", cred_file)

    resilience = res_mod.Resilience(port=8787, log_fn=lambda m: None)
    success, msg = resilience.try_refresh_oauth()

    # Deve fallire senza sollevare eccezione
    assert not success
    assert "500" in msg  # lo status HTTP deve essere nel messaggio

    # File intatto
    assert cred_file.read_text() == orig_content


def test_race_condition_cli_rinnova_nel_mezzo(tmp_path, monkeypatch):
    """Test 5: Race condition — fra read e write il CLI rinnova.

    Simula che il file venga aggiornato con un expiresAt futuro
    fra il momento della richiesta HTTP e il momento della scrittura.
    Il metodo deve rilevarlo e ritornare True senza sovrascrivere.
    """
    res_mod = _import_res()

    cred_file = tmp_path / ".credentials.json"
    creds_orig = _make_creds(
        access_token="sk-ant-oat01-oldToken",
        expires_at_ms=int((time.time() + 1) * 1000),  # scade fra poco
    )
    cred_file.write_text(json.dumps(creds_orig))

    # Questo flag simula se il CLI ha gia' scritto il file
    cli_has_renewed = False

    def mock_urlopen(req, *args, **kwargs):
        # Simula che il CLI aggiorni il file DURANTE la risposta HTTP
        nonlocal cli_has_renewed
        cli_has_renewed = True
        creds_renewed = _make_creds(
            access_token="sk-ant-oat01-cliRenewedToken",
            expires_at_ms=int((time.time() + 8 * 3600) * 1000),  # CLI ha rinnovato a 8h
        )
        cred_file.write_text(json.dumps(creds_renewed))

        # Ritorna la risposta del nostro refresh
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "access_token": "sk-ant-oat01-ourNewToken",
            "refresh_token": "sk-ant-ort01-ourNewToken",
            "expires_in": 25200,
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    monkeypatch.setattr(res_mod.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(res_mod, "CRED_FILE", cred_file)

    resilience = res_mod.Resilience(port=8787, log_fn=lambda m: None)
    success, msg = resilience.try_refresh_oauth()

    # Deve riportare successo (perché il CLI ha gia' rinnovato)
    assert success
    assert "gia' rinnovato" in msg.lower()

    # Il file deve contenere il token del CLI, NON il nostro (race detection ha funzionato)
    creds_final = json.loads(cred_file.read_text())
    assert creds_final["claudeAiOauth"]["accessToken"] == "sk-ant-oat01-cliRenewedToken"


def test_sicurezza_niente_token_nei_log(tmp_path, monkeypatch, caplog):
    """Test 6: SICUREZZA — il token non compare mai nei log."""
    res_mod = _import_res()

    cred_file = tmp_path / ".credentials.json"
    fake_access = "sk-ant-oat01-SEGRETO-QUESTO-DEVE-RIMANERE-NASCOSTO"
    fake_refresh = "sk-ant-ort01-REFRESH-SEGRETO-NASCOSTO"

    cred_file.write_text(json.dumps(_make_creds(
        access_token=fake_access,
        refresh_token=fake_refresh,
    )))

    mock_response_data = {
        "access_token": "sk-ant-oat01-newToken",
        "refresh_token": "sk-ant-ort01-newRefresh",
        "expires_in": 25200,
    }

    def mock_urlopen(req, *args, **kwargs):
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    monkeypatch.setattr(res_mod.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(res_mod, "CRED_FILE", cred_file)

    # Cattura i log
    logged_messages = []
    def capture_log(msg):
        logged_messages.append(msg)

    resilience = res_mod.Resilience(port=8787, log_fn=capture_log)
    success, msg = resilience.try_refresh_oauth()

    assert success

    # Verifica che NESSUN messaggio di log contiene i token
    all_logs = "\n".join(logged_messages)
    assert fake_access not in all_logs, \
        f"access token trovato nei log: {all_logs}"
    assert fake_refresh not in all_logs, \
        f"refresh token trovato nei log: {all_logs}"
    # Verifica anche parte dei token (non solo completi)
    assert "SEGRETO-QUESTO" not in all_logs
    assert "REFRESH-SEGRETO" not in all_logs


def test_user_agent_presente_nella_richiesta(tmp_path, monkeypatch):
    """Lo User-Agent NON è decorativo: senza, il token endpoint risponde 403.

    Misurato il 2026-08-18 contro l'endpoint reale: la stessa identica richiesta
    dà 403 con lo UA di default di urllib ("Python-urllib/3.x") e 200 con quello
    del CLI. Il filtro è a monte, prima che venga guardato il payload — quindi un
    403 qui non significa "credenziali sbagliate" ma "richiesta rifiutata al varco".
    """
    res_mod = _import_res()
    cred_file = tmp_path / ".credentials.json"
    cred_file.write_text(json.dumps(_make_creds()))
    monkeypatch.setattr(res_mod, "CRED_FILE", cred_file)

    visti = {}

    def mock_urlopen(req, *args, **kwargs):
        visti["ua"] = req.get_header("User-agent") or ""
        return mock.MagicMock(
            __enter__=lambda s: s, __exit__=lambda *a: None, status=200,
            read=lambda: json.dumps({
                "access_token": "sk-ant-oat01-nuovo",
                "refresh_token": "sk-ant-ort01-nuovo",
                "expires_in": 28800,
            }).encode(),
        )

    monkeypatch.setattr(res_mod.urllib.request, "urlopen", mock_urlopen)
    ok, _ = res_mod.Resilience(port=0, log_fn=lambda m: None).try_refresh_oauth()

    assert ok, "il refresh doveva riuscire con il mock"
    assert visti.get("ua"), "nessuno User-Agent inviato: l'endpoint risponderebbe 403"
    assert "python-urllib" not in visti["ua"].lower(), (
        f"User-Agent di default di urllib: viene rifiutato con 403 (era {visti['ua']!r})"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
