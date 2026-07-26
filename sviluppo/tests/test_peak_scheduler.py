import sys
sys.path.insert(0, 'src')
import peak_scheduler as ps

# Salva il datetime originale del modulo per poterlo ripristinare
saved_datetime = ps.datetime

def run_with_hour(hour, func, *args, **kwargs):
    """Esegue `func` con l'orario simulato `hour` (0-23)."""
    original = ps.datetime
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
    ps.datetime = FakeDatetime
    try:
        return func(*args, **kwargs)
    finally:
        ps.datetime = original

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
    risultato_peak = run_with_hour(15, ps.cost_multiplier, 'glm-5.2')
    assert risultato_peak == 3.0, \
        f"cost_multiplier('glm-5.2') a 15: atteso 3.0, ottenuto {risultato_peak}"
    # Ore 9 -> fuori peak
    risultato_off = run_with_hour(9, ps.cost_multiplier, 'glm-5.2')
    assert risultato_off == 1.0, \
        f"cost_multiplier('glm-5.2') a 9: atteso 1.0, ottenuto {risultato_off}"

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
    print('='*60)
    print('TUTTI I TEST PASSATI')

if __name__ == '__main__':
    main()
