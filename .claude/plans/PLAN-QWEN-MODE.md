---
name: plan-modalita-qwen-router-2026-08-03
description: Piano tecnico per aggiungere la 7ª modalità pura "qwen" (Alibaba Cloud Model Studio) al router :8787, con THINK e ACT su Qwen e tutti i servizi nativi
updated: 2026-08-03
metadata:
  type: project
---

# PLAN — Modalità `qwen` (Alibaba Cloud Model Studio), 7ª modalità del router
Data: 2026-08-03 · Repo: ai-router-switch · Branch: main

## Obiettivo
Aggiungere una 7ª modalità pura `qwen` al router :8787, con THINK e ACT entrambi su Qwen, tutti i servizi nativi Model Studio attivi (LLM, generativi, MCP web search, embeddings/rerank) e ZERO modifiche di comportamento alle 6 modalità esistenti (anthropic, minimax, mix-am, mix-ag, mix-gm, glm). Vincolo architetturale: il router resta un tunnel trasparente; la gerarchia THINK/ACT/VERIFY/escalation vive nella config globale, non nel proxy.

## 1. Cosa dice la documentazione ufficiale (verificato)

### 1.1 Endpoint Anthropic-compatible — il cuore della modalità
Tabella (voce | valore):
- URL HTTP | POST https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1/messages
- Base URL SDK | .../apps/anthropic (SENZA /v1 finale — altrimenti il client compone /v1/v1/models)
- Auth | `x-api-key: $DASHSCOPE_API_KEY` oppure `Authorization: Bearer $DASHSCOPE_API_KEY`
- Campi richiesta | model, max_tokens (obbligatorio), messages, system, stream, temperature, top_p, top_k, stop_sequences, thinking, tools, tool_choice, output_config
- thinking | {type: enabled|disabled, budget_tokens: int}
- output_config | estensione Qwen: {effort: high|max, format: <json-schema>}
- Blocchi content | text, image (url/base64), video (url/base64), tool_use, tool_result, thinking
- Prompt caching | cache_control {type: "ephemeral"}; usage riporta cache_creation_input_tokens / cache_read_input_tokens
- stop_reason | end_turn | max_tokens | tool_use
- NON supportato | /v1/models → 404 (innocuo, il client lo ignora); NESSUNA web search built-in su questo endpoint

Fonti: https://www.alibabacloud.com/help/en/model-studio/anthropic-api-messages e https://www.alibabacloud.com/help/en/model-studio/claude-code

Conseguenza di design: la forma è identica a z.ai/GLM, quindi qwen_backend.py ricalca glm_backend.py ma più snello (niente peak scheduler, niente classificatore di tier via MiniMax, niente _ANTHROPIC_BLOCKED).

### 1.2 Servizi nativi (endpoint separati, host DashScope)
Tabella (servizio | path | modelli):
- Image gen | /api/v1/services/aigc/multimodal-generation/generation (sync) | qwen-image-2.0-pro, qwen-image-3.0-pro, wan2.7-image-pro
- Video gen | task async (creazione + polling) | happyhorse-1.1-t2v, happyhorse-1.1-i2v, happyhorse-1.1-r2v, happyhorse-1.0-video-edit
- TTS | /api/v1/services/aigc/multimodal-generation/generation + header X-DashScope-SSE: enable | qwen3-tts-flash, qwen3-tts-instruct-flash, qwen-audio-3.0-tts-plus
- ASR | idem | fun-asr, fun-asr-realtime, qwen-audio-3.0-asr-flash-*
- Music | idem | fun-music-v1
- Embeddings | /compatible-mode/v1/embeddings | text-embedding-v4 (dimensioni da 2048 a 64), qwen3.7-text-embedding
- Rerank | DashScope | qwen3-rerank
- Web search | MCP remoto https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp, transport http, Authorization Bearer | —
- Web search (alternativa) | enable_search: true + search_options.search_strategy (agent / agent_max) su /compatible-mode/v1/chat/completions | qwen3.x-max/plus/flash

Fonti: https://help.aliyun.com/en/model-studio/qwen-image-api , https://help.aliyun.com/en/model-studio/qwen-tts-api , https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api , https://www.alibabacloud.com/help/en/model-studio/web-search , https://qwenlm.github.io/qwen-code-docs/en/developers/tools/web-search/

### 1.3 Incertezze dichiarate (da risolvere con probe live, non da indovinare)
1. Catalogo modelli divergente tra i due mirror della doc: alibabacloud.com/help/en dà qwen3.7-max, qwen3.7-plus, qwen3.6-flash; help.aliyun.com/en dà qwen3.8-max, qwen3.7-plus, qwen3.7-flash. Dipende dalla regione/tenant. I nomi vanno verificati vivi contro l'account.
2. Context window NON documentati per i modelli 3.6/3.7/3.8 (l'unico dato trovato è qwen-max legacy = 32.768). model_context_map.py va popolato dopo il probe, con commento "# NON VERIFICATO" sui valori prudenziali finché non confermati.
3. Rate limit RPM/TPM non reperiti (pagine 404). Limiter con placeholder espliciti, stessa convenzione già usata per GLM.
4. Host MCP WebSearch: la doc mostra l'host CN (dashscope.aliyuncs.com). L'equivalente Singapore va verificato prima di dichiararlo funzionante.

## 2. Blocco credenziali (richiede intervento dell'utente)
Sulla macchina NON esiste alcuna credenziale Model Studio: verificato in ~/.claude/secrets/secrets.sh, nelle variabili d'ambiente e in ~/.qwen/ (la CLI qwen-code installata oggi non ha credenziali salvate).
Servono due valori: DASHSCOPE_API_KEY (API key Model Studio, regione Singapore — le chiavi non sono interscambiabili tra regioni) e WorkspaceId (pagina Workspace Details della console).
Vanno in ~/.claude/secrets/secrets.sh come qwen.api_key e qwen.workspace_id (stesso meccanismo di glm.api_key), mai nel repo, mai nei log.
Finché mancano: il codice viene scritto e testato comunque (config-driven, i test usano fake upstream), ma la modalità risponde 502 "qwen key missing" — esattamente come fa oggi GLM senza chiave.

## 3. Decisioni prese
Tabella (decisione | scelta):
- Endpoint | Singapore workspace-dedicated {WorkspaceId}.ap-southeast-1.maas.aliyuncs.com
- Ampiezza | Tutto: LLM + generativi + MCP WebSearch + embeddings/rerank + CLI
- THINK | qwen3.7-max (oppure qwen3.8-max se il probe lo trova disponibile)
- ACT | qwen3-coder-plus
- Escalation (solo esecuzione, dopo 2 fail) | qwen3-coder-plus → qwen3.7-plus → qwen3.7-max. MAI Anthropic, mai MiniMax, mai GLM (stesso principio di isolamento della modalità glm pura)
- Config globale | riga qwen completa in ~/.claude/CLAUDE.md + set delegante di enforce_hierarchy.py

## 4. Implementazione — file toccati
Tutte le modifiche ai file esistenti sono ADDITIVE (nuove voci in tabelle/dizionari, un nuovo ramo elif). Nessuna riga di comportamento delle altre 6 modalità viene alterata.

### 4.1 src/qwen_backend.py — NUOVO (~350 righe)
Modellato su glm_backend.py, senza le parti che a Qwen non servono. API previste:
- get_qwen_key(): env QWEN_API_KEY / DASHSCOPE_API_KEY, poi secrets.sh qwen.api_key, cache 60s
- get_qwen_workspace(): env QWEN_WORKSPACE_ID / secrets.sh qwen.workspace_id
- qwen_upstream(): costruisce https://{ws}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic; fallback dashscope-intl se workspace assente; override QWEN_API_BASE
- QWEN_TIER_TOP / MID / CODER: qwen3.7-max / qwen3.7-plus / qwen3-coder-plus
- resolve_qwen_upstream_model(tier)
- set_body_model(body, model): riscrive il campo model (Qwen onora il body, come z.ai)
- clamp_qwen_max_tokens(): clamp al max output del modello (valore da probe)
- QwenRateLimiter: sliding window RPM/TPM per modello + cooldown 429 (copia della classe GLM)
- forward_qwen(...): passthrough per lo stream, retry 2 tentativi (429 backoff, 5xx), timeout sock_read=120 in stream e NON_STREAM_SOCK_READ_SEC altrimenti, header x-api-key + Authorization, tool_isolation come choke-point, NESSUN "async with" sulla response (bug della release prematura, cfr. glm)
- forward_qwen_image / video / tts / asr / music / embedding / rerank
- route_qwen_generative(...)

Cautele già pagate su GLM, da replicare testualmente: import LAZY di router_utils (il ciclo router_constants → qwen_backend → router_utils manderebbe QWEN_AVAILABLE=False in silenzio); resp restituito FUORI da async with in passthrough; NON_STREAM_SOCK_READ_SEC sulle richieste non-streaming.

### 4.2 src/role_routing.py — additivo
- override QWEN_THINK = "qwen3.7-max", QWEN_ACT = "qwen3-coder-plus"
- ROUTING_TABLE: ("qwen", ROLE_THINK) → ("qwen", QWEN_THINK); ("qwen", ROLE_ACT) → ("qwen", QWEN_ACT)
- _MODE_DEFAULT_PROVIDER["qwen"] = "qwen"
- _NATIVE_EXECUTOR["qwen"] = QWEN_ACT
- VALID_MODES += "qwen"
- model_provider(): riconosce qwen / qwq / qvq / wan / happyhorse → "qwen". Serve alla nativizzazione: un modello straniero in modalità pura verrebbe altrimenti inoltrato tale e quale (è il bug 404 del 2026-08-01)

### 4.3 src/router_constants.py — additivo
- VALID_MODES += "qwen"
- PORT_MODE[8778] = "qwen" (porta libera: 8771-8777 già assegnate)
- QWEN_UPSTREAM, QWEN_WORKSPACE_ID, QWEN_THINK/ACT, QWEN_RATE_LIMITS, QWEN_SAFETY
- blocco try: import qwen_backend → QWEN_AVAILABLE, gemello di GLM_AVAILABLE

### 4.4 src/ai-router-proxy.py — additivo
- nuovo ramo elif _provider == "qwen": nel dispatch tunnel (copia strutturale del ramo GLM: set_body_model → forward_qwen(passthrough=True) → relay(final_override=f"qwen:{model}"))
- _ctx_model_map["qwen"] = "qwen3.7-max"; _provider_ctx_model_map["qwen"] = "qwen3.7-max"
- healthz: blocco qwen con snapshot del limiter
- route generative /v1/images/generations, /v1/videos/generations, /v1/music/generations, /v1/audio/speech: oggi sono CABLATE su MiniMax. Diventano mode-aware: se mode == "qwen" → DashScope, altrimenti percorso MiniMax IDENTICO a oggi (nessuna regressione per le altre modalità)

### 4.5 src/tool_isolation.py — additivo
- is_qwen_branded_tool(): mcp__qwen__*, mcp__dashscope__*, mcp__websearch__* (Bailian), nome contenente "dashscope"
- _BRAND_CHECK["qwen"], brand_of_tool_name(), backend_from_final() → "qwen"
- ATTENZIONE al bug noto: is_anthropic_server_tool() classifica come Anthropic ogni tool privo di input_schema. I tool MCP Bailian vanno esclusi lì, come già fatto per mcp__zai__ e MiniMax, altrimenti in modalità qwen viene strippato il tool nativo Qwen.

### 4.6 src/model_context_map.py — additivo
Voci per qwen3.7-max, qwen3.7-plus, qwen3.6-flash, qwen3-coder-plus, qwen3-coder-next, qwen3-vl-plus. Valori dal probe; finché non confermati, commento "# NON VERIFICATO" (convenzione già in uso nel file).

### 4.7 scripts/ai-mode — additivo
"qwen" nella case e nell'usage. src/router_mode.py legge VALID_MODES: si adegua da solo.

### 4.8 Strumenti nativi lato client
- MCP WebSearch Bailian in ~/.claude/.mcp.json, con la chiave da env — attivo solo in modalità qwen, isolato dalle altre da tool_isolation
- CLI qwen-web (~/.local/bin, sul modello di m3-web): web search via enable_search + search_strategy: agent
- CLI qwen-code (sul modello di m3-code): esecutore di codice via qwen3-coder-plus, necessario perché la gerarchia globale impone che il THINK non scriva codice

### 4.9 Config globale ~/.claude/CLAUDE.md
Riga aggiunta alla tabella FLOW GERARCHICO (nessuna riga esistente toccata):
Modalità qwen | THINK qwen3.7-max | ESEGUE qwen3-coder-plus | VERIFY chi ha fatto il THINK | Fail 2x | Escalation coder-plus→3.7-plus→3.7-max, mai Anthropic/MiniMax/GLM
Più: "qwen" nell'elenco delle modalità del router e nel set delegante di ~/.claude/hooks/enforce_hierarchy.py (deny sulle scritture di codice di progetto, delega a qwen-code; i .md a m3-wiki o equivalente).

## 5. Test
Tabella (test | dove | cosa prova):
- tests/test_role_routing.py | esteso | routing qwen THINK/ACT, nativizzazione di un modello straniero, le 6 modalità esistenti INVARIATE (controprova di non-regressione)
- sviluppo/tests/test_qwen_mode.py | nuovo | forward_qwen con fake aiohttp: header, URL composto, retry 429/5xx, passthrough non rilasciato, clamp max_tokens, chiave assente → 502
- sviluppo/tests/test_qwen_tool_isolation.py | nuovo | tool Qwen sopravvive in modalità qwen; tool MiniMax/GLM/Anthropic strippati; tool Qwen strippato nelle altre modalità
- sviluppo/tests/test_qwen_mode.sh | nuovo | istanza ISOLATA via AIROUTER_PORT_MODE_JSON (non tocca :8787 live), sul modello di test_glm_modes.sh
- Probe live | sviluppo/tools/probe_qwen.py | con la chiave: quali model ID rispondono davvero, context window reale, streaming, tools, cache_control
- Regressione | suite intera | il totale dei test prima e dopo deve coincidere (trappola nota: sys.modules cachato tra file di test)

## 6. Ordine di esecuzione
1. qwen_backend.py + role_routing + router_constants + test di routing → suite verde
2. Ramo dispatch nel proxy + tool_isolation + model_context_map → test isolati verdi
3. Probe live con le credenziali → correzione dei model ID e dei context window reali
4. Route generative mode-aware + MCP WebSearch + CLI qwen-web/qwen-code
5. Config globale ~/.claude/CLAUDE.md + enforce_hierarchy.py
6. Restart del router secondo la procedura obbligatoria del CLAUDE.md di progetto (verifica systemctl --user is-active ai-router e Restart= PRIMA di toccarlo), poi verifica live in modalità qwen
7. Commit + push (Conventional), un commit per fase

## 7. Do NOT
- Non toccare la logica delle 6 modalità esistenti: solo aggiunte a tabelle e un elif
- Non cablare i model ID: tutto da costante/env, verificato col probe
- Non dichiarare "funziona" senza output letterale (evidence-gate)
- Non mettere la chiave nel repo, nei log, nei commit
- Non riavviare il router a freddo con kill/pkill

**Why:** Estendere il router a un secondo vendor Anthropic-compatible (dopo GLM) con scope completo (LLM + generativi + MCP + CLI) mantenendo l'isolamento totale dalle 6 modalità già operative. L'architettura a tunnel trasparente e la delega della gerarchia al config globale obbligano a modifiche solo additive su tabelle/dizionari/ramo elif.

**How to apply:** Seguire l'ordine di esecuzione in 7 fasi, una commit per fase. Prima del restart del router eseguire `systemctl --user is-active ai-router` per la procedura obbligatoria. Senza DASHSCOPE_API_KEY + WorkspaceId la modalità risponde 502 "qwen key missing" — è il comportamento atteso, non un bug. Tutti i model ID e i context window sono da verificare via probe live prima di rimuovere il commento "# NON VERIFICATO" in model_context_map.py.
