"""Modulo Python per il router ai-router-switch: alleggerisce le richieste verso Qwen
rimuovendo le definizioni dei connettori claude.ai.

I tre connettori claude.ai (Gmail, Google Calendar, Google Drive) pesano 64KB dei 137KB 
di definizioni tool inviate a ogni richiesta e da soli saturano il budget TPM di Qwen 
(1M/min per 0.8 di safety), causando il rifiuto del 25,6% delle richieste da parte 
del rate limiter interno. Rimuoverli dalle sole richieste qwen quasi dimezza il costo 
per richiesta. Misurato il 2026-08-04.
"""

import json
import os
from debug_catalog import record_event
from tool_isolation import sanitize_defer_loading, sanitize_tool_choice


_HEAVY_CONNECTOR_PREFIX = 'mcp__claude_ai_'
STRIP_HEAVY_CONNECTORS = os.environ.get('AIROUTER_QWEN_STRIP_HEAVY_CONNECTORS', '1') not in ('0', 'false', 'no')


def strip_heavy_connectors(body: bytes) -> bytes:
    """Rimuove i connettori Claude.ai dalle richieste per Qwen per ridurre il payload."""
    if not STRIP_HEAVY_CONNECTORS:
        return body
    
    try:
        data = json.loads(body)
    except Exception:
        return body
    
    tools = data.get('tools')
    if not isinstance(tools, list) or len(tools) == 0:
        return body
    
    kept = []
    for t in tools:
        if not isinstance(t, dict):
            kept.append(t)
            continue
        
        name = t.get('name')
        if isinstance(name, str) and name.startswith(_HEAVY_CONNECTOR_PREFIX):
            continue
        
        kept.append(t)
    
    if len(kept) == len(tools):
        return body
    
    record_event(
        # "info" e non "block": lo strip dei connettori pesanti è il comportamento
        # previsto, non un'anomalia. Stessa correzione fatta a tool_isolation_strip.
        severity='info',
        category='qwen',
        kind='heavy_connector_strip',
        snippet=f'stripped={len(tools)-len(kept)} kept={len(kept)}/{len(tools)}'
    )
    
    if kept:
        # Cerca il cache_control dell'ultimo tool rimosso
        cc = None
        for removed_tool in reversed(tools):
            if removed_tool in kept:
                continue
            if isinstance(removed_tool, dict) and 'cache_control' in removed_tool:
                cc = removed_tool['cache_control']
                break
        
        # Se nessun elemento di kept ha già un cache_control, assegna a ultimo
        if cc is not None and not any(isinstance(t, dict) and t.get('cache_control') for t in kept):
            last_kept = kept[-1]
            if isinstance(last_kept, dict):
                kept[-1] = {**last_kept, 'cache_control': cc}
        
        data['tools'] = kept
    else:
        data.pop('tools', None)
        data.pop('tool_choice', None)
    
    sanitize_tool_choice(data)
    # Il filtro per backend gira PRIMA di questo strip (qwen_backend.py) e puo'
    # aver gia' portato via il tool di ricerca: i marcatori vanno ripuliti anche
    # qui, altrimenti restano orfani sui connettori superstiti.
    sanitize_defer_loading(data)
    return json.dumps(data).encode()
