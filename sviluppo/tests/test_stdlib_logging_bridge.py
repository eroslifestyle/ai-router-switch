import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import router_utils


def test_stdlib_info_record_reaches_log(monkeypatch):
    router_utils._install_stdlib_bridge()
    seen = []
    monkeypatch.setattr(router_utils, "log", lambda msg: seen.append(msg))
    logging.getLogger("context_rewrite").info("shrink: test x")
    assert any("shrink: test x" in m for m in seen)
    # idempotente
    router_utils._install_stdlib_bridge()
    logging.getLogger("context_rewrite").info("shrink: test y")
    assert sum(1 for m in seen if "test y" in m) == 1
