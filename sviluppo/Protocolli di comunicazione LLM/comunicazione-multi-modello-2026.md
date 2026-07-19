# Piano: Comunicazione Bilaterale Multi-Modello — Best Practice 2026

## Context

Il router `ai-router-switch` già implementa pipeline THINK-ACT-VERIFY con 3 modelli (Anthropic/MiniMax/GLM), ma manca un protocollo strutturato per la comunicazione inter-modello. Obiettivi:
- **Zero allucinazioni**: verifica bilaterale cross-model
- **Zero incongruenze**: formato messaggio strutturato standard
- **Zero incompatibilità**: adapter layer per API diverse
- **Massimo risparmio token**: compressione intelligente + prompt caching

---

## Research Findings

### Protocolli 2026
| Protocollo | Ruolo | Status |
|---|---|---|
| **MCP** (Model Context Protocol) | Agent→Tool/Data | Maturità max, donato Linux Foundation |
| **A2A** (Agent-to-Agent) | Agent↔Agent collaboration | Google, emergente |
| **ACP** (Agent Comm. Protocol) | Orchestrazione multi-agent | Meno maturo |

Per il nostro proxy: MCP è già usato internamente (tool calls). A2A è per orchestrazione esterna — non applicabile.

### Best Practice Comunicazione Inter-Modello

1. **Formato strutturato invece di testo libero**
   - JSON Schema per piani/risultati tra modelli
   - Esempio RouteLLM: `{query_classification, model_recommendation, confidence}`

2. **Chain-of-Verification (CoVe)** — riduce allucinazioni 30-40%
   - Modello A produce output
   - Modello B verifica factual claims
   - A revisiona se necessario

3. **Cross-model Self-Consistency**
   - Stesso input → N modelli diversi
   - Voto majority per risposte critiche

### Compressione Token 2026

| Tecnica | Risparmio | Applicabilità |
|---|---|---|
| **Prompt Caching** (Anthropic) | ~90% cached input | Già in uso parziale |
| **LLMLingua** | 2-5x compressione | Integrazione Python disponibile |
| **Semantic Caching** | 100% (cache hit) | Con embeddings |
| **Adaptive Context Trim** | 20-50% | Già implementato (Layer 2) |

### LLM Router Best Practice (Redis Blog 2026)

- Hot path: routing < 1ms, auth/rate-limit in-memory
- Fallback chains: mai singolo fallback — sempre 3+ hop
- Latency overhead: target < 20ms

---

## Recommended Approach

### 1. Structured Inter-Model Protocol (nuovo file: `src/imcp.py`)

```python
# Inter-Model Communication Protocol — formato standard per comunicazione bilaterale

# Schema piano tra orchestrator e executor
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {"type": "string", "description": "Piano d'azione"},
        "tools_to_call": {"type": "array", "items": {"type": "string"}},
        "executor_model": {"type": "string"},  # M2.7 / M3 / Haiku / Sonnet
        "self_review_ok": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "constraints": {"type": "object"}  # max_tokens, temperature, timeout
    }
}

# Schema risultato da executor a orchestrator
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "output": {"type": "string"},
        "tools_executed": {"type": "array"},
        "verification_needed": {"type": "boolean"},
        "confidence": {"type": "number"},
        "token_used": {"type": "integer"}
    }
}

# Schema verifica bilaterale
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "factual_checks": [{"claim": "string", "verified": "boolean"}],
        "inconsistencies": {"type": "array", "items": {"type": "string"}},
        "revision_required": {"type": "boolean"},
        "confidence_score": {"type": "number"}
    }
}
```

### 2. Bilateral Verification Layer (integra in `src/verify_layer.py`)

Implementa CoVe-style verification:

```python
async def bilateral_verify(original: str, claims: list[str], verifier_model: str) -> VerifyResult:
    """Verifica claims factual contro fonte. Usa modello diverso dall'executor."""
    # Claims estratti con NLP leggero (no token extra significativo)
    # Verifier model = modello più piccolo che garantisce accuratezza
    # Cross-model: se executor = M3, verifier = Sonnet/Haiku
```

**Trigger policy:**
- Output > 500 token → bilaterale verificato
- Output < 500 token → self-consistency check interno
- Claims > 5 → CoVe completo

### 3. Adapter Layer per Compatibilità

Già parzialmente implementato. Espandi `src/providers/base.py`:

```python
class ModelAdapter(ABC):
    """Adapter base per API provider."""

    # Conversione request Anthropic-format → provider-format
    def to_provider(self, body: dict) -> dict: ...

    # Conversione risposta provider → Anthropic-format
    def from_provider(self, response: dict) -> dict: ...

    # Estrazione token usage
    def extract_usage(self, response: dict) -> Usage: ...

# Implementazioni
class AnthropicAdapter(ModelAdapter): ...
class MiniMaxAdapter(ModelAdapter): ...  # Già esistente come passthrough
class GLMAdapter(ModelAdapter): ...       # Estendi da src/glm_backend.py
```

### 4. Compressione Intelligente (miglioramenti)

**a) LLMLingua Integration** (`src/compression/llmlingua_compress.py`):
```python
# Dipendenza: pip install llmlingua
from llmlingua import PromptCompressor

class LLMLinguaCompressor:
    def __init__(self):
        self.compressor = PromptCompressor(
            model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
            use_llm_tokenizer=True
        )

    async def compress(self, text: str, ratio: float = 0.5) -> str:
        """Comprime mantenendo entità chiave."""
        compressed = await self.compressor.compress_prompt(
            text, ratio=ratio, target_token=2000
        )
        return compressed["compressed_prompt"]
```

**b) Semantic Cache** (`src/semantic_cache.py`):
```python
# Cache basata su embedding similarity
# MiniMax embedding API per vettorizzazione
# threshold: 0.95 similarity = cache hit
```

**c) Prompt Caching Enhancement**:
```python
# Sfrutta Anthropic Prompt Caching per contesti ripetuti
# Cache identifier basato su system_hash + conversation_type
```

### 5. Zero-Allucinazione: Verification Chain

Pipeline verificata (3-hop):

```
[Request] → Orchestrator (classifica complexity)
     ↓
[Complex] → THINKER produce piano strutturato
     ↓
[EXECUTOR] → ACT con piano strutturato
     ↓
[VERIFIER] → Cross-model verification (CoVe)
     ↓ Se fails > 2:
[REVISION] → Executor revisiona output
     ↓
[Final Output] con confidence_score
```

**Regole:**
- THINKER ≠ VERIFIER (evita bias)
- VERIFIER più piccolo/specifico (Haiku per facts semplici)
- Confidence < 0.7 → escalation ad Anthropic

---

## File da Modificare/Creare

| File | Azione | Descrizione |
|---|---|---|
| `src/imcp.py` | **CREARE** | Inter-Model Communication Protocol — schema + validation |
| `src/verify_layer.py` | **CREARE** | Bilateral verification con CoVe |
| `src/compression/llmlingua_compress.py` | **CREARE** | LLMLingua compression wrapper |
| `src/semantic_cache.py` | **CREARE** | Embedding-based semantic cache |
| `src/providers/base.py` | **MODIFICARE** | Espandi adapter pattern |
| `src/ai-router-proxy.py` | **MODIFICARE** | Integra verify_layer nel pipeline mix-am |
| `src/pipelines/primitives.py` | **MODIFICARE** | Usa IMCP schema per body building |
| `src/glm_backend.py` | **MODIFICARE** | Estendi adapter GLM |

---

## Dipendenze Nuove

```bash
pip install llmlingua        # Compressione prompt Microsoft
pip install sentence-transformers  # Semantic cache embeddings
# opzionale: pip install torch per GPU acceleration
```

---

## Verification

1. **Unit test compression**:
   ```bash
   pytest sviluppo/tests/test_compression.py -v
   ```

2. **Pipeline end-to-end**:
   ```bash
   ai-mode mix-am
   # Test con prompt complesso (500+ token output)
   # Verifica che verify_layer venga chiamato
   ```

3. **Token savings benchmark**:
   - Prima: registra token_usage per 10 richieste
   - Dopo: confronta con LLMLingua attivo
   - Target: 30-50% riduzione

4. **Verification accuracy**:
   - Test con prompt che genera facts verificabili
   - Confronta output con/senza bilateral verification

---

## Todo Board

- [ ] Creare `src/imcp.py` con schema e validation
- [ ] Creare `src/verify_layer.py` con CoVe implementation
- [ ] Creare `src/compression/llmlingua_compress.py`
- [ ] Creare `src/semantic_cache.py`
- [ ] Integrare IMCP nel pipeline mix-am esistente
- [ ] Test end-to-end
- [ ] Benchmark token savings

---

## Failed Approaches

- **Non usare**: format-free text passing tra modelli (causa incongruenze)
- **Non usare**: single-model verification (bias confirmation)
- **Non usare**: compressione pre-pipeline (perde contesto critico)

---

## Do NOT

- Non rimuovere pipeline esistenti (mix-am funziona)
- Non implementare A2A (per agenti esterni, non per routing interno)
- Non aggiungere overhead > 20ms nel hot path
