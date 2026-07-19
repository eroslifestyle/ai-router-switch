# Piano: Comunicazione Bilaterale Multi-Modello con Best Practice 2026

## Contesto

Il router proxy `ai-router-switch` è un sistema **già molto avanzato** con 7 modalità operative, pipeline THINK-ACT-VERIFY collaborative, e 10+ livelli di ottimizzazione token. Tuttavia, manca di una strategia unificata per comunicazione bilaterale inter-modelli che segua gli standard emergenti 2026 (MCP/A2A).

## Architettura Attuale (ANALISI COMPLETA)

### Sistema Proxy Esistente
- **File principale**: `src/ai-router-proxy.py` (4,294 righe)
- **7 modalità**: anthropic, minimax, mix-am, mix-ag, mix-gm, glm, inverse
- **Pipeline collaborative**: THINK (Anthropic/GLM-5.2) → ACT (MiniMax/GLM) → VERIFY (Anthropic/GLM-5.2)
- **Moduli supporto**: glm_backend.py (846 righe), providers/base.py, pipelines/primitives.py, fail_tracker.py, streaming_relay.py

### Pattern Comunicazione Attuali
- **Think-Act-Verify Pipeline**: Messaggi fluiscono attraverso 3 stadi con trasformazioni modello-specifiche
- **Body Remapping**: `remap_body_for_minimax()` gestisce traduzione Claude → MiniMax
- **Serializzazione**: JSON-based con streaming SSE support
- **Error Handling**: Multi-layer classification + fallback chain (executor → Haiku → Anthropic → 502)
- **Retry Logic**: Exponential backoff (5/10/20/40/60s) con per-model sliding windows

### Sistema Ottimizzazione Token (GIÀ AVANZATO)
1. **Token Counter**: Cache 30s + stima `byte//4`
2. **Context Rewrite**: Tool pruning + head+tail preservation (6+6 messaggi)
3. **Context Shrink**: Compressione adattiva con HHEM learn loop
4. **Context Manager**: Soglie 80%/90%/100% + tracking SQLite
5. **Summarizer**: LLM-based compression con cache 24h
6. **Model Context Map**: Window limits + 20% buffer per output
7. **Caching Multi-livello**: Token (30s), API keys (60s), chat config (5s), summaries (24h)

**GIÀ IMPLEMENTATO**: Sistema molto sofisticato con 10+ layer di ottimizzazione

## Gap Identificati vs Best Practice 2026

### 1. Assenza Protocolli Standard 2026
**Problema**: Non implementazione MCP (Model Context Protocol) e A2A (Agent-to-Agent)

**Riferimenti 2026**:
- MCP è diventato lo standard Anthropic per connessioni AI-tools (GitHub, Microsoft, HuggingFace)
- A2A protocol HuggingFace per comunicazione inter-agenti
- Architetture come Plano, Proxima usano MCP come base

### 2. Validazione Anti-Allucinazione Cross-Model
**Problema**: Mancanza validation layer esplicito per output cross-model

**Best Practice 2026**:
- Framework C3PO per multimodal reasoning (Chain-of-Thought + verification)
- HHEM (zero-token hallucination detection) per validazione fattuale
- Evidence-Gate Pattern: verifica output sub-model prima dell'uso
- Benchmark 2026: tassi allucinazione 4-34% dipendono dal modello

### 3. Compatibility Layer Type-Safe
**Problema**: Body remapping ad-hoc (`remap_body_for_minimax()`) non generalizzato

**Best Practice 2026**:
- Protocolli bidirezionali type-safe con schema validation
- Cross-Lingual Token Arbitrage per ottimizzazione context dinamica
- Standardizzazione transformation rules per-model

### 4. Compressione Avanzata 2026
**Problema**: Sistema attuale usa summarization LLM, mancano tecniche 2026

**Best Practice 2026**:
- Prompt caching system prompts e documenti stabili (Anthropic, Redis)
- KV-cache compression per attention optimization
- Token compression con LLM low-cost (risparmio 70-80%)
- Delta-token correction post-response

## Piano Implementativo

### FASE 1: Foundation - MCP/A2A Protocol Integration

#### 1.1 Implementare MCP Server/Client Layer
**File**: `src/mcp_layer.py` (nuovo)

**Responsabilità**:
- Implementare Model Context Protocol per comunicazione AI-tools
- Supportare resource discovery, tool invocation, prompts standard
- MCP client per chiamate esterne, MCP server per integrazioni

**Interfaccia**:
```python
class MCPLayer:
    async def invoke_tool(self, tool_name: str, params: dict) -> MCPResponse
    async def list_resources(self) -> List[MCPResource]
    async def get_prompt(self, prompt_name: str) -> MCPPrompt
```

**Integrazione**: Modificare `handle()` per supportare MCP headers e routing

#### 1.2 Agent-to-Agent (A2A) Protocol Support
**File**: `src/a2a_protocol.py` (nuovo)

**Responsabilità**:
- Implementare A2A protocol HuggingFace per comunicazione inter-agenti
- Handshake bilaterale con capability negotiation
- Message format standardizzato con type checking

**Interfaccia**:
```python
class A2AMessage:
    sender: str
    receiver: str
    type: Literal["request", "response", "notification"]
    payload: dict
    schema_version: str = "2026.1"

class A2AProtocol:
    async def send_message(self, msg: A2AMessage) -> Ack
    async def receive_message(self) -> A2AMessage
    async def negotiate_capabilities(self) -> CapabilitySet
```

### FASE 2: Zero-Allucinazione Validation Layer

#### 2.1 HHEM Integration per Output Validation
**File**: `src/hhem_validator.py` (nuovo)

**Responsabilità**:
- Integrazione giudice locale HHEM (endpoint :4002)
- Validazione fattuale output cross-model zero-token
- Score threshold < 0.5 → reject/retry

**Interfaccia**:
```python
class HHEMValidator:
    async def validate_claim(self, source: str, claim: str) -> float  # score 0-1
    async def validate_response(self, response: dict, context: dict) -> ValidationReport

class ValidationReport:
    is_safe: bool  # score >= 0.5
    score: float
    unsafe_claims: List[str]
    corrected_response: Optional[dict]
```

**Integrazione**: Inserire validazione dopo ogni ACT/VERIFY pipeline

#### 2.2 Evidence-Gate Pattern per Sub-Model Output
**File**: `src/evidence_gate.py` (nuovo)

**Responsabilità**:
- Verifica output sub-model prima dell'uso nel main
- Evidence collection con citation tracking
- Self-consistency sampling per output critici

**Integrazione**: Modificare pipeline THINK-ACT-VERIFY per gate verification

### FASE 3: Type-Safe Compatibility Layer

#### 3.1 Universal Schema Transformation
**File**: `src/schema_transformer.py` (nuovo)

**Responsabilità**:
- Generalizzare `remap_body_for_minimax()` per tutti i modelli
- Schema validation con JSON Schema per ogni transformation
- Bidirectional transformation rules

**Interfaccia**:
```python
class SchemaTransformer:
    def transform(self, body: dict, source_model: str, target_model: str) -> dict
    def validate_schema(self, body: dict, model: str) -> bool
    def get_transformation_rules(self, source: str, target: str) -> TransformationRule

class TransformationRule:
    field_mappings: Dict[str, str]
    strip_fields: Set[str]
    rename_fields: Dict[str, str]
    type_conversions: Dict[str, type]
```

**Migration**: Sostituire transformation logic ad-hoc con schema transformer

### FASE 4: Advanced Token Optimization 2026

#### 4.1 Prompt Caching Layer
**File**: `src/prompt_cache.py` (nuovo)

**Responsabilità**:
- Caching system prompts e documenti stabili (Anthropic-style)
- Cache invalidation intelligente per prompt changes
- Integration con Anthropic API `cache_control` headers

**Interfaccia**:
```python
class PromptCache:
    async def get_cached_prompt(self, prompt_hash: str) -> Optional[CachedPrompt]
    async def cache_prompt(self, prompt: str, ttl: int) -> str
    async def invalidate(self, prompt_hash: str) -> None

class CachedPrompt:
    cached_id: str
    tokens_saved: int
    created_at: float
    ttl: int
```

#### 4.2 KV-Cache Compression
**File**: `src/kv_cache_optimizer.py` (nuovo)

**Responsabilità**:
- Ottimizzazione cache attention per transformer models
- Compressione KV-cache con quantization
- Smart eviction per frequent patterns

**Interfaccia**:
```python
class KVCacheOptimizer:
    async def compress_kv_cache(self, cache: dict) -> dict
    async def optimize_eviction(self, usage_stats: dict) -> EvictionStrategy
```

#### 4.3 Delta-Token Correction
**File**: `src/token_correction.py` (nuovo)

**Responsabilità**:
- Correzione stima token post-response basata su usage headers
- Aggiornamento dinamico cache token counter
- Delta-correction per accuratezza rate limiting

**Integrazione**: Potenziare `token_counter.py` con delta-correction

### FASE 5: Integration & Testing

#### 5.1 Unified Communication Pipeline
**File**: `src/unified_pipeline.py` (nuovo)

**Responsabilità**:
- Orchestrazione di MCP + A2A + validation + optimization
- Pipeline configurabile per mode-specific requirements
- Fallback integration con legacy system

#### 5.2 Testing & Validation
**Test Suite**: `sviluppo/tests/test_communication_2026.py`

**Coverage**:
- MCP/A2A protocol compliance
- Anti-allucinazione validation
- Schema transformation accuracy
- Token optimization effectiveness
- End-to-end communication flows

## File Modificati/Creati

### Nuovi File (Priorità)
1. `src/mcp_layer.py` - MCP implementation
2. `src/a2a_protocol.py` - A2A protocol
3. `src/hhem_validator.py` - HHEM integration
4. `src/evidence_gate.py` - Evidence-gate pattern
5. `src/schema_transformer.py` - Universal transformation
6. `src/prompt_cache.py` - Prompt caching
7. `src/kv_cache_optimizer.py` - KV-cache optimization
8. `src/token_correction.py` - Delta-token correction
9. `src/unified_pipeline.py` - Unified orchestration
10. `sviluppo/tests/test_communication_2026.py` - Test suite

### File Modificati (Integration)
1. `src/ai-router-proxy.py` - Integration MCP/A2A in `handle()`
2. `src/pipelines/primitives.py` - Schema transformation integration
3. `src/token_counter.py` - Delta-correction enhancement
4. `src/context_shrink.py` - HHEM integration

## Verification Plan

### 1. Unit Testing
- MCP protocol compliance (resource discovery, tool invocation)
- A2A message format validation
- HHEM threshold testing (< 0.5 reject)
- Schema transformation accuracy per modello

### 2. Integration Testing
- End-to-end communication flow: Client → MCP → A2A → Model → Validation → Response
- Fallback behavior: MCP failure → legacy, HHEM reject → retry
- Token optimization effectiveness: prompt caching hit-rate, KV-cache savings

### 3. Performance Testing
- Latency overhead MCP/A2A layers (< 50ms target)
- Token savings: prompt caching (> 60% cache hit-rate target)
- HHEM validation throughput (> 100 req/s)

### 4. Regression Testing
- Legacy mode compatibility (anthropic, minimax, glm puri)
- Existing pipeline behavior (THINK-ACT-VERIFY)
- Context management unchanged

## Timeline Stimata

- **Fase 1** (MCP/A2A): 2-3 giorni
- **Fase 2** (Anti-Allucinazione): 2-3 giorni
- **Fase 3** (Compatibility): 1-2 giorni
- **Fase 4** (Optimization): 2-3 giorni
- **Fase 5** (Testing): 2-3 giorni

**Totale**: 9-14 giorni per implementazione completa

## Rischi & Mitigazioni

### Rischio 1: MCP/A2A Complexity Overhead
**Mitigazione**: Implementazione incrementale, fallback a legacy system

### Rischio 2: HHEM False Positives
**Mitigazione**: Configurable threshold, logging dettagliato per tuning

### Rischio 3: Performance Degradation
**Mitigazione**: Profiling continuo, caching aggressivo, async optimization

### Rischio 4: Compatibility Issues
**Mitigazione**: Comprehensive testing suite, gradual rollout per mode

## Success Metrics

1. **Zero Allucinazioni**: HHEM score > 0.95 su 95%+ output cross-model
2. **Zero Incongruenze**: Schema validation 100% pass rate
3. **Zero Incompatibilità**: MCP/A2A compliance 100%
4. **Token Savings**: 60-80% riduzione vs baseline (prompt caching + compression)
5. **Performance**: < 50ms overhead MCP/A2A layers, < 100ms HHEM validation

## Fonti & Riferimenti 2026

### Protocolli Standard
- Model Context Protocol (MCP) - Anthropic, GitHub, Microsoft, HuggingFace
- Agent-to-Agent (A2A) Protocol - HuggingFace Spaces
- Cross-Lingual Token Arbitrage - ArXiv 2026

### Anti-Allucinazione
- Framework C3PO - Chain-of-Thought verification per multimodal reasoning
- HHEM (Zero-Token Hallucination Detection) - Validazione fattuale locale
- Stanford AI Index 2026 - Hallucination rate benchmarks (4-34% per modello)

### Token Optimization
- Prompt Caching Best Practices 2026 - Anthropic, Redis
- KV-Cache Compression - Attention optimization techniques
- Token Compression Guide - Obvious Works, 70-80% savings

### Architetture Riferimento
- Plano (katanemo) - AI-native proxy con orchestrazione
- Proxima (Zen4-bit) - Multi-AI MCP server routing
- llm-router-proxy (b24039971) - Multi-Agent gateway

---

**Creato il**: 2026-07-19
**Sessione**: Plan mode - ai-router-switch communication protocols
**Stato**: Piano completo pronto per implementazione