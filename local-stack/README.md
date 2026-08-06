# Stack Locale per ai-router-switch

Stack locale che permette al router di usare un modello LLM eseguito in locale tramite LiteLLM e llama.cpp. Il flusso è:

```
router (porta 8787)  →  LiteLLM (porta 4000)  →  llama.cpp (porta 8083)
```

## Cosa NON è incluso

I pesi del modello **non sono nel repository**. I file GGUF pesano decine di gigabyte e vanno scaricati a parte dall'huggingface del modello scelto. Il repository fornisce solo l'infrastruttura di orchestrazione.

## Requisiti

- Docker con il plugin Compose v2.
- llama.cpp compilato con supporto CUDA (o Vulkan/Metal a seconda dell'hardware).
- Un file GGUF del modello desiderato.
- VRAM o RAM sufficiente a contenere il modello in memoria. La scelta del modello, dell'hardware e della quantizzazione è a carico dell'utente. Il riferimento usato qui è **Qwen3-Coder-Next 80B-A3B** in quantizzazione MXFP4, contesto 131072, tutti i layer su GPU.

## Passi

### 1. Configurazione dei file example

Copiare i due file example togliendo il suffisso `.example`:

```bash
cp litellm.config.example.yaml litellm.config.yaml
cp litellm.env.example litellm.env
```

### 2. Chiave master di LiteLLM

Generare e inserire in `litellm.env` la chiave master che LiteLLM userà per autenticare i client:

```bash
# genera una chiave casuale sicura
openssl rand -hex 32
```

Impostare il valore risultante come `LITELLM_MASTER_KEY` in `litellm.env`.

### 3. Unit systemd di llama.cpp

Il template si trova in `llama-server.service.in`. Sostituire i tre segnaposto con i valori concreti:

| Segnaposto | Valore da inserire |
|---|---|
| `@LLAMA_BIN_DIR@` | Directory contenente il binario `llama-server` compilato |
| `@MODEL_PATH@` | Percorso assoluto al file GGUF scaricato |
| `@MODEL_ALIAS@` | Nome che llama.cpp dovrà annunciare (deve corrispondere a quello registrato in `litellm.config.yaml`) |

Copiare il file risultante nella directory delle unit di systemd e ricaricare:

```bash
sudo cp llama-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llama-server
```

### 4. Avvio di LiteLLM

Dalla directory `local-stack`:

```bash
docker compose up -d
```

### 5. Verifica del funzionamento

Controllare che LiteLLM risponda al suo endpoint di liveness:

```bash
curl http://127.0.0.1:4000/health/liveliness
```

Eseguire una chiamata di test al modello:

```bash
curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY litellm.env | cut -d= -f2)" \
  -d '{
    "model": "code-max",
    "messages": [{"role": "user", "content": "Ciao"}],
    "max_tokens": 10
  }'
```

### 6. Configurazione del router

Inserire in `.env` (nella config dir del router) oppure in `secrets/local-llm.env`:

```bash
LOCAL_LLM_API_KEY=<stesso valore di LITELLM_MASTER_KEY>
LOCAL_LLM_API_BASE=http://127.0.0.1:4000
```

Commutare il router in modalità locale:

```bash
ai-mode local
```

Per tornare alla modalità con modello remoto:

```bash
ai-mode mix-al
```

## Verifica

Si prova ogni anello separatamente, dal basso verso l'alto, così un guasto si localizza subito.

**llama.cpp:**

```bash
curl http://127.0.0.1:8083/health
```

**LiteLLM:**

```bash
curl http://127.0.0.1:4000/health/liveliness
```

**Router:**

```bash
curl http://localhost:8787/health
```

Se tutti e tre rispondono, lo stack è operativo.

## Problemi frequenti

### (a) Il compose non parte

LiteLLM si aspetta `litellm.env` nella stessa directory del compose. Se manca:

```
Error loading env file: litellm.env
```

Copiare il file example e riavviare:

```bash
cp litellm.env.example litellm.env
docker compose up -d
```

### (b) LiteLLM non raggiunge llama.cpp dal container

Dall'interno del container Docker, `127.0.0.1` riferisce al container stesso, non alla macchina host. In `litellm.config.yaml`, `api_base` deve puntare a:

```
api_base: http://host.docker.internal:8083
```

Riavviare il compose dopo la modifica.

### (c) Errore di autenticazione sul router

Il router restituisce 401 perché `LOCAL_LLM_API_KEY` non corrisponde a `LITELLM_MASTER_KEY`. Verificare che i due valori siano identici:

```bash
grep LITELLM_MASTER_KEY litellm.env
grep LOCAL_LLM_API_KEY ~/.config/ai-router-switch/.env
```

### (d) Generazioni lunghe interrotte

Il timeout di default è troppo basso per risposte estese. Alzarlo in due posti:

In `litellm.config.yaml`:

```yaml
litellm_settings:
  request_timeout: 300
```

E nel `.env` del router:

```bash
AIROUTER_LOCAL_TIMEOUT_SEC=300
```

## Nota sulla sicurezza

Il server llama.cpp avviato con `--host 0.0.0.0` è raggiungibile da tutta la rete locale. Questo è necessario quando LiteLLM gira in un container, perché il container deve poter contattare llama.cpp sulla macchina host.

Se llama.cpp e LiteLLM girano entrambi sulla stessa macchina **senza** container, conviene usare `--host 127.0.0.1` per ascoltare solo su localhost e non esporre il servizio alla rete.
