import json
import time
import pathlib
import datetime
from typing import Union, Any

import paths

DEFAULT_M3_USAGE = paths.config_home() / "m3" / "usage.jsonl"


def _parse_ts(ts: Union[str, int, float, None]) -> float:
    """Parse timestamp from entry. Return epoch seconds."""
    if ts is None:
        return time.time()
    if isinstance(ts, (int, float)):
        return float(ts)
    # string case: ISO‑like format
    try:
        normalized = ts.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return time.time()


def _outcome_from_finish(finish: str) -> str:
    """Map finish reason to outcome string."""
    if finish == "end_turn":
        return "ok"
    if finish == "length":
        return "truncated"
    if finish == "error" or finish == "":
        return "error"
    return "ok"


def process_m3_usage(
    m3_path: Union[str, pathlib.Path],
    learner: Any,
    offset: int,
) -> int:
    """
    Process the MiniMax m3‑code usage file.

    Reads new lines from ``m3_path`` starting at ``offset`` (bytes) and feeds
    coding events to ``learner.observe``. Returns the new offset after reading.
    If the file has been rotated (new offset < offset), returns 0 so the next
    call will re‑read from the start. Any unhandled exception returns the
    original offset unchanged.
    """
    original_offset = offset
    try:
        with open(m3_path, "rb") as f:
            f.seek(offset)
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        new_offset = offset + len(raw)

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                # malformed JSON – skip line
                continue
            # only consider entries that carry a ``model`` field (coding calls)
            if "model" not in entry:
                continue
            model = entry["model"]
            finish = entry.get("finish", "")
            outcome = _outcome_from_finish(finish)
            ts = _parse_ts(entry.get("ts"))
            learner.observe(model, "coding", outcome, float(ts))
    except Exception:
        # tolerate any unexpected error
        return original_offset

    # handle file rotation
    if new_offset < original_offset:
        return 0
    return new_offset


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile
    import os

    class OutcomeLearner:
        """Minimal learner for self‑test purposes."""
        def __init__(self) -> None:
            self._stats: dict = {}

        def observe(
            self, model: str, task_class: str, outcome: str, ts: float
        ) -> None:
            key = f"{model}|{task_class}"
            if key not in self._stats:
                self._stats[key] = {"total": 0, "fails": 0}
            self._stats[key]["total"] += 1
            if outcome != "ok":
                self._stats[key]["fails"] += 1

    # Build a temporary JSONL with the four test records
    records = [
        '{"ts":"2026-07-31T17:00:00+00:00","model":"minimax-m2.7-hs","prompt_tokens":10,"completion_tokens":50,"finish":"end_turn"}',
        '{"ts":"2026-07-31T17:01:00+00:00","model":"minimax-m2.7-hs","prompt_tokens":10,"completion_tokens":0,"finish":"length"}',
        '{"ts":"2026-07-31T17:02:00+00:00","model":"minimax-m2.7-hs","prompt_tokens":10,"completion_tokens":0,"finish":"error"}',
        '{"ts":"2026-07-31T17:03:00+00:00","tool":"web","query":"x","seconds":1.0}',
    ]

    tmp_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    )
    try:
        with tmp_file as f:
            for rec in records:
                f.write(rec + "\n")
        tmp_path = tmp_file.name

        learner = OutcomeLearner()
        off = process_m3_usage(tmp_path, learner, 0)

        stats = learner._stats
        # Verify we processed exactly the three coding entries
        assert (
            stats["minimax-m2.7-hs|coding"]["total"] == 3
        ), f"total != 3: {stats}"
        assert (
            stats["minimax-m2.7-hs|coding"]["fails"] == 2
        ), f"fails != 2: {stats}"
        # offset must have advanced
        assert off > 0, "offset not advanced"
        # no spurious entries
        for k in stats:
            assert "?" not in k, f"unexpected key {k}"
            assert "tool" not in k, f"unexpected key {k}"

        print("OK")
    finally:
        os.unlink(tmp_path)
