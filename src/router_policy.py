import json
import time
from pathlib import Path

# Constants
POLICY_FILE = Path.home() / ".claude" / "router-policy.json"
_CACHE_TTL = 5.0  # seconds
_DEGRADE_THRESHOLD = 0.5

# Module state (cache)
_cache = {"ts": 0.0, "data": {}}


def get_policy() -> dict:
    """
    Retrieve the router policy from file with caching.
    Returns cached data if still valid, otherwise reads from file.
    Fail-open: returns empty dict on any error.
    """
    if time.time() - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]
    
    try:
        data = json.loads(POLICY_FILE.read_text())
        _cache["ts"] = time.time()
        _cache["data"] = data
    except Exception:
        data = {}
        _cache["ts"] = time.time()
        _cache["data"] = data
    
    return data


def is_degraded(model: str, task_class: str = "*") -> bool:
    """
    Check if a model is considered degraded for a given task class.
    Returns True if the model's ewma failure rate exceeds threshold.
    """
    policy = get_policy()
    degraded = policy.get("degraded", {})
    entry = degraded.get(model)
    
    if not entry:
        return False
    
    tc = entry.get("task_class", "*")
    if tc != "*" and tc != task_class:
        return False
    
    return float(entry.get("ewma", 0.0)) >= _DEGRADE_THRESHOLD


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    
    # Mock POLICY_FILE to a temp file with example policy
    tmp = tempfile.mktemp(suffix=".json")
    with open(tmp, "w") as f:
        json.dump({
            "updated": 1785522000,
            "degraded": {
                "glm-5.2": {"task_class": "coding", "ewma": 0.71, "fails": 8, "total": 12, "since": 1785521000}
            }
        }, f)
    
    # Override module variables for testing
    import router_policy as rp
    rp.POLICY_FILE = Path(tmp)
    rp._cache = {"ts": 0.0, "data": {}}
    
    # Run assertions
    assert rp.is_degraded("glm-5.2", "coding") is True
    assert rp.is_degraded("glm-5.2", "chat") is False
    assert rp.is_degraded("glm-5.2") is False
    assert rp.is_degraded("minimax-m2.7", "coding") is False
    
    # Test fail-open: empty cache and file
    rp._cache = {"ts": 0.0, "data": {}}
    with open(tmp, "w") as f:
        json.dump({}, f)
    
    assert rp.get_policy() == {}
    assert rp.is_degraded("glm-5.2", "coding") is False
    
    print("OK")
