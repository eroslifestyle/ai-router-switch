import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from self_healing import fixer


def test_state_roundtrip(tmp_path, monkeypatch):
    """Verifica che lo stato venga salvato e caricato correttamente."""
    monkeypatch.setattr(fixer, 'STATE_DIR', tmp_path)
    ticket_id = "test_ticket"
    
    # Carica stato fresco
    initial_state = fixer.load_state(ticket_id)
    assert initial_state["attempts"] == 0
    assert initial_state["history"] == []
    
    # Modifica e salva lo stato
    initial_state["attempts"] = 1
    initial_state["history"].append("prova")
    fixer.save_state(ticket_id, initial_state)
    
    # Ricarica e verifica coerenza
    reloaded_state = fixer.load_state(ticket_id)
    assert reloaded_state["attempts"] == 1
    assert reloaded_state["history"] == ["prova"]


def test_state_corrotto_ritorna_fresh(tmp_path, monkeypatch):
    """Verifica che uno stato corrotto ritorni uno stato fresco."""
    monkeypatch.setattr(fixer, 'STATE_DIR', tmp_path)
    ticket_id = "corrupted_ticket"
    
    # Crea un file JSON corrotto
    state_file = tmp_path / f"{ticket_id}.json"
    state_file.write_text("{non_valid_json}")
    
    # Carica stato - deve tornare uno stato fresco
    state = fixer.load_state(ticket_id)
    assert state["attempts"] == 0
    assert state["history"] == []


def test_router_busy_con_traffico_recente(tmp_path, monkeypatch):
    """Verifica che router_is_busy ritorni True se c'è traffico recente."""
    monkeypatch.setattr(fixer, 'USAGE_SIDECAR', tmp_path / "sidecar.log")
    log_file = tmp_path / "sidecar.log"
    
    now = time.time()
    recent_time = now - 10
    
    log_content = '{"ts": 12345.0}\n{"ts": %f}\n' % recent_time
    log_file.write_text(log_content)
    
    result = fixer.router_is_busy()
    assert result is True


def test_router_non_busy_con_traffico_vecchio(tmp_path, monkeypatch):
    """Verifica che router_is_busy ritorni False se il traffico è vecchio."""
    monkeypatch.setattr(fixer, 'USAGE_SIDECAR', tmp_path / "sidecar.log")
    log_file = tmp_path / "sidecar.log"
    
    old_time = time.time() - 3600
    
    log_content = '{"ts": 12345.0}\n{"ts": %f}\n' % old_time
    log_file.write_text(log_content)
    
    result = fixer.router_is_busy()
    assert result is False


def test_router_non_busy_senza_sidecar(tmp_path, monkeypatch):
    """Verifica che router_is_busy ritorni False se il file sidecar non esiste."""
    monkeypatch.setattr(fixer, 'USAGE_SIDECAR', tmp_path / "sidecar.log")
    
    result = fixer.router_is_busy()
    assert result is False


def test_forbidden_self_healing():
    """Verifica che i file in src/self_healing siano considerati vietati."""
    changed_files = ["src/self_healing/watcher.py"]
    result = fixer._touches_forbidden(changed_files)
    assert result is True


def test_forbidden_policy_e_unit():
    """Verifica che router_policy.py e unità systemd siano vietate."""
    # Test router_policy.py
    files1 = ["src/router_policy.py"]
    assert fixer._touches_forbidden(files1) is True
    
    # Test .service in sviluppo/systemd/
    files2 = ["sviluppo/systemd/my.service"]
    assert fixer._touches_forbidden(files2) is True
    
    # Test .timer ovunque
    files3 = ["altro/mio.timer"]
    assert fixer._touches_forbidden(files3) is True


def test_forbidden_traversal():
    """Verifica che i percorsi con traversal siano gestiti correttamente."""
    changed_files = ["../src/self_healing/x.py"]
    result = fixer._touches_forbidden(changed_files)
    assert result is True


def test_path_lecito():
    """Verifica che i percorsi leciti non siano considerati vietati."""
    changed_files = ["src/forward_minimax.py", "sviluppo/tests/test_x.py"]
    result = fixer._touches_forbidden(changed_files)
    assert result is False


def test_pick_ticket_scelto_se_pulito(tmp_path, monkeypatch):
    """Verifica che un ticket pulito venga scelto."""
    monkeypatch.setattr(fixer, 'STATE_DIR', tmp_path)
    def mock_load_catalog():
        return [{}]
        
    def mock_recurring_bugs(catalog, min_count):
        return [{"id": "test_id", "kind": "bug", "symptom": "test", "count": 5, 
                 "mode": "auto", "suspected_files": ["src/main.py"], 
                 "reproduction": "steps", "branch": "main", "status": "open"}]
    
    def mock_make_ticket(bug):
        return bug
    
    monkeypatch.setattr(fixer, 'load_bug_catalog', mock_load_catalog)
    monkeypatch.setattr(fixer, 'recurring_bugs', mock_recurring_bugs)
    monkeypatch.setattr(fixer, 'make_ticket', mock_make_ticket)
    
    ticket, reason = fixer.pick_ticket(5)
    assert ticket is not None
    assert ticket["id"] == "test_id"


def test_pick_ticket_salta_max_tentativi(tmp_path, monkeypatch):
    """Verifica che un ticket con troppi tentativi venga saltato."""
    def mock_load_catalog():
        return [{}]
        
    def mock_recurring_bugs(catalog, min_count):
        return [{"id": "test_id", "kind": "bug", "symptom": "test", "count": 5, 
                 "mode": "auto", "suspected_files": ["src/main.py"], 
                 "reproduction": "steps", "branch": "main", "status": "open"}]
    
    def mock_make_ticket(bug):
        return bug
    
    monkeypatch.setattr(fixer, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(fixer, 'load_bug_catalog', mock_load_catalog)
    monkeypatch.setattr(fixer, 'recurring_bugs', mock_recurring_bugs)
    monkeypatch.setattr(fixer, 'make_ticket', mock_make_ticket)
    
    # Salva stato con tentativi massimi raggiunti
    state = {"id": "test_id", "attempts": 2, "last_ts": 0, "last_outcome": "", "history": []}
    fixer.save_state("test_id", state)
    
    ticket, reason = fixer.pick_ticket(5)
    assert ticket is None
    assert "max_attempts" in reason


def test_pick_ticket_salta_cooldown(tmp_path, monkeypatch):
    """Verifica che un ticket in cooldown venga saltato."""
    def mock_load_catalog():
        return [{}]
        
    def mock_recurring_bugs(catalog, min_count):
        return [{"id": "test_id", "kind": "bug", "symptom": "test", "count": 5, 
                 "mode": "auto", "suspected_files": ["src/main.py"], 
                 "reproduction": "steps", "branch": "main", "status": "open"}]
    
    def mock_make_ticket(bug):
        return bug
    
    monkeypatch.setattr(fixer, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(fixer, 'load_bug_catalog', mock_load_catalog)
    monkeypatch.setattr(fixer, 'recurring_bugs', mock_recurring_bugs)
    monkeypatch.setattr(fixer, 'make_ticket', mock_make_ticket)
    
    # Salva stato con ultimo tentativo recente
    current_time = time.time()
    state = {"id": "test_id", "attempts": 0, "last_ts": current_time, "last_outcome": "", "history": []}
    fixer.save_state("test_id", state)
    
    ticket, reason = fixer.pick_ticket(5)
    assert ticket is None
    assert "cooldown" in reason


def test_pick_ticket_salta_zona_proibita(tmp_path, monkeypatch):
    """Verifica che un ticket con file vietati venga saltato."""
    monkeypatch.setattr(fixer, 'STATE_DIR', tmp_path)
    def mock_load_catalog():
        return [{}]
        
    def mock_recurring_bugs(catalog, min_count):
        return [{"id": "test_id", "kind": "bug", "symptom": "test", "count": 5, 
                 "mode": "auto", "suspected_files": ["src/self_healing/watcher.py"], 
                 "reproduction": "steps", "branch": "main", "status": "open"}]
    
    def mock_make_ticket(bug):
        return bug
    
    monkeypatch.setattr(fixer, 'load_bug_catalog', mock_load_catalog)
    monkeypatch.setattr(fixer, 'recurring_bugs', mock_recurring_bugs)
    monkeypatch.setattr(fixer, 'make_ticket', mock_make_ticket)
    
    ticket, reason = fixer.pick_ticket(5)
    assert ticket is None
    assert "forbidden" in reason

