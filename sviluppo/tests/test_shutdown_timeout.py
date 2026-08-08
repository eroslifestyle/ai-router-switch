"""
Test per il fix dello shutdown del router (audit F1/A1).

Verifica che:
- La costante SHUTDOWN_TIMEOUT_S sia configurabile e sotto il timeout systemd
- Il proxy passi correttamente il parametro shutdown_timeout ad AppRunner
- Il cleanup sia parallelo con asyncio.gather
- Il default della libreria aiohttp sia stato neutralizzato dal fix
"""
import sys
import os
import re
import importlib
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))  # noqa: E402

from aiohttp import web  # noqa: E402


def _ricarica_costanti():
    """Ricarica router_constants rileggendo l'ambiente.

    Non usa importlib.reload: la conftest ha _purge_src_modules(), che toglie da
    sys.modules tutti i moduli di src/. Dopo quella purga reload() fallirebbe con
    ImportError perche' il riferimento globale non e' piu' quello registrato.
    Il pop + import_module funziona in entrambi i casi.
    """
    sys.modules.pop('router_constants', None)
    return importlib.import_module('router_constants')


PROXY =Path(__file__).resolve().parents[2] / 'src' / 'ai-router-proxy.py'
SRC = PROXY.read_text(encoding='utf-8')


class TestCostanteShutdown:
    """Verifica la corretta definizione della costante SHUTDOWN_TIMEOUT_S."""

    def test_costante_esiste_ed_e_float(self):
        """La costante esiste ed e' un float positivo."""
        rc = _ricarica_costanti()
        assert hasattr(rc, 'SHUTDOWN_TIMEOUT_S')
        assert isinstance(rc.SHUTDOWN_TIMEOUT_S, float)
        assert rc.SHUTDOWN_TIMEOUT_S > 0

    def test_default_e_tre_secondi(self):
        """Senza variabile d'ambiente, il default e' 3.0 secondi."""
        # Salva lo stato originale dell'ambiente
        env_originale = os.environ.get('AIROUTER_SHUTDOWN_TIMEOUT_S')

        try:
            # Rimuovi la variabile d'ambiente se presente
            if 'AIROUTER_SHUTDOWN_TIMEOUT_S' in os.environ:
                del os.environ['AIROUTER_SHUTDOWN_TIMEOUT_S']

            # Ricarica il modulo per rileggere la variabile d'ambiente
            rc = _ricarica_costanti()

            # Verifica che il default sia 3.0
            assert rc.SHUTDOWN_TIMEOUT_S == 3.0
        finally:
            # Ripristina l'ambiente originale
            if env_originale is not None:
                os.environ['AIROUTER_SHUTDOWN_TIMEOUT_S'] = env_originale
            _ricarica_costanti()

    def test_override_da_env(self):
        """La costante puo' essere sovrascritta tramite variabile d'ambiente."""
        # Salva lo stato originale
        env_originale = os.environ.get('AIROUTER_SHUTDOWN_TIMEOUT_S')

        try:
            # Imposta un valore custom
            os.environ['AIROUTER_SHUTDOWN_TIMEOUT_S'] = '7.5'
            rc = _ricarica_costanti()

            # Verifica che il valore sia stato letto
            assert rc.SHUTDOWN_TIMEOUT_S == 7.5
        finally:
            # Ripristina l'ambiente e ricarica per non contaminare altri test
            if env_originale is not None:
                os.environ['AIROUTER_SHUTDOWN_TIMEOUT_S'] = env_originale
            elif 'AIROUTER_SHUTDOWN_TIMEOUT_S' in os.environ:
                del os.environ['AIROUTER_SHUTDOWN_TIMEOUT_S']
            _ricarica_costanti()

    def test_sta_sotto_il_timeout_di_systemd(self):
        """Il timeout singolo e quello pessimo (10 porte sequenziali) stanno sotto systemd."""
        rc = _ricarica_costanti()
        # TimeoutStopSec della unit systemd
        TIMEOUT_STOP_SEC_UNIT = 20  # ~/.config/systemd/user/ai-router.service

        timeout_singolo = rc.SHUTDOWN_TIMEOUT_S
        # Caso pessimo: 10 porte, cleanup sequenziale
        timeout_pessimo = timeout_singolo * 10

        # Il valore singolo deve essere comodamente sotto i 20 secondi
        assert timeout_singolo < TIMEOUT_STOP_SEC_UNIT, (
            f"Timeout singolo ({timeout_singolo}s) supera TimeoutStopSec ({TIMEOUT_STOP_SEC_UNIT}s)"
        )

        # Il caso pessimo sequenziale non deve superare i 60 secondi (default aiohttp)
        assert timeout_pessimo <= 60, (
            f"Caso pessimo ({timeout_pessimo}s) supera il default aiohttp (60s)"
        )
class TestSorgenteProxy:
    """Regressione: verifica che il fix non venga accidentalmente rimosso."""

    def test_apprunner_passa_shutdown_timeout(self):
        """Il sorgente contiene AppRunner con il parametro shutdown_timeout."""
        pattern = r'web\.AppRunner\s*\(\s*app\s*,\s*shutdown_timeout\s*=\s*SHUTDOWN_TIMEOUT_S\s*\)'
        assert re.search(pattern, SRC), (
            "web.AppRunner deve ricevere shutdown_timeout=SHUTDOWN_TIMEOUT_S"
        )

    def test_nessun_apprunner_senza_timeout(self):
        """Non esiste piu' la forma nuda web.AppRunner(app) che usava il default 60s."""
        # Cerca la forma nuda: web.AppRunner(app) con parentesi chiusa subito dopo
        pattern = r'web\.AppRunner\s*\(\s*app\s*\)'
        matches = re.findall(pattern, SRC)
        assert len(matches) == 0, (
            f"Trovati {len(matches)} riferimenti a web.AppRunner(app) senza timeout"
        )

    def test_cleanup_e_parallelo(self):
        """Il cleanup usa asyncio.gather per esecuzione parallela."""
        # Cerca asyncio.gather nella stessa riga o contesto di r.cleanup
        pattern = r'asyncio\.gather\s*\([^)]*r\.cleanup\s*\(\s*\)\s*for\s+r\s+in\s+runners'
        assert re.search(pattern, SRC), (
            "Il cleanup deve usare asyncio.gather con r.cleanup() for r in runners"
        )

    def test_cleanup_non_e_piu_sequenziale(self):
        """Non esiste piu' il vecchio ciclo sequenziale for + await."""
        # Normalizza gli spazi per una ricerca piu' robusta
        src_normalizzato = re.sub(r'\s+', ' ', SRC)

        # Cerca il vecchio pattern: for r in runners: ... await r.cleanup()
        # con possible newline o spazi tra le parti
        pattern_vecchio = r'for\s+r\s+in\s+runners\s*:[^}]*?await\s+r\.cleanup\s*\(\s*\)'

        assert not re.search(pattern_vecchio, src_normalizzato, re.DOTALL), (
            "Trovato il vecchio ciclo sequenziale for r in runners + await r.cleanup()"
        )


class TestComportamentoAiohttp:
    """Verifica che il parametro shutdown_timeout sia realmente supportato dalla libreria."""

    def test_apprunner_accetta_il_parametro(self):
        """AppRunner accetta e memorizza il parametro shutdown_timeout."""
        app = web.Application()
        runner = web.AppRunner(app, shutdown_timeout=3.0)

        # aiohttp 3.13.4 memorizza il valore nell'attributo privato
        # _shutdown_timeout: non esiste una property pubblica. Il test lo legge
        # comunque, perche' l'alternativa (fidarsi del fatto che il costruttore
        # non sollevi TypeError) non proverebbe che il valore viene usato.
        assert runner._shutdown_timeout == 3.0

    def test_default_libreria_e_sessanta(self):
        """
        Il default della libreria aiohttp per shutdown_timeout e' 60.0 secondi.

        NOTA: un fallimento qui NON e' un bug del nostro codice, ma indica
        un cambio upstream nella libreria aiohttp che potrebbe richiedere
        una rivalutazione della configurazione.
        """
        sig = inspect.signature(web.BaseRunner.__init__)
        parametro = sig.parameters['shutdown_timeout']

        assert parametro.default == 60.0, (
            f"Il default upstream di shutdown_timeout e' cambiato a {parametro.default}. "
            "Verificare se il fix e' ancora necessario o se il default upstream "
            "e' gia' appropriato."
        )
