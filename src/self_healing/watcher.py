"""
Self-healing watcher module for learning router failure patterns.

Monitors router usage logs and maintains failure statistics per (model, task_class)
pair using EWMA with temporal decay, allowing models that stop failing to be
"forgiven" over time.
"""

import json
import os
import time
from argparse import ArgumentParser
from copy import deepcopy
from pathlib import Path
from typing import Optional

# Constants for default paths
DEFAULT_JSONL = Path.home() / ".claude" / "logs" / "router-usage.jsonl"
DEFAULT_STATE = Path.home() / ".claude" / "router-learnings.json"

# Constants for outcome classification
FAIL_OUTCOMES = frozenset({"empty", "truncated", "error"})
DEFAULT_MODEL = "?"
DEFAULT_TASK_CLASS = "?"


class OutcomeLearner:
    """
    Tracks failure statistics for (model, task_class) pairs using EWMA with temporal decay.

    The EWMA (Exponentially Weighted Moving Average) naturally decays over time
    when no events occur, allowing models that stop failing to be "forgiven" over time.
    """

    def __init__(self, alpha: float = 0.15, half_life_seconds: float = 3600) -> None:
        """
        Initialize the learner.

        Args:
            alpha: Weight given to new events in EWMA (0.15 = new event contributes 15%).
            half_life_seconds: Time for EWMA to halve without events (3600s = 1 hour).
        """
        self.alpha = alpha
        self.half_life_seconds = half_life_seconds
        self._stats: dict[str, dict] = {}

    def observe(self, model: Optional[str], task_class: Optional[str], outcome: str, ts: float) -> None:
        """
        Record an outcome event with EWMA update and temporal decay.

        Args:
            model: Model identifier.
            task_class: Task classification.
            outcome: Event outcome string.
            ts: Unix timestamp of the event.
        """
        key = f"{model or DEFAULT_MODEL}|{task_class or DEFAULT_TASK_CLASS}"
        is_fail = outcome in FAIL_OUTCOMES

        entry = self._stats.get(key, {"ewma": 0.0, "total": 0, "fails": 0, "last_ts": 0.0})

        # Temporal decay: reduce EWMA based on time elapsed since last event
        if entry["last_ts"] > 0:
            dt = max(0.0, ts - entry["last_ts"])
            factor = 0.5 ** (dt / self.half_life_seconds)
            entry["ewma"] *= factor

        # Update EWMA with new observation
        entry["ewma"] = self.alpha * (1.0 if is_fail else 0.0) + (1.0 - self.alpha) * entry["ewma"]

        entry["total"] += 1
        if is_fail:
            entry["fails"] += 1
        entry["last_ts"] = ts

        self._stats[key] = entry

    def snapshot(self) -> dict:
        """
        Return current state as a dictionary with metadata.

        Returns:
            Dict with 'updated', 'alpha', 'half_life_s', and deep-copied 'stats'.
        """
        last_ts = max((e["last_ts"] for e in self._stats.values()), default=0.0)
        return {
            "updated": last_ts if last_ts > 0 else time.time(),
            "alpha": self.alpha,
            "half_life_s": self.half_life_seconds,
            "stats": deepcopy(self._stats),
        }

    def to_dict(self) -> dict:
        """
        Serialize learner state for persistence.

        Returns:
            Dict with 'alpha', 'half_life_seconds', and 'stats'.
        """
        return {
            "alpha": self.alpha,
            "half_life_seconds": self.half_life_seconds,
            "stats": self._stats,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OutcomeLearner":
        """
        Reconstruct learner from serialized state.

        Args:
            d: Dictionary with 'alpha', 'half_life_seconds', and 'stats'.

        Returns:
            Reconstructed OutcomeLearner instance.
        """
        learner = cls(
            alpha=d.get("alpha", 0.15),
            half_life_seconds=d.get("half_life_seconds", d.get("half_life_s", 3600)),
        )
        learner._stats = d.get("stats", {})
        return learner


def load_state(path: Path) -> tuple[OutcomeLearner, int]:
    """
    Load learner state and offset from file.

    Args:
        path: Path to the state file.

    Returns:
        Tuple of (learner instance, offset). Returns new learner with offset 0 if
        file doesn't exist or is malformed.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        learner = OutcomeLearner.from_dict(data)
        offset = data.get("offset", 0)
        return learner, offset
    except Exception:
        return OutcomeLearner(), 0


def save_state(path: Path, learner: OutcomeLearner, offset: int) -> None:
    """
    Write learner state and offset to file atomically.

    Args:
        path: Path to the state file.
        learner: Learner instance to persist.
        offset: Current byte offset to save.
    """
    try:
        data = learner.to_dict()
        data["offset"] = offset
        data["updated"] = time.time()

        # Atomic write: write to temp file then replace (Path(path) accetta str e Path)
        tmp_path = Path(path).with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        pass


def process_file(jsonl_path: Path, learner: OutcomeLearner, offset: int) -> int:
    """
    Process new entries from JSONL log file.

    Args:
        jsonl_path: Path to the JSONL log file.
        learner: Learner instance to update.
        offset: Current byte offset in file.

    Returns:
        New offset after processing. Returns unchanged offset if file doesn't exist.
        Resets to 0 if file was truncated/rotated (smaller than offset).
    """
    try:
        with open(jsonl_path, "rb") as f:
            # Check if file was truncated/rotated
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()

            if file_size < offset:
                offset = 0  # File was rotated/truncated

            f.seek(offset)
            content = f.read().decode("utf-8", errors="replace")
            new_offset = f.tell()

            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    model = entry.get("final") or entry.get("orig") or DEFAULT_MODEL
                    task_class = entry.get("task_class") or DEFAULT_TASK_CLASS
                    outcome = entry.get("outcome") or "ok"
                    ts = entry.get("ts") or time.time()
                    learner.observe(model, task_class, outcome, ts)
                except Exception:
                    continue

            return new_offset
    except FileNotFoundError:
        return offset


def follow(jsonl_path: Path, state_path: Path, interval: int = 30) -> None:
    """
    Run continuous monitoring loop.

    Args:
        jsonl_path: Path to the JSONL log file.
        state_path: Path to the state file.
        interval: Seconds to sleep between processing cycles.
    """
    while True:
        learner, offset = load_state(state_path)
        offset = process_file(jsonl_path, learner, offset)
        save_state(state_path, learner, offset)
        time.sleep(interval)


def main() -> int:
    """
    Command-line entry point.

    Returns:
        Exit code (0 for success).
    """
    parser = ArgumentParser(description="Monitor router failure patterns")
    parser.add_argument("--once", action="store_true", help="Process once and exit")
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL, help="Path to JSONL log")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="Path to state file")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between cycles")

    args = parser.parse_args()

    if args.once:
        learner, offset = load_state(args.state)
        offset = process_file(args.jsonl, learner, offset)
        save_state(args.state, learner, offset)
        snapshot = learner.snapshot()
        print(f"Processed entries: {sum(e['total'] for e in snapshot['stats'].values())}")
        print(f"Unique keys: {len(snapshot['stats'])}")
        # Print top 5 by EWMA
        top5 = sorted(snapshot["stats"].items(), key=lambda x: x[1]["ewma"], reverse=True)[:5]
        print("Top 5 by EWMA:")
        for key, stats in top5:
            print(f"  {key}: ewma={stats['ewma']:.4f}, fails={stats['fails']}/{stats['total']}")
    else:
        follow(args.jsonl, args.state, args.interval)

    return 0


if __name__ == "__main__":
    # Self-test
    learner = OutcomeLearner(alpha=0.5, half_life_seconds=1000)
    base_ts = 1000.0

    # Test 1: 10 consecutive fails should bring EWMA close to 1.0
    for i in range(10):
        learner.observe("glm-5.2", "coding", "error", base_ts + i)
    key1 = "glm-5.2|coding"
    assert learner._stats[key1]["ewma"] > 0.9, f"Expected ewma > 0.9 after 10 fails, got {learner._stats[key1]['ewma']}"

    # Test 2: 10 consecutive OK should reduce EWMA significantly
    for i in range(10):
        learner.observe("glm-5.2", "coding", "ok", base_ts + 10 + i)
    assert learner._stats[key1]["ewma"] < 0.5, f"Expected ewma < 0.5 after 10 ok, got {learner._stats[key1]['ewma']}"

    # Test 3: New key with 1 fail should have EWMA = alpha
    learner.observe("minimax-m2.7", "coding", "error", base_ts + 20)
    key2 = "minimax-m2.7|coding"
    assert abs(learner._stats[key2]["ewma"] - 0.5) < 0.001, f"Expected ewma = 0.5 after 1 fail, got {learner._stats[key2]['ewma']}"

    # Test 4: Temporal decay - after dt = half_life with no events, observe 1 ok
    learner2 = OutcomeLearner(alpha=0.5, half_life_seconds=1000)
    learner2.observe("model-x", "task-y", "error", 1000.0)  # ewma = 0.5
    ewma_before = learner2._stats["model-x|task-y"]["ewma"]
    # Simulate half_life elapsed (dt = 1000s)
    learner2.observe("model-x", "task-y", "ok", 2000.0)  # Decay factor = 0.5, then update
    ewma_after = learner2._stats["model-x|task-y"]["ewma"]
    _decayed = 0.5 * 0.5  # ewma_before(0.5) * decay_factor(0.5 at dt=half_life)
    expected = 0.5 * 0.0 + (1 - 0.5) * _decayed  # alpha*fail(0, ok) + (1-alpha)*decayed
    assert abs(ewma_after - expected) < 0.001, f"Expected ewma ~ {expected}, got {ewma_after}"
    assert ewma_after < ewma_before, "EWMA should decrease after decay and OK"

    # Test 5: Roundtrip serialization
    snapshot = learner.snapshot()
    restored = OutcomeLearner.from_dict(snapshot)
    assert restored.alpha == learner.alpha
    assert restored.half_life_seconds == learner.half_life_seconds
    for k in learner._stats:
        assert k in restored._stats
        assert learner._stats[k]["ewma"] == restored._stats[k]["ewma"]
        assert learner._stats[k]["total"] == restored._stats[k]["total"]
        assert learner._stats[k]["fails"] == restored._stats[k]["fails"]
        assert learner._stats[k]["last_ts"] == restored._stats[k]["last_ts"]

    print("OK")
