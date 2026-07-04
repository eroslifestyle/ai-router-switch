# MiniMax Platform — Capacità complete e integrazione in `ai-router-switch`

> Ricerca approfondita su <https://platform.minimax.io/docs> (2026-07-04).
> Fonte primaria: `llms.txt` + pagine `api-reference/*.md` e `guides/*.md`.
> Obiettivo: mappare TUTTE le funzionalità MiniMax (text, video, image, music, TTS, MCP, OCR, web search) e definire **come attivarle in modalità `minimax`** del router.
>
> **Decisione utente (2026-07-04): attivare tutto in ENTRAMBE le modalità — `minimax` (solo) E `mixed`.** Vedi §7bis per il comportamento per-modalità.

---

## 0. TL;DR operativo

| Capacità | API MiniMax | Endpoint | Come si integra nel nostro stack | Sforzo |
|---|---|---|---|---|
| **Testo/chat/agentic** | Chat Completions / Anthropic Messages | `POST /v1/chat/completions` · `/anthropic` | **Già attivo** — il router instrada qui in mode `minimax` | ✅ 0 |
| **OCR / vision** | Chat Completions **multimodale** (M3) | `POST /v1/chat/completions` con `image_url` | **Già possibile** via protocollo Anthropic (image blocks) → verifica passthrough | 🟢 basso |
| **Web search** | **Web Search MCP** (`minimax-coding-plan-mcp`) | MCP server (`uvx`) | MCP aggiunto a Claude Code, **gated su mode=minimax** | 🟢 basso |
| **Tool use / function call** | Chat API `tools` + interleaved thinking | `POST /v1/chat/completions` | Passthrough protocollo (già funziona) | ✅ 0 |
| **Image generation** | Text-to-Image / Image-to-Image | `POST /v1/image_generation` | Wrapper CLI/skill `m3-image` (REST separata) | 🟡 medio |
| **Video generation** | Hailuo T2V/I2V/S2V (async task) | `POST /v1/video_generation` + polling | Wrapper CLI/skill `m3-video` (async) | 🟡 medio |
| **Music generation** | Music-2.6 / Cover | `POST /v1/music_generation` | Wrapper CLI/skill `m3-music` | 🟡 medio |
| **TTS / voce** | speech-2.8 HD/turbo + voice clone | `POST /v1/t2a_v2` + WS | Wrapper CLI/skill `m3-tts` | 🟡 medio |

**Punto chiave architetturale:** il router `:8787` è un **proxy del protocollo Anthropic Messages** (VSCode/Claude Code → `:8787` → upstream). Solo `text` e `OCR/vision` viaggiano *dentro* quel protocollo e quindi passano dal router senza modifiche. Tutto il resto (image/video/music/TTS/web-search) usa **endpoint REST nativi `api.minimax.io/v1/*`** o **MCP**, quindi NON transita dal router: va esposto come **tool/skill/CLI separati**, attivi solo quando `ai-mode == minimax`.

---

## 1. Architettura attuale del router (contesto)

Fonte: [`src/ai-router-proxy.py`](../src/ai-router-proxy.py), [`router-mode/card.py`](../router-mode/card.py).

- **Punto unico**: `:8787` (più porte per-mode `8771/8772/8773`). App → `ANTHROPIC_BASE_URL=:8787`.
- **Modalità** (`~/.claude/ai-router-mode`):
  - `anthropic` — tutto → `api.anthropic.com`
  - `minimax` — tutto → `api.minimaxi.chat/anthropic` (endpoint **Anthropic-compat** di MiniMax). M3 orchestra, M2.7 esegue.
  - `mixed` — Anthropic primario, fallback MiniMax su 429/5xx.
  - `inverse` — speculare.
- **Natura del proxy**: transparent forward su `request.path_qs` (`forward_anthropic` / `forward_minimax`). Instrada il **protocollo `/v1/messages`**, non le REST generative.
- **Upstream MiniMax attuale** = `api.minimaxi.chat/anthropic` (Anthropic-compat). Le REST native stanno su **`api.minimax.io/v1/*`** → dominio/protocollo diversi.

> Conseguenza: image/video/music/TTS/web-search **non sono "instradabili"** dal router così com'è. Si attivano come strumenti collaterali che il main invoca quando la mode è `minimax`.

---

## 2. Catalogo modelli MiniMax (2026-07)

### 2.1 Text / LLM
| Modello | Note | Context | Output max |
|---|---|---|---|
| **MiniMax-M3** | multimodale (testo+**image/video understanding**), frontier coding, tool use, interleaved thinking | **1M** | 524.288 tk |
| **MiniMax-M2.7** / **-highspeed** | real-world engineering, office delivery; hs = inferenza più veloce | — | — |
| MiniMax-M2.5 / -hs | code-gen & refactor (legacy) | — | — |
| MiniMax-M2.1 / -hs | 230B tot / 10B attivi (legacy) | — | — |
| MiniMax-M2 | legacy | 200k | 128k |

### 2.2 Video (Hailuo)
- `MiniMax-Hailuo-2.3`, `MiniMax-Hailuo-02`, `T2V-01-Director`, `T2V-01`
- 1080p (6s) / 768p (6–10s), 24fps. Text-to-video, image-to-video, subject-ref, first&last-frame.

### 2.3 Image
- `image-01` (unico). T2I + I2I.

### 2.4 Music
- `music-2.6` (raccomandato, paid), `music-cover` (paid), `music-2.6-free`, `music-cover-free`.

### 2.5 Speech / TTS
- `speech-2.8-hd`, `speech-2.8-turbo` (40 lingue), legacy 2.6/02. Voice clone + voice design.

---

## 3. OCR — analisi e implementazione ⭐

### 3.1 Come funziona su MiniMax
**Non esiste un endpoint OCR dedicato.** L'OCR si fa via **Chat Completions multimodale** con **MiniMax-M3** (che ha "Image and video understanding"):

- Endpoint: `POST https://api.minimax.io/v1/chat/completions`
- Input immagine: `image_url` (URL **o** base64), ≤ **10 MB**, formati **JPEG/PNG/GIF/WebP**
- `detail`: `low` / `default` / `high` (~600 → 15k+ token, controlla costo)
- Message content = array `[{type:text, text:"Estrai il testo"}, {type:image_url, image_url:{url:"data:image/png;base64,..."}}]`

### 3.2 Come si attiva in modalità `minimax`
**Buona notizia: l'OCR passa DENTRO il protocollo Anthropic Messages** → il router lo instrada già oggi, a patto che:

1. **Il client** (Claude Code / app) invii `image` content-block nel formato Anthropic (`{"type":"image","source":{"type":"base64",...}}`).
2. **L'endpoint MiniMax Anthropic-compat** (`api.minimaxi.chat/anthropic`) accetti gli image-block e li mappi su M3 vision. → **DA VERIFICARE** (le doc vision documentano il formato OpenAI `image_url`; il gate Anthropic-compat potrebbe non tradurlo).

**Due strade:**

- **(A) Passthrough puro (sforzo minimo).** Se il gate `/anthropic` supporta gli image-block, l'OCR funziona **senza toccare il router**: basta essere in mode `minimax` e mandare l'immagine. → *Test necessario prima di dichiararlo funzionante.*
- **(B) Tool/CLI dedicato `m3-ocr` (fallback robusto).** Se il gate Anthropic NON gestisce le immagini, si crea un piccolo tool che chiama direttamente `api.minimax.io/v1/chat/completions` con M3 + `image_url`. Sganciato dal router, invocabile dal main solo in mode minimax.

**Raccomandazione:** verificare prima (A) con un test one-shot (immagine→testo via `:8787` in mode minimax). Se fallisce, implementare (B) come `m3-ocr <file|url>` sul modello dei tool `m3-*` già esistenti (`m3-code`, `m3-web`).

### 3.3 Snippet di riferimento (strada B, REST nativa)
```bash
# m3-ocr: estrazione testo da immagine locale via M3 vision
IMG_B64=$(base64 -w0 "$1")
curl -s https://api.minimax.io/v1/chat/completions \
  -H "Authorization: Bearer $MINIMAX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "MiniMax-M3",
    "messages": [{"role":"user","content":[
      {"type":"text","text":"Estrai TUTTO il testo dell'\''immagine, verbatim, senza commenti."},
      {"type":"image_url","image_url":{"url":"data:image/png;base64,'"$IMG_B64"'","detail":"high"}}
    ]}]
  }' | jq -r '.choices[0].message.content'
```

---

## 4. Web Search — analisi e implementazione ⭐

### 4.1 Come funziona su MiniMax
Il web search è esposto come **MCP server** (`minimax-coding-plan-mcp`), NON come feature della Chat API. Tool primario: **`web_search(query: string)`** → "returns search results and related suggestions".

**Prerequisito:** `uvx` (uv package manager).
**Auth:** `MINIMAX_API_KEY` con Token Plan seat / Credits.

Install per Claude Code (ufficiale):
```bash
claude mcp add -s user MiniMax \
  --env MINIMAX_API_KEY=your_key \
  --env MINIMAX_API_HOST=https://api.minimax.io \
  -- uvx minimax-coding-plan-mcp -y
```

### 4.2 Come si attiva SOLO in modalità `minimax`
L'MCP è indipendente dal router. Per rispettare "solo minimax", si vuole che `web_search` sia **disponibile a Claude Code solo quando `ai-mode == minimax`**. Tre opzioni:

| Opzione | Meccanismo | Pro | Contro |
|---|---|---|---|
| **Gate via env/hook** | Hook `SessionStart`/`PreToolUse` che abilita/disabilita l'MCP in base a `~/.claude/ai-router-mode` | Automatico, coerente con lo stack hook esistente | MCP non caricati a metà sessione non si attivano senza `/mcp` o restart |
| **Wrapper CLI `m3-web` (GIÀ ESISTE)** | Nel globale c'è già `m3-web "query"` come web-search di default | Zero nuova infra, sempre disponibile | Non è "gated" per mode |
| **Script `ai-mode`** | `ai-mode minimax` aggiunge l'MCP; le altre mode lo rimuovono | Attivazione esplicita e pulita | Richiede restart Claude Code per prendere l'MCP |

**Osservazione importante:** il sistema globale usa **già** `m3-web "query"` (MiniMax-M3) come motore di ricerca web di default (regola CLAUDE.md "WEB-SEARCH = `m3-web`"). Quindi **il web search MiniMax è di fatto già attivo a livello utente**. L'MCP ufficiale è utile se si vuole il tool `web_search` *dentro* Claude Code (tool-call nativo invece di CLI).

**Raccomandazione:**
1. Usare `m3-web` come default (già in produzione).
2. Aggiungere l'MCP `MiniMax` **solo se** serve il tool-call nativo in-editor, e **gate-arlo su mode=minimax** legandolo allo script `ai-mode` (add on `minimax`, remove sulle altre) — perché "attivare tutto in solo-minimax" è la direttiva.

---

## 5. Image / Video / Music / TTS — API generative

Tutte REST native `api.minimax.io/v1/*`, **fuori dal protocollo del router** → si integrano come **tool/skill CLI** (famiglia `m3-*`), invocabili dal main quando mode=minimax.

### 5.1 Image — `POST /v1/image_generation`
- Modello: `image-01`. `prompt` (≤1500 char), `aspect_ratio` (1:1/16:9/…), `n` (1–9), `width/height` (512–2048, /8), `response_format` (url/base64), `seed`, `prompt_optimizer`.
- Output: `image_urls[]` o `image_base64[]`.

### 5.2 Video — `POST /v1/video_generation` (**async**)
- Modelli: `MiniMax-Hailuo-2.3`, `MiniMax-Hailuo-02`, `T2V-01-Director`, `T2V-01`.
- `prompt` (≤2000, camera cmd `[Pan left]`/`[Zoom in]`), `duration` (6/10), `resolution` (720P/768P/1080P), `callback_url`.
- Flusso: request → `task_id` → **polling** Query Task → download.

### 5.3 Music — `POST /v1/music_generation`
- Modelli: `music-2.6` / `music-cover` (+ free tier). `prompt` (strumentale), `lyrics` (`[Verse]`/`[Chorus]`), `is_instrumental`, `lyrics_optimizer`, `audio_setting`, `output_format` (url/hex).

### 5.4 TTS — `POST /v1/t2a_v2` (+ WebSocket, async long)
- `speech-2.8-hd`/`-turbo` (40 lingue), voice clone (`voice_cloning/*`), voice design. Sync HTTP, WS streaming, async per testi lunghi.

### 5.5 Pattern d'integrazione comune
- Un tool per capacità: `m3-image`, `m3-video`, `m3-music`, `m3-tts`, `m3-ocr` — allineati ai `m3-code`/`m3-web` esistenti.
- **Gate mode**: ogni tool legge `~/.claude/ai-router-mode`; se ≠ `minimax` → warn/no-op (rispetta "solo minimax"). Oppure li abilita/disabilita `ai-mode`.
- Auth: `MINIMAX_API_KEY` da `secrets/` (mai hardcode — regola security R1).
- Async (video): helper di polling con timeout + backoff (riusa il pattern rate-limit già in `ai-router-proxy.py`).

---

## 6. MCP & Tool Use

- **Web Search MCP** (`minimax-coding-plan-mcp`) → §4.
- **Tool use / function calling** (Chat API `tools`) + **interleaved thinking** su M3: passthrough del protocollo, già funzionante. Regola critica: **rimandare l'intera `response.content`** (inclusi i blocchi thinking) nella history per mantenere la catena di reasoning.
- SDK: Anthropic (`<think>` auto-preservati) e OpenAI (`reasoning_split` true/false).

---

## 7. Piano d'implementazione consigliato (attivo in `minimax` E `mixed`)

Ordine per rapporto valore/sforzo:

1. **OCR (verifica passthrough)** — test: immagine via `:8787` in mode minimax. Se OK → 0 lavoro. Se KO → tool `m3-ocr` (REST nativa, §3.3). *[priorità utente]*
2. **Web search** — confermare `m3-web` come default (già live) e, opzionale, aggiungere l'MCP `MiniMax` gated su `ai-mode minimax`. *[priorità utente]*
3. **Image** — tool `m3-image` (sync, semplice).
4. **Music** — tool `m3-music` (sync).
5. **TTS** — tool `m3-tts` (sync + async long).
6. **Video** — tool `m3-video` (async con polling, il più complesso).

**Gate trasversale (minimax E mixed):** i tool `m3-*` generativi + web-search sono l'**unica fonte** per quelle capacità (Anthropic non le offre) → **sempre attivi in `minimax` e `mixed`** (no gate/no-op). Lo script `ai-mode` NON deve disabilitarli cambiando mode. L'unica differenza per-mode:
- **OCR/vision**: in `mixed` preferisci l'orchestratore Anthropic (nativo); in `minimax` usa M3 vision. Un solo `m3-ocr` copre entrambi.
- **Web search**: `m3-web` uguale in entrambe; l'MCP MiniMax (se aggiunto) va registrato per entrambe le mode.

**Vincoli (regole progetto):**
- `MINIMAX_API_KEY` da secrets, mai in git.
- I tool sono CODICE → li scrive MiniMax (`m3-code`), il main verifica/committa (regola gerarchia V23).
- Ogni tool nella cartella progetto (`scripts/` o `sviluppo/`), niente file sparsi.

---

## 7bis. Comportamento per-modalità: `minimax` (solo) vs `mixed`

Le capacità NON generative-native (image/video/music/TTS/web-search/OCR-tool) sono **strumenti collaterali** che vivono fuori dal router: quindi **funzionano in qualsiasi mode**. La differenza tra `minimax` e `mixed` sta in **chi orchestra** e **quale motore serve la capacità quando esiste un'alternativa Anthropic**.

| Capacità | In mode **`minimax`** (solo) | In mode **`mixed`** (Anthropic primario + fallback MiniMax) |
|---|---|---|
| **Testo/chat** | tutto → MiniMax (M3 orch / M2.7 act) | Anthropic primario; fallback MiniMax su 429/5xx (già così) |
| **OCR / vision** | via MiniMax-M3 vision (passthrough Anthropic-compat o tool `m3-ocr`) | **Anthropic vision NATIVA** (Opus/Sonnet leggono immagini) → primaria; su fallback usa M3 vision. **In mixed l'OCR funziona già oggi** senza MiniMax | 
| **Web search** | `m3-web` (MiniMax) / MCP MiniMax | **Stesso `m3-web`** (regola globale: web-search = m3-web in OGNI mode). Alternativa: WebSearch Anthropic se M3 giù | 
| **Tool use / function call** | M3 tools + interleaved thinking | Anthropic tools primari; MiniMax su fallback (passthrough protocollo) |
| **Image / Video / Music / TTS** | tool `m3-*` (MiniMax, unica fonte) | **Identico** — Anthropic non ha queste API → sempre MiniMax, in ogni mode |

**Conseguenze pratiche:**

1. **I tool generativi `m3-*` (image/video/music/tts) NON vanno gate-ati sulla mode.** Sono l'unica fonte per quelle capacità → devono essere disponibili sia in `minimax` sia in `mixed` (e volendo anche in `anthropic`, dato che Anthropic non le offre). Il gate su `minimax` proposto in §4/§5 va **rimosso**: si tengono sempre attivi.
2. **OCR ha due percorsi ottimali diversi per mode:**
   - `mixed` → lascia che l'**orchestratore Anthropic** legga l'immagine nativamente (zero costo MiniMax, già funziona). Il tool `m3-ocr` resta come fallback quando MiniMax subentra.
   - `minimax` → OCR servito da **M3 vision** (passthrough o `m3-ocr`).
   - Un unico tool `m3-ocr` copre entrambi; in `mixed` è semplicemente opzionale.
3. **Web search è già uniforme**: `m3-web` è il default in tutte le mode per regola globale → nessuna differenza `minimax`/`mixed`. L'MCP MiniMax (tool-call in-editor), se aggiunto, va reso disponibile in **entrambe** le mode, non solo `minimax`.

**Gate rivisto (`ai-mode`):** invece di "abilita solo in minimax", la regola diventa **"i tool `m3-*` generativi + web-search sono sempre attivi (minimax E mixed); l'orchestratore cambia solo il routing del testo/vision"**. Nessun no-op per mode sui tool generativi.

---

## 8. Domande aperte / da verificare (non allucinare)

- ⬜ Il gate `api.minimaxi.chat/anthropic` **traduce gli image-block Anthropic** su M3 vision? (decide OCR strada A vs B). *(non verificato)*
- ⬜ Rate limit specifici per image/video/music vs text (§ rate-limits doc). *(non recuperato in dettaglio)*
- ⬜ Costo per-immagine OCR con `detail:high` (~15k token) su budget MiniMax.
- ⬜ `minimax-coding-plan-mcp`: espone solo `web_search` o anche altri tool (image/tts)? *(doc cita solo web_search)*

---

## 9. Fonti

- `https://platform.minimax.io/docs/llms.txt` (indice completo)
- `guides/models-intro.md`, `api-reference/text-chat-openai.md` (chat + vision/OCR)
- `guides/text-m3-function-call.md` (tool use / thinking)
- `token-plan/mcp-guide.md` (Web Search MCP)
- `api-reference/image-generation-t2i.md`, `video-generation-t2v.md`, `music-generation.md`
- Codice locale: `src/ai-router-proxy.py`, `router-mode/card.py`
