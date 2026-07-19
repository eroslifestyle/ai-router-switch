# BUG-CATALOG.md

> Generato automaticamente da `scripts/generate_bug_report.py` a partire da `logs/BUG-CATALOG.jsonl`. Non modificare a mano — rilanciare lo script. Vedi `DEBUG-CATALOG-SPEC.md` per lo schema completo.

**2 tipi distinti di bug/blocco/errore** · **31 occorrenze totali** su 2 modalita'.

## Modalita': `anthropic`

1 tipi distinti, 30 occorrenze.

### `tool_isolation_strip`

- **Firma**: `5eb9aca25569b15f`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 30
- **Prima volta**: 2026-07-19T19:41:54Z
- **Ultima volta**: 2026-07-19T19:44:45Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search'] kept=40/42`

## Modalita': `mix-am`

1 tipi distinti, 1 occorrenze.

### `minimax_fallback_5xx` (502)

- **Firma**: `9fb265787ba5870f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T19:40:52Z
- **Ultima volta**: 2026-07-19T19:40:52Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `<html> <head><title>502 Bad Gateway</title></head> <body bgcolor="white"> <center><h1>502 Bad Gateway</h1></center> <hr><center>alb</center> </body> </html>`
