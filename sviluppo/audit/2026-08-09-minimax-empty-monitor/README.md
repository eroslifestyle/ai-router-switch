# Monitor risposte vuote MiniMax

Monitor speculare a `../2026-08-09-glm-empty-monitor/monitor_glm_empty.py`, adattato
per la modalità `minimax` invece che `glm:glm-5.2`.

## Scopo

Rilevare e campionare le risposte vuote del backend MiniMax in modalità
`minimax` ( THINK = M3, ACT = M2.7). Risposta vuota = `output_tokens <= 5`
su una richiesta con `mode == "minimax"` in `router-usage.jsonl`.

Il monitor e una sonda complementare al rilevamento gia esistente per GLM
(`glm_empty_response` in `debug-events.jsonl`): GLM emette un evento esplicito
quando il body SSE torna vuoto; per MiniMax non esiste ancora un kind
`empty_response_minimax`, quindi il segnale primario e il conteggio
`output_tokens <= 5` sulle righe di `router-usage.jsonl`.

## Come si usa

```bash
# raccoglie un campione (finestra = dall'ultimo campione o 600 s) e lo appende a samples.jsonl
python3 monitor_minimax_empty.py

# stampa il report aggregato da samples.jsonl
python3 monitor_minimax_empty.py --report

# limita il report agli ultimi N campioni
python3 monitor_minimax_empty.py --report --samples 24
```

`samples.jsonl` e locale e NON va committato (e nella stessa cartella del GLM
monitor). Una riga JSON per campione, con i campi:

- `ts_start`, `ts_end`, `ts_start_iso`, `clock`, `window_min`
- `total_minimax_requests`: righe `mode=="minimax"` nella finestra
- `empty_output_le5`: di queste, quelle con `output_tokens <= 5`
- `events_empty_response_minimax`: count del kind omonimo in `debug-events.jsonl` (oggi sempre 0)
- `models`: `{MiniMax-M3: {tot, empty}, MiniMax-M2.7: {tot, empty}}`
- `pct_empty_of_total`

## Cosa cerca

- `router-usage.jsonl` (in `~/.claude/logs/`): righe con `mode == "minimax"`
- `debug-events.jsonl` (in `<repo>/logs/`): kind `empty_response_minimax`
  (kind speculativo, non ancora emesso dal router - contato per quando verra aggiunto)

Il breakdown normalizza i nomi modello case-insensitive
(`MiniMax-M2.7`/`minimax-m2.7-hs` -> `MiniMax-M2.7`,
`MiniMax-M3`/`minimax-m3` -> `MiniMax-M3`).

## Differenze dal glm-empty-monitor

| Aspetto                | GLM monitor                              | MiniMax monitor (questo)                    |
|------------------------|------------------------------------------|---------------------------------------------|
| Filtro router-usage    | `final == "glm:glm-5.2"`                 | `mode == "minimax"`                         |
| Segnale primario       | evento `glm_empty_response` (attempt=1/2)| `output_tokens <= 5` sulle righe minimax    |
| Segnale secondario     | `glm_exhausted`                          | kind `empty_response_minimax` (oggi assente)|
| Concetto di "recovery" | attempt1 - exhausted                     | non applicato (MiniMax non ha retry interno esposto come GLM) |
| Breakdown per modello  | no (singolo modello GLM)                 | si, normalizzato M3 vs M2.7                 |
| Flag CLI               | solo `--report`                          | `--report` + `--samples N`                  |

## Caveat

- Le richieste in `mode=="minimax"` includono anche le esenzioni micro-edit
  (haiku, sonnet) che girano su Anthropic nonostante la modalita. Il breakdown
  per modello le nasconde (classification come `(altro)`) ma il
  `total_minimax_requests` le conta. Per isolare solo le chiamate ai modelli
  MiniMax veri, guardare il breakdown M3+M2.7.
- `output_tokens <= 5` e una proxy: include sia i veri 200-ma-vuoti sia i 429/5xx
  con `output_tokens=0`. Per discrimare serve cross-check con `status` nella
  riga router-usage (miglioramento futuro, YAGNI per la v1).
