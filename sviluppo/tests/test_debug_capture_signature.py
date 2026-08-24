"""Regressione: ogni chiamata a dl.capture()/debug_capture() deve usare solo
kwarg realmente accettati da DebugLogger.capture(). Il bug che l'ha originata:
src/forward_minimax.py passava `snippet=` che capture() non ha mai avuto (il
campo si chiama `note`), e il TypeError si propagava come 502 su una
richiesta reale (2026-08-24)."""
import ast
import inspect
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, SRC_DIR)

from router_debug import DebugLogger  # noqa: E402


def _valid_capture_kwargs():
    sig = inspect.signature(DebugLogger.capture)
    return {
        name for name, p in sig.parameters.items()
        if p.kind == inspect.Parameter.KEYWORD_ONLY
    }


def _iter_py_files():
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _is_capture_call(node):
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "capture":
        if isinstance(func.value, ast.Name) and func.value.id == "dl":
            return True
    if isinstance(func, ast.Name) and func.id == "debug_capture":
        return True
    return False


def test_all_capture_calls_use_valid_kwargs():
    valid = _valid_capture_kwargs()
    assert valid, "signature capture() non risolta: il test non protegge nulla"

    violations = []
    for path in _iter_py_files():
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_capture_call(node):
                continue
            for kw in node.keywords:
                if kw.arg is None:  # **kwargs forwarding, non un typo
                    continue
                if kw.arg not in valid:
                    violations.append(
                        f"{os.path.relpath(path, REPO_ROOT)}:{node.lineno}: "
                        f"kwarg sconosciuto '{kw.arg}' (validi: {sorted(valid)})"
                    )

    assert not violations, "kwarg non validi in chiamate a capture():\n" + "\n".join(violations)


if __name__ == "__main__":
    test_all_capture_calls_use_valid_kwargs()
    print("OK")
