---
name: modalita-qwen-alibaba-model-studio
description: Settima modalità del router — Qwen pura (THINK e ACT entrambi Qwen), porta 8778, in attesa di credenziali Alibaba Cloud Model Studio Singapore
updated: 2026-08-03
metadata:
  type: project
---

# Modalità `qwen` — Alibaba Cloud Model Studio

Settima modalità del router. Stato: **ATTIVA e verificata in produzione** dal 2026-08-03
sull'account `ws-XXXXXXXXXXXXXXXX` (Singapore).

## Cos'è

Modalità PURA: THINK e ACT sono entrambi Qwen. Nessun fallback verso Anthropic, MiniMax o GLM — stesso isolamento della modalità `glm` pura.

Porta dedicata: 8778. Sul router principale :8787 si attiva con `ai-mode qwen` (globale) o `!router qwen` (solo la chat corrente).

| Fase | Modello |
|---|---|
| THINK | qwen3.8-max |
| ACT | qwen3-coder-plus |
| VERIFY | chi ha fatto il THINK |
| Escalation dopo 2 fallimenti (solo esecuzione) | qwen3-coder-plus → qwen3.7-plus → qwen3.8-max |

**Perché:** un quarto provider indipendente dai tre esistenti, con una linea di modelli dedicata al codice (`qwen3-coder-*`) e un catalogo di servizi nativi proprio.

**Quando usarla:** quando serve una catena interamente Alibaba. I servizi generativi non sono un'esclusiva — MiniMax offre già immagini, video, musica e TTS sulle stesse rotte: qui cambia il provider, non la funzione. Sono invece specifici di Qwen `qwen3-rerank` e `text-embedding-v4`.

## Attivazione in tre passi

1. Procurarsi da https://modelstudio.console.alibabacloud.com/ap-southeast-1 una API key della regione Singapore e il WorkspaceId (pagina Workspace Details). ATTENZIONE: le chiavi NON sono interscambiabili fra regioni.
2. Eseguire `qwen-setup` (chiede la chiave con getpass, non la mostra a schermo). Salva le credenziali nello store cifrato e verifica con una chiamata reale. Con `--dry-run` mostra cosa farebbe senza scrivere niente. Il MCP WebSearch **non** viene registrato: non esiste in regione internazionale (404). Con `--with-mcp` lo si forza comunque.
3. Eseguire `python3 sviluppo/tools/probe_qwen.py` per vedere quali model ID esistono sull'account. **`--deep` con cautela**: misura il context window inviando payload reali, e ogni iterazione accettata fa pagare l'input. Preferire un modello per volta con `--models`.

Senza credenziali la modalità risponde `qwen key missing` con HTTP 502: è il comportamento atteso, non un guasto.

## Endpoint

Anthropic-compatible, forma identica a z.ai:

`POST https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1/messages`

La base URL finisce con `/apps/anthropic` SENZA `/v1`: aggiungerlo fa comporre al client `/v1/v1/models`.
Senza WorkspaceId si ricade su `https://dashscope-intl.aliyuncs.com/apps/anthropic`.

Auth: si mandano sia `x-api-key` sia `Authorization: Bearer`.

Supporta: stream, tools, tool_choice, thinking, cache_control ephemeral, blocchi image e video, usage con `cache_creation_input_tokens` e `cache_read_input_tokens`.

NON supporta: `/v1/models` (404 innocuo) e la web search built-in.

## Strumenti nativi

| Strumento | Comando o rotta | Note |
|---|---|---|
| Esecuzione codice | `qwen-code "spec" > file.py` | qwen3-coder-plus; stdout pulito, diagnostica su stderr |
| Web search | `qwen-web "query"` | DashScope nativo in SSE: è l'unico path che restituisce le fonti |
| Web search MCP | — | **non disponibile**: 404 in regione internazionale |
| Immagini | POST /v1/images/generations | qwen-image-2.0-pro |
| Video | POST /v1/videos/generations | happyhorse-1.1-t2v |
| TTS | POST /v1/audio/speech | qwen3-tts-flash |
| Musica | POST /v1/music/generations | fun-music-v1 |
| Embeddings | POST /v1/embeddings | text-embedding-v4 |
| Rerank | POST /v1/rerank | qwen3-rerank |

Le rotte generative sono mode-aware: solo in modalità qwen vanno a DashScope, in tutte le altre restano sul percorso MiniMax di sempre.

## Variabili d'ambiente

| Variabile | Effetto |
|---|---|
| `QWEN_API_KEY` / `DASHSCOPE_API_KEY` | chiave (altrimenti secrets.sh qwen.api_key) |
| `QWEN_WORKSPACE_ID` | workspace (altrimenti secrets.sh qwen.workspace_id) |
| `QWEN_REGION` | regione, default ap-southeast-1 |
| `QWEN_API_BASE` | override totale dell'upstream Anthropic-compatible |
| `QWEN_DASHSCOPE_HOST` | host dei servizi nativi (default: derivato dal workspace) |
| `AIROUTER_QWEN_MAX_BODY_BYTES` | tetto sui byte del corpo, default 4 MB (il gateway dà 413 oltre ~5 MB) |
| `QWEN_MODEL_TOP` / `MID` / `CODER` / `VISION` | modelli per tier |
| `QWEN_MODEL_IMAGE` / `VIDEO` / `TTS` / `ASR` / `MUSIC` / `EMBED` / `RERANK` | modelli dei servizi nativi |
| `QWEN_PATH_IMAGE` / `TTS` / `VIDEO` / `ASR` / `MUSIC` | path DashScope |
| `QWEN_MCP_URL` | url del server MCP WebSearch |
| `AIROUTER_QWEN_MAX_TOKENS_LIMIT` | clamp di max_tokens, default 65536 |

## Cosa è VERIFICATO e cosa NO

**Attivata e in funzione dal 2026-08-03** sull'account `ws-XXXXXXXXXXXXXXXX` (Singapore).

MISURATO IN PRODUZIONE, attraverso il router sulla porta 8778:
- `claude-opus-5` → `qwen3.8-max` e `claude-haiku` → `qwen3-coder-plus`, entrambi rispondono
- streaming SSE, tool calling (`stop_reason: tool_use` con nome e argomenti corretti), thinking, `cache_control`
- `/v1/embeddings` → `text-embedding-v4`, vettore da 1024 dimensioni
- `/v1/images/generations` → URL PNG realmente generato
- `qwen-web` → risposta aggiornata con 16 fonti citabili
- context: `qwen3-coder-plus` ha **accettato 1.000.009 token di input** (corpo da 4,8 MB)
- **limite sui BYTE, ortogonale al contesto**: il gateway risponde `413 RequestTooLarge`
  guardando la dimensione del corpo, prima di valutare il contesto, e non dichiara il limite.
  4,8 MB passano, 6,7 MB no. Guardrail a 4 MB in `QWEN_MAX_BODY_BYTES`.
- modelli disponibili sull'account (13 su 13 testati, tutti HTTP 200): `qwen3.8-max`,
  `qwen3.7-max`, `qwen3.7-plus`, `qwen3.7-flash`, `qwen3.6-plus`, `qwen3.6-flash`,
  `qwen3-max`, `qwen3-coder-next`, `qwen3-coder-plus`, `qwen3-coder-flash`,
  `qwen3-vl-plus`, `qwen-plus`, `qwen-flash`

SMENTITO dalla misura (era scritto qui e non era vero):
- l'host DashScope **non** è `dashscope-intl.aliyuncs.com`: il CSV delle credenziali emesso
  dalla console dichiara `dashScope = https://{ws}.{region}.maas.aliyuncs.com/api/v1`
- i servizi nativi autenticano con `Authorization: Bearer`, **non** `x-api-key`
- il **MCP WebSearch di Bailian non esiste in regione internazionale**: 404 sia sull'host del
  workspace sia su `dashscope-intl`. La registrazione è diventata opt-in (`--with-mcp`)
- la web search **non restituisce fonti** su `/compatible-mode/v1/chat/completions` (277 eventi
  ispezionati, zero occorrenze). Le fonti esistono solo su DashScope nativo in SSE

ANCORA NON MISURATO (stime prudenziali nel codice, sottostimare costa solo compressione inutile):
- context window di flash, coder-flash e vl-plus: ogni misura costa l'input davvero
  (le due fatte sono costate ~1,5M token), quindi non sono state ripetute per ogni modello
- limiti RPM/TPM: le pagine della doc rispondono 404, il rate limiter usa placeholder
- path DashScope di video, ASR e musica

## File toccati

| File | Cosa |
|---|---|
| `src/qwen_backend.py` | NUOVO: chiavi, upstream, tiering, rate limiter, `forward_qwen` |
| `src/qwen_generative.py` | NUOVO: servizi nativi DashScope |
| `src/role_routing.py` | rotte qwen, `model_provider` riconosce `qwen`/`qwq`/`qvq`/`wan`/`happyhorse` |
| `src/router_constants.py` | `VALID_MODES`, `PORT_MODE[8778]`, `QWEN_AVAILABLE` |
| `src/ai-router-proxy.py` | ramo di dispatch, rotte generative mode-aware, healthz |
| `src/tool_isolation.py` | isolamento dei tool nativi Qwen |
| `src/model_context_map.py` | context window Qwen |
| `scripts/ai-mode` | accetta `qwen` |
| `scripts/qwen-code`, `scripts/qwen-web`, `scripts/qwen-setup` | CLI, con symlink in `~/.local/bin` |
| `sviluppo/tools/probe_qwen.py` | probe live |
| `systemd/ai-router.service` | porta 8778 in `ExecStartPre` |
| `~/.claude/CLAUDE.md` e `~/.claude/hooks/enforce_hierarchy.py` | riga qwen nella gerarchia, deny che rimanda a `qwen-code` |

## Voci aperte

- ~~Il ramo glm ha la stessa forma latente corretta nel ramo qwen.~~ **CHIUSA il 2026-08-03**: corretta alla fonte per entrambi con `src/synthetic_response.py`, più il 429 che non degrada più a 502. Vedi `sviluppo/tests/test_synthetic_response.py`.
- In modalità `glm` le rotte generative vanno ancora a **MiniMax**, benché `glm_backend` contenga `forward_glm_image`/`forward_glm_video`: quelle funzioni sono raggiungibili solo da `route_glm_request`, che **non è chiamata da nessuno**. Comportamento invariato rispetto a prima della modalità qwen; cablarle è una scelta da fare, non un fix.
- Non esiste un equivalente di `m3-wiki` per Qwen: in modalità qwen l'hook della gerarchia rimanda i documenti a `qwen-code`.
