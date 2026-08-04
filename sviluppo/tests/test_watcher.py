import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from self_healing.watcher import OutcomeLearner, process_file, load_state, save_state

def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

def test_process_file_aggregates(tmp_path):
    jsonl = tmp_path / "aggregates.jsonl"
    entries = (
        [{"outcome": "ok", "final": "glm-5.2", "task_class": "coding", "ts": i} for i in range(1, 4)]
        + [{"outcome": "empty", "final": "glm-5.2", "task_class": "coding", "ts": i} for i in range(4, 6)]
        + [{"outcome": "ok", "final": "minimax", "task_class": "chat", "ts": 6}]
    )
    _write_jsonl(jsonl, entries)
    learner = OutcomeLearner(alpha=0.1, half_life_seconds=200)
    process_file(str(jsonl), learner, 0)  # il valore di ritorno non serve a questo test
    coding_stats = learner._stats["glm-5.2|coding"]
    assert coding_stats["total"] == 5, f"expected total 5, got {coding_stats['total']}"
    assert coding_stats["fails"] == 2, f"expected fails 2, got {coding_stats['fails']}"
    assert coding_stats["ewma"] > 0, f"expected ewma > 0, got {coding_stats['ewma']}"
    chat_stats = learner._stats["minimax|chat"]
    assert chat_stats["total"] == 1, f"expected total 1, got {chat_stats['total']}"

def test_process_file_offset_advances(tmp_path):
    jsonl = tmp_path / "offset.jsonl"
    entries = [{"outcome": "ok", "final": "a", "task_class": "b", "ts": i} for i in range(3)]
    _write_jsonl(jsonl, entries)
    learner = OutcomeLearner(alpha=0.1, half_life_seconds=100)
    off1 = process_file(str(jsonl), learner, 0)
    file_size = jsonl.stat().st_size
    assert off1 > 0, "offset should be positive"
    assert off1 == file_size, f"expected offset {file_size}, got {off1}"
    off2 = process_file(str(jsonl), learner, off1)
    assert off2 == off1, f"offset should not change, got {off2}"
    total = sum(v["total"] for v in learner._stats.values())
    assert total == 3, f"expected total 3, got {total}"

def test_process_file_appends_new(tmp_path):
    jsonl = tmp_path / "append.jsonl"
    initial = [{"outcome": "ok", "final": "x", "task_class": "y", "ts": i} for i in range(3)]
    _write_jsonl(jsonl, initial)
    learner = OutcomeLearner(alpha=0.1, half_life_seconds=100)
    off1 = process_file(str(jsonl), learner, 0)
    extra = [{"outcome": "error", "final": "x", "task_class": "y", "ts": i} for i in range(3, 5)]
    with open(jsonl, "a", encoding="utf-8") as f:
        for e in extra:
            f.write(json.dumps(e) + "\n")
    off2 = process_file(str(jsonl), learner, off1)
    total = sum(v["total"] for v in learner._stats.values())
    assert total == 5, f"expected total 5, got {total}"
    assert off2 > off1, f"expected off2 > off1, got off2={off2}, off1={off1}"

def test_process_file_skips_malformed(tmp_path):
    jsonl = tmp_path / "malformed.jsonl"
    with open(jsonl, "w", encoding="utf-8") as f:
        f.write(json.dumps({"outcome": "ok", "final": "a", "task_class": "b", "ts": 1}) + "\n")
        f.write("not json{\n")
        f.write(json.dumps({"outcome": "error", "final": "a", "task_class": "b", "ts": 2}) + "\n")
    learner = OutcomeLearner(alpha=0.1, half_life_seconds=100)
    process_file(str(jsonl), learner, 0)  # il valore di ritorno non serve a questo test
    total = sum(v["total"] for v in learner._stats.values())
    assert total == 2, f"expected total 2, got {total}"

def test_save_load_roundtrip(tmp_path):
    state_file = tmp_path / "state.json"
    jsonl = tmp_path / "fails.jsonl"
    entries = [{"outcome": "error", "final": "m", "task_class": "t", "ts": i} for i in range(5)]
    _write_jsonl(jsonl, entries)
    learner = OutcomeLearner(alpha=0.3, half_life_seconds=500)
    process_file(str(jsonl), learner, 0)
    save_state(str(state_file), learner, 123)
    learner2, off = load_state(str(state_file))
    assert off == 123, f"expected offset 123, got {off}"
    assert learner2.alpha == 0.3, f"expected alpha 0.3, got {learner2.alpha}"
    assert learner2.half_life_seconds == 500, f"expected half_life_seconds 500, got {learner2.half_life_seconds}"
    stats = learner2._stats["m|t"]
    assert stats["total"] == 5, f"expected total 5, got {stats['total']}"
    assert stats["fails"] == 5, f"expected fails 5, got {stats['fails']}"
    original_ewma = learner._stats["m|t"]["ewma"]
    loaded_ewma = stats["ewma"]
    assert abs(loaded_ewma - original_ewma) < 1e-9, f"ewma mismatch: {loaded_ewma} vs {original_ewma}"

def test_file_rotation_resets_offset(tmp_path):
    jsonl = tmp_path / "rotation.jsonl"
    initial = [{"outcome": "ok", "final": "r", "task_class": "s", "ts": i} for i in range(3)]
    _write_jsonl(jsonl, initial)
    learner = OutcomeLearner(alpha=0.1, half_life_seconds=100)
    off1 = process_file(str(jsonl), learner, 0)
    assert off1 == jsonl.stat().st_size, f"expected off1 == size, got off1={off1}"
    rotated = [{"outcome": "error", "final": "r", "task_class": "s", "ts": i} for i in range(3, 5)]
    _write_jsonl(jsonl, rotated)
    off2 = process_file(str(jsonl), learner, off1)
    assert off2 < off1, f"expected off2 < off1, got off2={off2}, off1={off1}"
    total = sum(v["total"] for v in learner._stats.values())
    # learner cumulativo: 3 iniziali + 2 dopo rotazione = 5 (la rotazione resetta
    # l'offset del file, NON le stats dell'learner)
    assert total == 5, f"expected total 5 (cumulativo), got {total}"
    fails = sum(v["fails"] for v in learner._stats.values())
    assert fails == 2, f"expected fails 2, got {fails}"

def test_decay_lowers_ewma(tmp_path):
    learner = OutcomeLearner(alpha=0.5, half_life_seconds=100)
    learner.observe("x", "y", "error", 0)
    ewma1 = learner._stats["x|y"]["ewma"]
    learner.observe("x", "y", "ok", 100)
    ewma2 = learner._stats["x|y"]["ewma"]
    assert ewma2 < ewma1, f"ewma2 ({ewma2}) should be lower than ewma1 ({ewma1})"
    assert ewma2 < 0.5, f"ewma2 ({ewma2}) should be less than 0.5"


# vivevano nel blocco if __name__ == "__main__" di src/self_healing/watcher.py, che il 2026-08-04 è stato sostituito dalla chiamata a main() -- il self-test aveva preso il posto dell'entry point e rendeva la CLI irraggiungibile. Spostati qui per non perdere copertura.

def test_ewma_satura_dopo_dieci_fallimenti():
    learner = OutcomeLearner(alpha=0.5, half_life_seconds=1000)
    for i in range(10):
        learner.observe("glm-5.2", "coding", "error", 1000.0 + i)
    ewma = learner._stats["glm-5.2|coding"]["ewma"]
    assert ewma > 0.9, f"ewma ({ewma}) should be greater than 0.9 after 10 errors"

def test_ewma_scende_dopo_dieci_successi():
    learner = OutcomeLearner(alpha=0.5, half_life_seconds=1000)
    for i in range(10):
        learner.observe("glm-5.2", "coding", "error", 1000.0 + i)
    for i in range(10):
        learner.observe("glm-5.2", "coding", "ok", 1010.0 + i)
    ewma = learner._stats["glm-5.2|coding"]["ewma"]
    assert ewma < 0.5, f"ewma ({ewma}) should be less than 0.5 after 10 successes"

def test_ewma_di_chiave_nuova_vale_alpha():
    learner = OutcomeLearner(alpha=0.5, half_life_seconds=1000)
    learner.observe("minimax-m2.7", "coding", "error", 1020.0)
    ewma = learner._stats["minimax-m2.7|coding"]["ewma"]
    assert abs(ewma - 0.5) < 0.001, f"ewma ({ewma}) should be approximately 0.5 on first failure for unseen key"

def test_run_cycle_emette_la_policy(tmp_path):
    """Guardia anti-regressione: run_cycle DEVE scrivere il file della policy. Fino al 2026-08-04 follow() era una copia monca del ramo --once e non chiamava emit_policy, quindi in modalita continua router-policy.json non veniva MAI rigenerato: il servizio aggiornava router-learnings.json ogni 60 secondi mentre la policy restava congelata."""
    from self_healing.watcher import run_cycle
    jsonl = tmp_path / "usage.jsonl"
    entries = [{"outcome": "error", "final": "modello-x", "task_class": "coding", "ts": n} for n in range(1, 7)]
    _write_jsonl(jsonl, entries)
    state = tmp_path / "learnings.json"
    policy = tmp_path / "policy.json"
    run_cycle(jsonl, state, policy)
    assert policy.exists(), "run_cycle non ha scritto la policy"
    dati = json.loads(policy.read_text())
    assert "degraded" in dati, f"degraded non presente in {dati.keys()}"
    assert "modello-x" in dati["degraded"], f"modello-x non in degraded: {dati['degraded'].keys()}"
    assert state.exists(), "run_cycle non ha scritto lo state"

def test_run_cycle_ritorna_il_learner(tmp_path):
    """run_cycle restituisce il learner, perche il ramo --once di main() lo usa per stampare il riepilogo."""
    from self_healing.watcher import run_cycle
    jsonl = tmp_path / "usage.jsonl"
    entries = [
        {"outcome": "ok", "final": "m", "task_class": "chat", "ts": 1},
        {"outcome": "ok", "final": "m", "task_class": "chat", "ts": 2}
    ]
    _write_jsonl(jsonl, entries)
    learner = run_cycle(jsonl, tmp_path / "s.json", tmp_path / "p.json")
    assert learner is not None, "run_cycle ha restituito None"
    assert hasattr(learner, "_stats"), f"learner non ha _stats: {dir(learner)}"
