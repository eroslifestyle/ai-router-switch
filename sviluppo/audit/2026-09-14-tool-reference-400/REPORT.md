# Il 400 `Tool reference ... not found in available tools` — tre round, e la forma che nessuno aveva chiesto all'API

> Scritto per chi mette le mani su `src/tool_isolation.py` o indaga un 400 dal router.
> Chiusura: commit `c3d16a8`, live sul router dal 2026-09-14 03:54:23.

## Il sintomo

```
API Error: 400 Tool reference 'mcp__zai__web_search_prime' not found in available tools
```

Ricorrente dal 2026-09-02, sempre lo stesso schema con nomi di tool diversi: `mcp__MiniMax__understand_image` (02/09), `mcp__MiniMax__web_search` (05/09), `mcp__zai__web_search_prime` (12/09, 13/09, 14/09).

## Perche' succede, in una riga

`filter_tools_for_backend` toglie da `tools[]` i tool brandizzati di un provider diverso dal backend a cui sta inoltrando. Se nel corpo della richiesta resta anche **un solo riferimento** a un tool che non e' piu' dichiarato in `tools[]`, l'API risponde 400. L'isolamento crea l'incoerenza; il compito di `demote_foreign_tool_use` e' ripulire ogni riferimento residuo.

**Invariante da tenere a mente**: *qualunque nome sparisca da `tools[]`, nessun riferimento a quel nome puo' sopravvivere altrove nel body.* Ogni round di questo bug e' stato un posto nuovo in cui il riferimento si nascondeva.

## I tre round

| commit | data | cosa copriva | perche' non bastava |
|---|---|---|---|
| `7ca825b` | 12/09 | i predicati di brand leggevano solo `t["name"]`; aggiunto `tool_name`, e `demote_foreign_tool_use` estesa a `server_tool_use`/`mcp_tool_use` | copriva `tool_reference` solo dentro `tools[]`, non nella history |
| `356e9da` | 13/09 | guardia allargata a `b"tool_reference"` (prima un `tool_reference` non conteneva `b"tool_use"`, quindi la demote **non partiva mai**), tipo `tool_reference` aggiunto ai declassati | scandiva solo i blocchi di **primo livello** di `messages[].content` |
| `c3d16a8` | 14/09 | scansione **ricorsiva**, che scende anche in `content["tool_references"]` | — |

Dopo `356e9da`, con router riavviato il 13/09 alle 21:01:12, il 400 e' tornato 4 volte fra le 03:05 e le 03:08 del 14/09, tutte su `forward_anthropic` in modalita' `mix-ag-2`.

## La forma reale del blocco tool-search

Non e' stata dedotta: e' stata **chiesta all'API**, con un `POST :8771/v1/messages`, header `anthropic-beta: advanced-tool-use-2025-11-20`, e tool marcati `defer_loading: true`. La risposta:

```json
{
  "type": "tool_search_tool_result",
  "tool_use_id": "srvtoolu_01WpJYjFZWfHXARL4ndTGT8b",
  "content": {
    "type": "tool_search_tool_search_result",
    "tool_references": [
      {"type": "tool_reference", "tool_name": "leggi_file"},
      {"type": "tool_reference", "tool_name": "scrivi_file"}
    ]
  }
}
```

Tre dettagli che contano, tutti e tre disattesi dai tentativi precedenti:

1. `content` e' un **dict**, non una lista di blocchi. Una ricorsione che scende solo nelle liste non ci arriva.
2. I riferimenti stanno sotto la chiave `tool_references`, due livelli sotto `messages[].content`.
3. Il nome del tool e' in `tool_name`, non in `name`.

## Il fix

`demote_foreign_tool_use` in `src/tool_isolation.py` ora scandisce ricorsivamente, scendendo sia nei `content` che sono liste sia in `content["tool_references"]`.

Nei contenuti **annidati** il riferimento estraneo si **rimuove**, invece di declassarlo a un blocco `text` come si fa al primo livello: li' lo schema non ammette blocchi di testo. Se la lista resta vuota, la si lascia vuota — `tool_references: []` e' uno stato lecito, verificato con un round-trip contro l'API reale (HTTP 200), non assunto.

Il choke-point resta unico. I sei punti che lo chiamano, verificati con `git grep`:

- `src/forward_anthropic.py:262` e `:490` — `"anthropic"`
- `src/forward_minimax.py:110` — `"minimax"`
- `src/glm_backend.py:588` — `"glm"`
- `src/local_backend.py:395` — `"local"`
- `src/qwen_backend.py:430` — `"qwen"`

Il fix e' quindi backend-agnostico: vale anche fuori da `mix-ag-2`, dove il bug si e' manifestato.

## Le prove

Stesso body, forma vera, contro l'API reale attraverso la porta `8771`.

| body | esito | quando |
|---|---|---|
| non filtrato | `HTTP 400 :: Tool reference 'mcp__zai__web_search_prime' not found in available tools` | prima del restart, router col codice pre-`c3d16a8` |
| dopo il filtro nuovo | `HTTP 200` | prima del restart |
| sul router live `:8787` (mix-ag-2) | `HTTP 200` | dopo il restart delle 03:54:23 |

Lo script `riproduci.py` in questa cartella **non puo' piu' mostrare quel 400**: col fix in esecuzione il router ripulisce anche il body che gli arriva sporco, ed e' esattamente il comportamento voluto. Al suo posto lo script manda un **controllo negativo** — un riferimento a un tool inesistente e non brandizzato, che nessun filtro tocca — e ottiene il 400: la prova che l'API rifiuta davvero i riferimenti orfani, e che quindi il 200 sul riferimento brandizzato significa qualcosa. Per rivedere il 400 originale servirebbe una versione pre-`c3d16a8` in esecuzione.

Test: `test_tool_reference_annidato_in_search_result_rimosso` in `sviluppo/tests/test_tool_isolation_tool_reference.py`, che fallisce se si toglie la ricorsione nel dict. Suite completa **875 passed**.

## Cosa NON riprovare

- **Non scrivere il test su una forma inventata.** Il primo tentativo del 14/09 ricorreva solo nelle liste, perche' il test era costruito su un `tool_search_tool_result` con `content` lista. L'API rispondeva `RequestToolSearchToolResultError: Input does not match the expected shape` a **tutti** i casi di prova, quindi il test verde non dimostrava nulla e quello rosso non discriminava. Chiedere prima la forma all'API costa una chiamata da 300 token.
- **Non cercare la causa nel server MCP.** Il 12/09 z.ai rispondeva `initialize` in 0,53 s con HTTP 200: non c'entrava.
- **Non dedurre il tipo di blocco dal nome del campo.** `content` e' una lista nei messaggi, un dict qui, una stringa nei `tool_result` semplici.
- **Non fidarsi della guardia sul body grezzo.** Il round 2 e' esistito solo perche' `if b"tool_use" in body` non matcha `tool_reference`. Ogni tipo nuovo va aggiunto sia alla guardia sia alla tupla dei tipi.

## Rischio residuo

Se svuoto completamente `tool_references`, resta una lista vuota. Verificata 200 con round-trip reale, quindi non e' un'assunzione — ma e' il primo punto da guardare se il 400 ricompare in una forma diversa. In quel caso: declassare a testo anche il blocco contenitore e il suo `server_tool_use` abbinato (il commento `ponytail:` nel codice indica questa via d'uscita).

## Vedi anche

- Vault: `Memoria/progetti/ai-router-switch/sessione-400-tool-reference-tool-name-20260912.md` — cronologia completa dei tre round, incluso il gate `from_main` dell'hook globale di isolamento.
- `BUG-CATALOG.md`, firme `relay_error_400` in modalita' `anthropic`.
