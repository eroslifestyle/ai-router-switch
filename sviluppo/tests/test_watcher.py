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
