"""Test per src/paths.py"""

import sys
import tempfile
from pathlib import Path

import pytest

# Path assoluto e non sys.path.insert(0, "src"): quest'ultimo dipende dalla cwd
# e il modulo non si trova quando pytest parte da sviluppo/tests/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import paths  # noqa: E402  - l'import deve seguire l'aggiunta al sys.path


@pytest.fixture(autouse=True)
def reset():
    """Resetta la cache prima e dopo ogni test."""
    paths.reset_cache()
    yield
    paths.reset_cache()


class TestConfigHome:
    """Test per config_home()."""

    def test_airouter_home_ha_precedenza(self, monkeypatch, tmp_path):
        """AIROUTER_HOME ha precedenza assoluta su ~/.claude."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        (fake_home / ".claude").mkdir()
        monkeypatch.setenv("AIROUTER_HOME", str(tmp_path / "custom"))
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == tmp_path / "custom"

    def test_airouter_home_con_tilde(self, monkeypatch, tmp_path):
        """La tilde in AIROUTER_HOME viene espansa."""
        monkeypatch.setenv("AIROUTER_HOME", "~/airouter-test")
        # Path.expanduser() legge $HOME, non Path.home(): il solo monkeypatch
        # del metodo lascerebbe la tilde puntata alla home vera dell'utente.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = paths.config_home()
        assert result == tmp_path / "airouter-test"
        assert result.is_absolute()
        assert "~" not in str(result)

    def test_airouter_home_vuota_ignorata(self, monkeypatch, tmp_path):
        """La stringa vuota in AIROUTER_HOME viene ignorata."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        (fake_home / ".claude").mkdir()
        monkeypatch.setenv("AIROUTER_HOME", "")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == fake_home / ".claude"

    def test_usa_home_claude_se_esiste(self, monkeypatch, tmp_path):
        """Se ~/.claude esiste, viene usato come config_home."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        claude_dir = fake_home / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == claude_dir

    def test_linux_con_xdg_config_home(self, monkeypatch, tmp_path):
        """Su Linux con XDG_CONFIG_HOME impostata, viene usato."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        xdg = tmp_path / "xdg"
        xdg.mkdir()
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == xdg / "ai-router-switch"

    def test_linux_senza_xdg(self, monkeypatch, tmp_path):
        """Su Linux senza XDG_CONFIG_HOME usa ~/.config/ai-router-switch."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == fake_home / ".config" / "ai-router-switch"

    def test_macos(self, monkeypatch, tmp_path):
        """Su macOS usa ~/Library/Application Support/ai-router-switch."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == fake_home / "Library" / "Application Support" / "ai-router-switch"

    def test_windows_con_appdata(self, monkeypatch, tmp_path):
        """Su Windows con APPDATA impostata, usa APPDATA/ai-router-switch."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        appdata = tmp_path / "appdata"
        appdata.mkdir()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == appdata / "ai-router-switch"

    def test_windows_senza_appdata(self, monkeypatch, tmp_path):
        """Su Windows senza APPDATA usa ~/AppData/Roaming/ai-router-switch."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        assert paths.config_home() == fake_home / "AppData" / "Roaming" / "ai-router-switch"


class TestNoSideEffect:
    """Verifica che nessuna funzione crei directory al call."""

    def test_nessuna_funzione_crea_dir(self, monkeypatch, tmp_path):
        """Nessuna funzione crea directory: contratto no side effect."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        paths.config_home()
        paths.logs_dir()
        paths.secrets_dir()
        paths.state_dir()
        paths.credentials_file()
        paths.mode_file()
        paths.chat_store()
        paths.policy_file()
        paths.learnings_file()
        paths.secrets_script()
        paths.env_file()
        paths.log_file("x.log")
        # Nessuna directory deve esistere sul filesystem
        assert not (fake_home / ".claude").exists()
        assert not (fake_home / ".config" / "ai-router-switch").exists()


class TestEnsureDir:
    """Test per ensure_dir()."""

    def test_crea_directory_con_genitori(self, tmp_path):
        """ensure_dir crea davvero la directory con i genitori mancanti."""
        deep = tmp_path / "a" / "b" / "c"
        result = paths.ensure_dir(deep)
        assert result == deep
        assert deep.is_dir()

    def test_non_solleva_se_mkdir_fallisce(self, tmp_path, monkeypatch):
        """ensure_dir non solleva quando mkdir fallisce."""
        def mock_mkdir(self, *args, **kwargs):
            raise OSError("simulated failure")
        monkeypatch.setattr(Path, "mkdir", mock_mkdir)
        result = paths.ensure_dir(tmp_path / "nonexistent")
        assert result == tmp_path / "nonexistent"


class TestTrimStateDir:
    """Test per trim_state_dir()."""

    def test_rispetta_airouter_trim_dir(self, monkeypatch, tmp_path):
        """trim_state_dir rispetta AIROUTER_TRIM_DIR quando impostata."""
        custom = tmp_path / "custom_trim"
        monkeypatch.setenv("AIROUTER_TRIM_DIR", str(custom))
        assert paths.trim_state_dir() == custom

    def test_default_usa_tempfile(self, monkeypatch):
        """trim_state_dir senza env usa tempfile.gettempdir()."""
        result = paths.trim_state_dir()
        expected = Path(tempfile.gettempdir()) / "ai-router-trim"
        assert result == expected
        # Non e' cablato: deriva da tempfile.gettempdir()
        assert not str(result).startswith("/tmp/ai-router-trim") or \
            Path(tempfile.gettempdir()) == Path("/tmp")


class TestRelazioniParentela:
    """Verifica che tutte le funzioni derivate stiano sotto config_home()."""

    def test_derivati_sotto_config_home(self, monkeypatch, tmp_path):
        """Tutte le funzioni derivate stanno sotto config_home()."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        custom = tmp_path / "custom"
        custom.mkdir()
        monkeypatch.setenv("AIROUTER_HOME", str(custom))
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        ch = paths.config_home()
        assert paths.logs_dir().parent == ch
        assert paths.secrets_dir().parent == ch
        assert paths.state_dir().parent == ch
        assert paths.credentials_file().parent == ch
        assert paths.mode_file().parent == ch
        assert paths.chat_store().parent == ch
        assert paths.policy_file().parent == ch
        assert paths.learnings_file().parent == ch
        assert paths.secrets_script().parent == paths.secrets_dir()
        assert paths.secrets_dir().parent == ch
        assert paths.env_file().parent == ch
        assert paths.log_file("x.log").parent == paths.logs_dir()


class TestCostantiNomeFile:
    """Verifica che le costanti di nome file siano stringhe."""

    def test_nomi_file_sono_stringhe_non_path(self):
        """MODE_FILE_NAME e CHAT_STORE_NAME sono stringhe, non Path.
        Evita collisione con router_constants dove sono Path."""
        assert isinstance(paths.MODE_FILE_NAME, str)
        assert isinstance(paths.CHAT_STORE_NAME, str)
        assert not isinstance(paths.MODE_FILE_NAME, Path)
        assert not isinstance(paths.CHAT_STORE_NAME, Path)
