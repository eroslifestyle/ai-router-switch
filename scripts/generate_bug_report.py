#!/usr/bin/env python3
# ~90 lines
"""Genera BUG-CATALOG.md a partire da logs/BUG-CATALOG.jsonl.
Vedi DEBUG-CATALOG-SPEC.md. Uso: python3 scripts/generate_bug_report.py"""
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import debug_catalog  # noqa: E402

OUTPUT_MD = PROJECT_ROOT / "BUG-CATALOG.md"
SEVERITY_ORDER = {"bug": 0, "error": 1, "block": 2}
SEVERITY_LABEL = {"bug": "🐛 Bug", "error": "🔴 Errori", "block": "🟡 Blocchi"}


def _group_by_category(items: list) -> dict:
    grouped = defaultdict(list)
    for item in items:
        for cat in item.get("categories") or ["?"]:
            grouped[cat].append(item)
    return grouped


def _render_entry(item: dict) -> str:
    lines = [
        f"### `{item.get('kind', '?')}`"
        + (f" ({item['code']})" if item.get("code") not in (None, "") else ""),
        "",
        f"- **Firma**: `{item.get('signature', '?')}`",
        f"- **Severita'**: {SEVERITY_LABEL.get(item.get('severity'), item.get('severity'))}",
        f"- **Occorrenze**: {item.get('count', 0)}",
        f"- **Prima volta**: {item.get('first_seen', '?')}",
        f"- **Ultima volta**: {item.get('last_seen', '?')}",
        f"- **Modalita' coinvolte**: {', '.join(item.get('categories', []))}",
    ]
    snippet = (item.get("example_snippet") or "").strip()
    if snippet:
        snippet_display = snippet[:400].replace("\n", " ")
        lines.append(f"- **Esempio**: `{snippet_display}`")
    lines.append("")
    return "\n".join(lines)


def generate() -> str:
    items = debug_catalog.get_catalog()
    total = len(items)
    total_occurrences = sum(i.get("count", 0) for i in items)
    grouped = _group_by_category(items)

    out = [
        "# BUG-CATALOG.md",
        "",
        "> Generato automaticamente da `scripts/generate_bug_report.py` a partire da"
        " `logs/BUG-CATALOG.jsonl`. Non modificare a mano — rilanciare lo script."
        " Vedi `DEBUG-CATALOG-SPEC.md` per lo schema completo.",
        "",
        f"**{total} tipi distinti di bug/blocco/errore** · **{total_occurrences} occorrenze totali** "
        f"su {len(grouped)} modalita'.",
        "",
    ]

    for category in sorted(grouped.keys()):
        cat_items = sorted(
            grouped[category],
            key=lambda i: (SEVERITY_ORDER.get(i.get("severity"), 9), -i.get("count", 0)),
        )
        out.append(f"## Modalita': `{category}`")
        out.append("")
        out.append(f"{len(cat_items)} tipi distinti, {sum(i.get('count', 0) for i in cat_items)} occorrenze.")
        out.append("")
        for item in cat_items:
            out.append(_render_entry(item))

    if not grouped:
        out.append("_Nessun evento catturato finora._")
        out.append("")

    return "\n".join(out)


def main():
    md = generate()
    OUTPUT_MD.write_text(md, encoding="utf-8")
    print(f"Scritto {OUTPUT_MD} ({len(md)} char)")


if __name__ == "__main__":
    main()
