import sys
sys.path.insert(0, 'src')
import peak_scheduler as ps

# Salva il datetime originale del modulo per poterlo ripristinare
saved_datetime = ps.datetime

def run_with_hour(hour, func, *args, modulo=None, **kwargs):
    """Esegue `func` con l'orario simulato `hour` (0-23).
    
    Il parametro `modulo` permette di iniettare l'ora in un modulo peak_scheduler
    specifico. Utile quando la funzione sotto test risolve peak_scheduler con
    import lazy (es. glm_backend), ottenendo un'istanza diversa da quella
    importata in testa a questo file, dato che il conftest della suite purga
    sys.modules dei moduli di src/ tra un test e l'altro.
    """
    target = modulo if modulo is not None else ps
    original = target.datetime
    # Definiamo una classe fake il cui metodo now() restituisce
    # un oggetto con attributo hour pari a `hour`.
    class FakeDatetime:
        _hour = hour
        @classmethod
        def now(cls, tz=None):
            # Restituisce un oggetto fittizio con solo l'attributo hour
            class MockTime:
                def __init__(self, h):
                    self.hour = h
                def __repr__(self):
                    return f"MockTime(hour={self.hour})"
            return MockTime(cls._hour)
    target.datetime = FakeDatetime
    try:
        return func(*args, **kwargs)
    finally:
        target.datetime = original

def test_bordi_fascia_peak():
    """Verifica i quattro bordi della fascia peak."""
    # (ora, valore atteso di is_peak_hour)
    bordi = [(13, False), (14, True), (17, True), (18, False)]
    for ora, atteso in bordi:
        risultato = run_with_hour(ora, ps.is_peak_hour)
        assert risultato is atteso, \
            f"Ora {ora}: is_peak_hour dovrebbe essere {atteso}, ottenuto {risultato}"

def test_blocco_modelli_costosi_in_peak():
    """Verifica should_block_glm_model in fascia peak e fuori."""
    # Ore 15 (peak)
    for tier, atteso in [('TOP', True), ('TURBO', True), ('CHEAP', False)]:
        risultato = run_with_hour(15, ps.should_block_glm_model, tier)
        assert risultato is atteso, \
            f"should_block_glm_model('{tier}') a 15: atteso {atteso}, ottenuto {risultato}"
    # Ore 9 (fuori peak)
    for tier in ['TOP', 'TURBO', 'CHEAP']:
        risultato = run_with_hour(9, ps.should_block_glm_model, tier)
        assert risultato is False, \
            f"should_block_glm_model('{tier}') a 9: atteso False, ottenuto {risultato}"

def test_moltiplicatore_costo():
    """Verifica cost_multiplier in peak e fuori."""
    # Ore 15 -> peak
    risultato_peak = run_with_hour(15, ps.cost_multiplier, 'glm-5.3')
    assert risultato_peak == 3.0, \
        f"cost_multiplier('glm-5.3') a 15: atteso 3.0, ottenuto {risultato_peak}"
    # Ore 9 -> fuori peak
    risultato_off = run_with_hour(9, ps.cost_multiplier, 'glm-5.3')
    assert risultato_off == 1.0, \
        f"cost_multiplier('glm-5.3') a 9: atteso 1.0, ottenuto {risultato_off}"

def test_scheduling_status_coerente():
    """Verifica che scheduling_status()['peak_active'] sia coerente."""
    status_peak = run_with_hour(15, ps.scheduling_status)
    assert status_peak['peak_active'] is True, \
        f"scheduling_status()['peak_active'] a 15: atteso True, ottenuto {status_peak['peak_active']}"
    status_off = run_with_hour(9, ps.scheduling_status)
    assert status_off['peak_active'] is False, \
        f"scheduling_status()['peak_active'] a 9: atteso False, ottenuto {status_off['peak_active']}"

def test_ripristino_datetime():
    """Verifica che ps.datetime venga ripristinato correttamente."""
    # Esegue un'operazione dummy con l'helper
    run_with_hour(10, lambda: None)
    assert ps.datetime is saved_datetime, \
        "ps.datetime non è stato ripristinato al valore originale dopo run_with_hour"

def test_peak_cap_declassa_modello_costoso_in_peak():
    """Il cap in fascia peak declassa il modello costoso a glm-4.7. E il caso REALE dal 2026-07-25: il proxy riceve da role_routing un NOME MODELLO (glm-5.3 per il THINK), non una tier key."""
    import glm_backend as gb
    import importlib
    ps_vivo = importlib.import_module("peak_scheduler")  # la STESSA istanza che apply_peak_cap risolvera con import lazy
    # Ora in fascia peak (15:00)
    risultato = run_with_hour(15, gb.apply_peak_cap, "glm-5.3", modulo=ps_vivo)
    assert risultato == ("glm-4.7", True), \
        f"apply_peak_cap('glm-5.3') a 15h: atteso ('glm-4.7', True), ottenuto {risultato}"
    risultato = run_with_hour(15, gb.apply_peak_cap, "glm-5-turbo", modulo=ps_vivo)
    assert risultato == ("glm-4.7", True), \
        f"apply_peak_cap('glm-5-turbo') a 15h: atteso ('glm-4.7', True), ottenuto {risultato}"
    risultato = run_with_hour(15, gb.apply_peak_cap, "glm-4.7", modulo=ps_vivo)
    assert risultato == ("glm-4.7", False), \
        f"apply_peak_cap('glm-4.7') a 15h: atteso ('glm-4.7', False), ottenuto {risultato}"
    risultato = run_with_hour(15, gb.apply_peak_cap, "glm-4.6V", modulo=ps_vivo)
    assert risultato == ("glm-4.6V", False), \
        f"apply_peak_cap('glm-4.6V') a 15h: atteso ('glm-4.6V', False), ottenuto {risultato}"
    # Ora fuori fascia peak (9:00)
    risultato = run_with_hour(9, gb.apply_peak_cap, "glm-5.3", modulo=ps_vivo)
    assert risultato == ("glm-5.3", False), \
        f"apply_peak_cap('glm-5.3') a 9h: atteso ('glm-5.3', False), ottenuto {risultato}"
    risultato = run_with_hour(9, gb.apply_peak_cap, "glm-5-turbo", modulo=ps_vivo)
    assert risultato == ("glm-5-turbo", False), \
        f"apply_peak_cap('glm-5-turbo') a 9h: atteso ('glm-5-turbo', False), ottenuto {risultato}"

def test_peak_cap_retrocompatibile_con_tier_key():
    """Le tier key continuano a funzionare come prima: il cap accetta entrambi i domini e risponde nel dominio dellinput."""
    import glm_backend as gb
    import importlib
    ps_vivo = importlib.import_module("peak_scheduler")  # la STESSA istanza che apply_peak_cap risolvera con import lazy
    # Ora in fascia peak (15:00)
    risultato = run_with_hour(15, gb.apply_peak_cap, "TOP", modulo=ps_vivo)
    assert risultato == ("MID", True), \
        f"apply_peak_cap('TOP') a 15h: atteso ('MID', True), ottenuto {risultato}"
    risultato = run_with_hour(15, gb.apply_peak_cap, "TURBO", modulo=ps_vivo)
    assert risultato == ("MID", True), \
        f"apply_peak_cap('TURBO') a 15h: atteso ('MID', True), ottenuto {risultato}"
    risultato = run_with_hour(15, gb.apply_peak_cap, "MID", modulo=ps_vivo)
    assert risultato == ("MID", False), \
        f"apply_peak_cap('MID') a 15h: atteso ('MID', False), ottenuto {risultato}"
    risultato = run_with_hour(15, gb.apply_peak_cap, "VISION", modulo=ps_vivo)
    assert risultato == ("VISION", False), \
        f"apply_peak_cap('VISION') a 15h: atteso ('VISION', False), ottenuto {risultato}"
    # Ora fuori fascia peak (9:00)
def test_peak_cap_collegato_nel_proxy():
    """Guardia anti-regressione: il ramo GLM del proxy DEVE chiamare apply_peak_cap.
    Fino al 2026-08-04 la funzione esisteva, era corretta e testata, ma non la chiamava nessuno:
    il cap era documentato come attivo e invece era inerte.
    Questo test fallisce se qualcuno lo scollega di nuovo.
    """
    from pathlib import Path
    proxy_path = Path(__file__).resolve().parents[2] / "src" / "ai-router-proxy.py"
    assert proxy_path.exists(), f"proxy non trovato: {proxy_path}"
    source = proxy_path.read_text(encoding="utf-8")
    assert "apply_peak_cap(" in source, \
        "il ramo GLM del proxy non chiama più apply_peak_cap: il cap peak è tornato inerte"
    assert "_peak_capped" in source, \
        "il risultato del cap peak non viene usato: _peak_capped assente nel sorgente"

def main():
    print('='*60)
    test_bordi_fascia_peak()
    print('  test_bordi_fascia_peak: OK')
    test_blocco_modelli_costosi_in_peak()
    print('  test_blocco_modelli_costosi_in_peak: OK')
    test_moltiplicatore_costo()
    print('  test_moltiplicatore_costo: OK')
    test_scheduling_status_coerente()
    print('  test_scheduling_status_coerente: OK')
    test_ripristino_datetime()
    print('  test_ripristino_datetime: OK')
    test_peak_cap_declassa_modello_costoso_in_peak()
    print('  test_peak_cap_declassa_modello_costoso_in_peak: OK')
    test_peak_cap_retrocompatibile_con_tier_key()
    print('  test_peak_cap_retrocompatibile_con_tier_key: OK')
    test_peak_cap_collegato_nel_proxy()
    print('  test_peak_cap_collegato_nel_proxy: OK')
    print('='*60)
    print('TUTTI I TEST PASSATI')

if __name__ == '__main__':
    main()
