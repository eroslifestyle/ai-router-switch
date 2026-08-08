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

import paths

# Constants for default paths
DEFAULT_JSONL = paths.log_file("router-usage.jsonl")
DEFAULT_STATE = paths.learnings_file()
DEFAULT_POLICY = paths.policy_file()

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


def _read_int(path) -> int:
    """Offset int da file di stato secondario (sorgenti aggiuntive)."""
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return 0


def _write_int(path, value) -> None:
    """Scrive offset int in file di stato secondario (fail-safe)."""
    try:
        Path(path).write_text(str(int(value)))
    except Exception:
        pass


def emit_policy(learner, policy_path, threshold=0.5):
    # Fase 3 ACTUATOR: scrive router-policy.json con i modelli degradati (ewma>=threshold).
    # Il proxy e l'orchestratore consultano questo file (hot-reload) per deviare il routing.
    try:
        deg = {}
        for k, st in learner._stats.items():
            if st["ewma"] >= threshold and st["total"] >= 3:
                m, _, tc = k.partition("|")
                # "?" = non classificato nel sidecar: emesso come "*" perche'
                # is_degraded fa matchare solo "*" o la classe esatta.
                deg[m] = {"task_class": tc if tc not in ("", "?") else "*",
                          "ewma": round(st["ewma"], 3),
                          "fails": st["fails"], "total": st["total"], "since": st["last_ts"]}
        tmp = Path(policy_path).with_suffix(".tmp")
        tmp.write_text(json.dumps({"updated": time.time(), "degraded": deg}, indent=2))
        tmp.replace(policy_path)
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
                    # Sonde e test non sono traffico reale (2026-08-08, audit D5):
                    # la policy marcava degradato un modello anche sulla base di
                    # richieste generate da noi. Nel sidecar il 6,3% delle entry
                    # storiche erano sonde, e senza filtro la modalita' minimax
                    # risultava al 95,3% di errori mentre le richieste vere erano
                    # 9 con zero errori. Il campo lo scrive log_router_usage
                    # quando la richiesta porta l'header x-airouter-synthetic.
                    if entry.get("synthetic"):
                        continue
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


def run_cycle(jsonl_path: Path, state_path: Path, policy_path: Optional[Path] = None) -> "OutcomeLearner":
    """
    Un ciclo completo: carica lo stato, processa il log del router, processa la telemetria m3-code,
    salva e rigenera la policy. Usata sia da --once sia dal loop continuo: prima esisteva solo
    dentro il ramo --once e follow() ne era una copia monca, senza Fase 2.5 e senza emit_policy.
    """
    if policy_path is None:
        policy_path = DEFAULT_POLICY

    learner, offset = load_state(state_path)
    offset = process_file(jsonl_path, learner, offset)

    # La telemetria m3-code è OUT-OF-BAND: invisibile a router-usage.jsonl
    try:
        from self_healing.m3_source import process_m3_usage, DEFAULT_M3_USAGE
        m3_off_path = state_path.with_suffix(state_path.suffix + ".m3off")
        off_m3 = _read_int(m3_off_path)
        # l'offset AVANZATO va riscritto: senza l'assegnazione il file resterebbe
        # fermo e la telemetria m3 verrebbe riprocessata a ogni ciclo
        off_m3 = process_m3_usage(DEFAULT_M3_USAGE, learner, off_m3)
        _write_int(m3_off_path, off_m3)
    except Exception:
        pass

    save_state(state_path, learner, offset)
    emit_policy(learner, policy_path)
    return learner


def follow(jsonl_path: Path, state_path: Path, interval: int = 30) -> None:
    """
    Loop continuo: esegue run_cycle ogni interval secondi.
    """
    while True:
        run_cycle(jsonl_path, state_path)
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
        # il ciclo è condiviso con il loop continuo, così i due rami non possono più divergere.
        learner = run_cycle(args.jsonl, args.state)
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
    # Fino al 2026-08-04 qui viveva un self-test che stampava "OK" e non
    # chiamava mai main(): la CLI era irraggiungibile e il watcher non poteva
    # girare, ne una volta ne in continuo. I suoi cinque casi sull'EWMA sono
    # stati spostati in sviluppo/tests/test_watcher.py.
    raise SystemExit(main())
