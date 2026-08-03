---
name: modalita-qwen-alibaba-model-studio
description: Settima modalità del router — Qwen pura (THINK e ACT entrambi Qwen), porta 8778, in attesa di credenziali Alibaba Cloud Model Studio Singapore
updated: 2026-08-03
metadata:
  type: project
---

# Modalità `qwen` — Alibaba Cloud Model Studio

Settima modalità del router. Stato: implementata e attiva sul router, IN ATTESA DI CREDENZIALI.

## Cos'è

Modalità PURA: THINK e ACT sono entrambi Qwen. Nessun fallback verso Anthropic, MiniMax o GLM — stesso isolamento della modalità `glm` pura.

Porta dedicata: 8778. Sul router principale :8787 si attiva con `ai-mode qwen` (globale) o `!router qwen` (solo la chat corrente).

| Fase | Modello |
|---|---|
| THINK | qwen3.7-max |
| ACT | qwen3-coder-plus |
| VERIFY | chi ha fatto il THINK |
| Escalation dopo 2 fallimenti (solo esecuzione) | qwen3-coder-plus → qwen3.7-plus → qwen3.7-max |

**Perché:** un quarto provider indipendente dai tre esistenti, con una linea di modelli dedicata al codice (`qwen3-coder-*`) e un catalogo di servizi nativi proprio.

**Quando usarla:** quando serve una catena interamente Alibaba. I servizi generativi non sono un'esclusiva — MiniMax offre già immagini, video, musica e TTS sulle stesse rotte: qui cambia il provider, non la funzione. Sono invece specifici di Qwen `qwen3-rerank` e `text-embedding-v4`.

## Attivazione in tre passi

1. Procurarsi da https://modelstudio.console.alibabacloud.com/ap-southeast-1 una API key della regione Singapore e il WorkspaceId (pagina Workspace Details). ATTENZIONE: le chiavi NON sono interscambiabili fra regioni.
2. Eseguire `qwen-setup` (chiede la chiave con getpass, non la mostra a schermo). Lo script salva le credenziali nello store cifrato, registra il MCP WebSearch e verifica con una chiamata reale. Con `--dry-run` mostra cosa farebbe senza scrivere niente.
3. Eseguire `python3 sviluppo/tools/probe_qwen.py --deep` per verificare quali model ID esistono davvero e qual è il context window reale, poi correggere le costanti che il probe segnala.

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
| Web search | `qwen-web "query"` | enable_search + search_options.search_strategy=agent |
| Web search MCP | server `qwen-websearch` in `~/.claude/.mcp.json` | registrato da qwen-setup |
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
| `QWEN_DASHSCOPE_HOST` | host dei servizi nativi |
| `QWEN_MODEL_TOP` / `MID` / `CODER` / `VISION` | modelli per tier |
| `QWEN_MODEL_IMAGE` / `VIDEO` / `TTS` / `ASR` / `MUSIC` / `EMBED` / `RERANK` | modelli dei servizi nativi |
| `QWEN_PATH_IMAGE` / `TTS` / `VIDEO` / `ASR` / `MUSIC` | path DashScope |
| `QWEN_MCP_URL` | url del server MCP WebSearch |
| `AIROUTER_QWEN_MAX_TOKENS_LIMIT` | clamp di max_tokens, default 65536 |

## Cosa è VERIFICATO e cosa NO

Questa sezione è la più importante: non trattare come fatto ciò che è marcato NON VERIFICATO.

VERIFICATO sulla doc ufficiale:
- forma, campi e limiti dell'endpoint Anthropic-compatible
- path DashScope di immagini e TTS: `/api/v1/services/aigc/multimodal-generation/generation`
- path embeddings: `/compatible-mode/v1/embeddings`
- assenza di `/v1/models` e assenza della web search built-in su quell'endpoint

VERIFICATO per misura diretta (test end-to-end su istanza isolata, upstream finto):
- `claude-opus-5` instrada a qwen3.7-max, `claude-haiku` a qwen3-coder-plus
- `max_tokens` viene clampato a 65536
- il tool nativo Qwen sopravvive in modalità qwen e viene rimosso nelle altre; i tool GLM/MiniMax/Anthropic vengono rimossi in modalità qwen
- le quattro rotte generative raggiungono DashScope con path e modello attesi, e in modalità minimax la stessa rotta va a MiniMax

NON VERIFICATO, da chiudere col probe:
- i nomi esatti dei model ID: i due mirror della doc divergono (`alibabacloud.com/help/en` dà `qwen3.7-max` e `qwen3.6-flash`, `help.aliyun.com/en` dà `qwen3.8-max` e `qwen3.7-flash`)
- i context window: la doc non li pubblica per i modelli 3.6/3.7/3.8. I due valori da 1M in `model_context_map` vengono da fonti TERZE concordi (`openrouter.ai`, `requesty.ai`), gli altri sono stime prudenziali
- i limiti RPM/TPM: le pagine della doc rispondono 404, il rate limiter usa placeholder
- i path DashScope di video, ASR e musica
- l'host internazionale del MCP WebSearch: la doc mostra quello cinese `dashscope.aliyuncs.com`

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

- Il ramo glm ha la stessa forma latente corretta nel ramo qwen: `forward_glm` ritorna una `web.Response` sui percorsi d'errore e il relay ci chiama `.release()` sopra. Non toccato perché fuori mandato; l'esito resta comunque un 502.
- Non esiste un equivalente di `m3-wiki` per Qwen: in modalità qwen l'hook della gerarchia rimanda i documenti a `qwen-code`.
