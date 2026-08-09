# Monitor risposte vuote Anthropic

Monitor speculare a `../2026-08-09-minimax-empty-monitor/monitor_minimax_empty.py`, adattato
per la modalita `anthropic`.

## Scopo

Rilevare e campionare le risposte vuote del backend Anthropic in modalita
`anthropic`. Risposta vuota = `output_tokens <= 5` su una richiesta con
`mode == "anthropic"` in `router-usage.jsonl`.

Il monitor monitora anche eventi `kind == 'empty_response_anthropic'`
in `debug-events.jsonl`.

## Come si usa

```bash
# raccoglie un campione (finestra = dall'ultimo campione o 600 s)
python3 monitor_anthropic_empty.py

# stampa il report aggregato da samples.jsonl
python3 monitor_anthropic_empty.py --report

# limita il report agli ultimi N campioni
python3 monitor_anthropic_empty.py --report --samples 24
```

`samples.jsonl` e locale e NON va committato. Una riga JSON per campione:
- `ts_start`, `ts_end`, `ts_start_iso`, `clock`, `window_min`
- `total_anthropic_requests`: righe `mode=="anthropic"` nella finestra
- `empty_output_le5`: di queste, quelle con `output_tokens <= 5`
- `events_empty_response_anthropic`: count del kind omonimo in `debug-events.jsonl`
- `models`: `{claude-opus-5, claude-sonnet-5, claude-haiku-4-5, claude-fable-5}` con `{tot, empty}`
- `pct_empty_of_total`

## Cosa cerca

- `router-usage.jsonl` (in `~/.claude/logs/`): righe con `mode == "anthropic"`
- `debug-events.jsonl` (in `<repo>/logs/`): kind `empty_response_anthropic`

## Differenze dal minimax-empty-monitor

| Aspetto                | MiniMax monitor                        | Anthropic monitor (questo)              |
|------------------------|----------------------------------------|-----------------------------------------|
| Filtro router-usage    | `mode == "minimax"`                   | `mode == "anthropic"`                   |
| Segnale primario       | `output_tokens <= 5`                   | `output_tokens <= 5`                    |
| Segnale secondario     | kind `empty_response_minimax` (assente)| kind `empty_response_anthropic` (futuro)|
| Breakdown per modello  | M3 vs M2.7                            | opus, sonnet, haiku, fable              |

## Caveat

- `output_tokens <= 5` include sia i veri 200-ma-vuoti sia i 429/5xx
  con `output_tokens=0`. Per discrimare serve cross-check con `status`
  nella riga router-usage (miglioramento futuro, YAGNI per la v1).
