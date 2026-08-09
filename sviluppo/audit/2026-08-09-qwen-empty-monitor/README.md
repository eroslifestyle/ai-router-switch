# Monitor risposte vuote Qwen

Monitor speculare a `../2026-08-09-minimax-empty-monitor/monitor_minimax_empty.py`, adattato
per la modalita `qwen`.

## Scopo

Rilevare e campionare le risposte vuote del backend Qwen in modalita
`qwen`. Risposta vuota = `output_tokens <= 5` su una richiesta con
`mode == "qwen"` in `router-usage.jsonl`.

Il monitor e una sonda complementare: Qwen emette un evento esplicito
quando il body SSE torna vuoto (`kind == 'empty_response_qwen'`).

## Come si usa

```bash
# raccoglie un campione (finestra = dall'ultimo campione o 600 s)
python3 monitor_qwen_empty.py

# stampa il report aggregato da samples.jsonl
python3 monitor_qwen_empty.py --report

# limita il report agli ultimi N campioni
python3 monitor_qwen_empty.py --report --samples 24
```

`samples.jsonl` e locale e NON va committato. Una riga JSON per campione:
- `ts_start`, `ts_end`, `ts_start_iso`, `clock`, `window_min`
- `total_qwen_requests`: righe `mode=="qwen"` nella finestra
- `empty_output_le5`: di queste, quelle con `output_tokens <= 5`
- `events_empty_response_qwen`: count del kind omonimo in `debug-events.jsonl`
- `models`: `{qwen3.8-max, qwen3-coder-plus, qwen3.7-plus}` con `{tot, empty}`
- `pct_empty_of_total`

## Cosa cerca

- `router-usage.jsonl` (in `~/.claude/logs/`): righe con `mode == "qwen"`
- `debug-events.jsonl` (in `<repo>/logs/`): kind `empty_response_qwen`

## Differenze dal minimax-empty-monitor

| Aspetto                | MiniMax monitor                        | Qwen monitor (questo)                   |
|------------------------|----------------------------------------|-----------------------------------------|
| Filtro router-usage    | `mode == "minimax"`                   | `mode == "qwen"`                        |
| Segnale primario       | `output_tokens <= 5`                   | `output_tokens <= 5`                    |
| Segnale secondario     | kind `empty_response_minimax` (assente)| kind `empty_response_qwen` (eventuale) |
| Breakdown per modello  | M3 vs M2.7                            | 3.8-max, coder-plus, 3.7-plus         |

## Caveat

- `output_tokens <= 5` include sia i veri 200-ma-vuoti sia i 429/5xx
  con `output_tokens=0`. Per discrimare serve cross-check con `status`
  nella riga router-usage (miglioramento futuro, YAGNI per la v1).
