# BUG-CATALOG.md

> Generato automaticamente da `scripts/generate_bug_report.py` a partire da `logs/BUG-CATALOG.jsonl`. Non modificare a mano — rilanciare lo script. Vedi `DEBUG-CATALOG-SPEC.md` per lo schema completo.

**228 tipi distinti di bug/blocco/errore** · **35367 occorrenze totali** su 9 modalita'.

## Modalita': `anthropic`

76 tipi distinti, 27196 occorrenze.

### `rate_limit_429_exhausted` (429)

- **Firma**: `9c7020f92cb35d8a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 156
- **Prima volta**: 2026-07-22T11:14:43Z
- **Ultima volta**: 2026-08-04T00:53:05+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `retry-after=?`

### `relay_error_400` (400)

- **Firma**: `752f347e44651cc7`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 27
- **Prima volta**: 2026-07-21T20:44:09Z
- **Ultima volta**: 2026-07-26T08:35:55Z
- **Modalita' coinvolte**: anthropic

### `rate_limit_429_exhausted` (429)

- **Firma**: `a0c07b40348d36be`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 9
- **Prima volta**: 2026-07-23T15:33:35Z
- **Ultima volta**: 2026-08-04T00:42:35+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `retry-after=1`

### `forward_exception`

- **Firma**: `83bbc9e3ba47b798`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-08-07T14:40:53+0200
- **Ultima volta**: 2026-08-07T14:41:58+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `'list' object has no attribute 'get'`

### `relay_error_400` (400)

- **Firma**: `616b77eeee413a4c`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-27T23:02:44+0200
- **Ultima volta**: 2026-07-27T23:02:48+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long: 208904 tokens > 200000 maximum"},"request_id":"req_011CdTEnv8dPM4AZnueLkxmb"}`

### `relay_error_400` (400)

- **Firma**: `81601913633cbd3b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-07T14:41:00+0200
- **Ultima volta**: 2026-08-07T14:41:00+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"campo_inventato_xyz: Extra inputs are not permitted"},"request_id":"req_011CdoQRbcJw7nT1fCnPdVYR"}`

### `relay_error_400` (400)

- **Firma**: `4ea73c5e09bf91e0`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-07T14:40:56+0200
- **Ultima volta**: 2026-08-07T14:40:56+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"model: Field required"},"request_id":"req_011CdoQRFiEaEA6U4TaPKhr4"}`

### `relay_error_400` (400)

- **Firma**: `89943f7a4c78960b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-07T14:40:56+0200
- **Ultima volta**: 2026-08-07T14:40:56+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"messages: Field required"},"request_id":"req_011CdoQRHQBcdL3gr8MpFzDZ"}`

### `relay_error_400` (400)

- **Firma**: `62b01920e930d237`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-07T14:40:52+0200
- **Ultima volta**: 2026-08-07T14:40:52+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"The request body is not valid JSON: unexpected end of data: line 1 column 41 (char 40)"},"request_id":"req_011CdoQQyDoAjeqQ2Atj7oKm"}`

### `relay_error_400` (400)

- **Firma**: `fe86f22f95549cf3`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-07T14:40:52+0200
- **Ultima volta**: 2026-08-07T14:40:52+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"The request body is not valid JSON: Input is a zero-length, empty document: line 1 column 1 (char 0)"},"request_id":"req_011CdoQQznJpmx1vZnQ4CCVf"}`

### `sse_truncated` (200)

- **Firma**: `b8cdeaf54a136e8e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:13:22+0200
- **Ultima volta**: 2026-08-01T11:13:22+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"model":"claude-haiku-4-5-20251001","id":"msg_011CdbmjdJQgRZvw4cLxp9hU","type":"message","role":"assistant","content":[],"stop_reason":null,"stop_sequence":null,"stop_details":null,"usage":{"input_tokens":12,"cache_creation_input_tokens":`

### `sse_truncated` (200)

- **Firma**: `dbd0724a1f3d6485`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:06:35+0200
- **Ultima volta**: 2026-08-01T11:06:35+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `relay_error_404` (404)

- **Firma**: `43850a86517bcb18`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:04:52+0200
- **Ultima volta**: 2026-08-01T11:04:52+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: MiniMax-M3"},"request_id":"req_011Cdbm69yKgGdis1D4j9wAZ"}`

### `relay_error_404` (404)

- **Firma**: `7a84c720ffe8b4dc`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:04:52+0200
- **Ultima volta**: 2026-08-01T11:04:52+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: glm-4.7"},"request_id":"req_011Cdbm6BoD7kF4tX5qLP8Lm"}`

### `relay_error_404` (404)

- **Firma**: `ee6c45c4e5ba1933`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T10:49:16+0200
- **Ultima volta**: 2026-08-01T10:49:16+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: MiniMax-M3"},"request_id":"req_011CdbjuATRQAXABPYdxSMgQ"}`

### `relay_error_404` (404)

- **Firma**: `de45eba0b6702c3e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T10:49:16+0200
- **Ultima volta**: 2026-08-01T10:49:16+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: glm-4.7"},"request_id":"req_011CdbjuC7tMcxdFNZGqfdVj"}`

### `relay_error_401` (401)

- **Firma**: `876d341709cdab3e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T10:49:08+0200
- **Ultima volta**: 2026-08-01T10:49:08+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdbjtZLoNZ8BKhW1vFtGM"}`

### `relay_error_401` (401)

- **Firma**: `01705d85cee455cb`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T10:49:08+0200
- **Ultima volta**: 2026-08-01T10:49:08+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdbjtaG7vJVcJyo74C4t1"}`

### `relay_error_401` (401)

- **Firma**: `6d320f9a72da97d5`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T10:49:08+0200
- **Ultima volta**: 2026-08-01T10:49:08+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdbjtbG9NDVULGYESz2WP"}`

### `ctx_gate` (error)

- **Firma**: `f1fce1a81fe35cf4`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-27T23:20:46+0200
- **Ultima volta**: 2026-07-27T23:20:46+0200
- **Modalita' coinvolte**: anthropic

### `relay_error_400` (400)

- **Firma**: `9cbf1e5e7aec7060`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-26T08:39:54Z
- **Ultima volta**: 2026-07-26T08:39:54Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"messages.1.content.0.server_tool_use.id: String should match pattern '^srvtoolu_[a-zA-Z0-9_]+$'"},"request_id":"req_011CdQDBXB9w2RAAeRZbDqPh"}`

### `relay_error_404` (404)

- **Firma**: `59c9d40b47a29cfb`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-26T07:58:29Z
- **Ultima volta**: 2026-07-26T07:58:29Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: MiniMax-M3"},"request_id":"req_011CdQA2LFj4EyydWTganSr5"}`

### `relay_error_404` (404)

- **Firma**: `e9e5bbdd961fd5e5`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T02:35:28Z
- **Ultima volta**: 2026-07-23T02:35:28Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: MiniMax-M3"},"request_id":"req_011CdJ3xuwwAG2jH9Mmeiv5z"}`

### `relay_error_404` (404)

- **Firma**: `025554e80c54cfcd`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T01:06:57Z
- **Ultima volta**: 2026-07-23T01:06:57Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: MiniMax-M2.7"},"request_id":"req_011CdHwDPLHKfUnRZL2YGkKt"}`

### `relay_error_404` (404)

- **Firma**: `d8c147c94794abf1`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T01:06:51Z
- **Ultima volta**: 2026-07-23T01:06:51Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: MiniMax-M2.7"},"request_id":"req_011CdHwCxiCN44ZFHUjth81y"}`

### `relay_error_404` (404)

- **Firma**: `80ed01663cdec4e8`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T01:06:38Z
- **Ultima volta**: 2026-07-23T01:06:38Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: MiniMax-M2.7"},"request_id":"req_011CdHwBzqfWgDtWJELJAzHZ"}`

### `relay_error_404` (404)

- **Firma**: `e14766c7e0088548`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T13:09:30Z
- **Ultima volta**: 2026-07-22T13:09:30Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: sonnet"},"request_id":"req_011CdGzWAg6cUk6sUv7UBHA7"}`

### `relay_error_404` (404)

- **Firma**: `9443057db85379c3`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T13:09:20Z
- **Ultima volta**: 2026-07-22T13:09:20Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: sonnet"},"request_id":"req_011CdGzVURfc6o6ASjybdUCw"}`

### `relay_error_401` (401)

- **Firma**: `75a41db7ef20d783`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T08:54:04Z
- **Ultima volta**: 2026-07-22T08:54:04Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdGf2XBQ7dKTk1fNb9zZ8"}`

### `relay_error_401` (401)

- **Firma**: `2a7e8e6beaea69f6`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T07:39:47Z
- **Ultima volta**: 2026-07-22T07:39:47Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdGZMyfS12jBWmr4hqA3U"}`

### `relay_error_401` (401)

- **Firma**: `bd17e2a9ecd8e03e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T07:29:27Z
- **Ultima volta**: 2026-07-22T07:29:27Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdGYaFkXqQhpusxtrssDH"}`

### `relay_error_401` (401)

- **Firma**: `760343629b809543`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T07:20:06Z
- **Ultima volta**: 2026-07-22T07:20:06Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdGXrtHZCqP58himu1MCC"}`

### `relay_error_404` (404)

- **Firma**: `6e885a6a43b87c3a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T14:48:58Z
- **Ultima volta**: 2026-07-20T14:48:58Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: haiku-4.5"},"request_id":"req_011CdDLUHNBvZcHEQqPzLAhv"}`

### `relay_error_404` (404)

- **Firma**: `b5f8c8746e5666ad`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T14:48:58Z
- **Ultima volta**: 2026-07-20T14:48:58Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: haiku-4.5"},"request_id":"req_011CdDLUNK6gJN7yeEczbsRW"}`

### `relay_error_404` (404)

- **Firma**: `520455a342d195c7`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T14:48:55Z
- **Ultima volta**: 2026-07-20T14:48:55Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"not_found_error","message":"model: haiku-4.5"},"request_id":"req_011CdDLU9DZEDzhJdTiUwQFJ"}`

### `relay_error_529` (529)

- **Firma**: `94e350861f8a767a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:13:32Z
- **Ultima volta**: 2026-07-20T08:13:32Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpKK7NZQMAMhNvRVrjv"}`

### `relay_error_529` (529)

- **Firma**: `3f7387224b0955ea`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:13:22Z
- **Ultima volta**: 2026-07-20T08:13:22Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpJcrvwn6EC1KxouXTf"}`

### `relay_error_529` (529)

- **Firma**: `f29c3d7c3b14e322`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:13:16Z
- **Ultima volta**: 2026-07-20T08:13:16Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpJB2B75Sa1xWyjvJX3"}`

### `relay_error_529` (529)

- **Firma**: `5c39671dd7f0e66d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:13:13Z
- **Ultima volta**: 2026-07-20T08:13:13Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpHuvKh9R32TtJ4XDTz"}`

### `relay_error_529` (529)

- **Firma**: `9e79cf9eccac0907`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:13:10Z
- **Ultima volta**: 2026-07-20T08:13:10Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpHhEq5BNkzcvJnviHk"}`

### `relay_error_529` (529)

- **Firma**: `1e7d7380335ccd98`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:13:08Z
- **Ultima volta**: 2026-07-20T08:13:08Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpHZGMnfFEBkXqTJrD6"}`

### `relay_error_529` (529)

- **Firma**: `6596937075664c63`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:12:33Z
- **Ultima volta**: 2026-07-20T08:12:33Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpEwxXgkgpgNak7BLRp"}`

### `relay_error_529` (529)

- **Firma**: `83ca3e8a9240399f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:11:57Z
- **Ultima volta**: 2026-07-20T08:11:57Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCpCJ79cwLXHA2XCxjR6"}`

### `relay_error_529` (529)

- **Firma**: `55b8a7c373c72307`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:11:19Z
- **Ultima volta**: 2026-07-20T08:11:19Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCp9Z1WV4zVTNfKqvGYd"}`

### `relay_error_529` (529)

- **Firma**: `cdc8f01fa600dbf7`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:10:42Z
- **Ultima volta**: 2026-07-20T08:10:42Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCp6miAJ3a6rgQw1BEzt"}`

### `relay_error_529` (529)

- **Firma**: `676406991e05136f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:10:01Z
- **Ultima volta**: 2026-07-20T08:10:01Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCp3moHFt5rTUM3dxxT5"}`

### `relay_error_529` (529)

- **Firma**: `41179ff08d46220d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:09:42Z
- **Ultima volta**: 2026-07-20T08:09:42Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCp2PbGwd4HymqSpAog3"}`

### `relay_error_529` (529)

- **Firma**: `938027753efe11ad`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:09:32Z
- **Ultima volta**: 2026-07-20T08:09:32Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCp1fuXA19AcEVpjDpZH"}`

### `relay_error_529` (529)

- **Firma**: `d1b531ff523c88a9`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:09:26Z
- **Ultima volta**: 2026-07-20T08:09:26Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCp18fKDw9TqZvY6q4bD"}`

### `relay_error_529` (529)

- **Firma**: `dc9d0e0a7892bbce`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:09:22Z
- **Ultima volta**: 2026-07-20T08:09:22Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCozuGQsBKrvbLBcoQ8D"}`

### `relay_error_529` (529)

- **Firma**: `e4492ae6bf3dc433`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:09:20Z
- **Ultima volta**: 2026-07-20T08:09:20Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCozh1DUcdWz6BejtAZ6"}`

### `relay_error_529` (529)

- **Firma**: `4dd2837c19371fc8`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:09:17Z
- **Ultima volta**: 2026-07-20T08:09:17Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCozM6eemy9kzb8rKrKJ"}`

### `relay_error_529` (529)

- **Firma**: `61277624a696e380`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:05:03Z
- **Ultima volta**: 2026-07-20T08:05:03Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCofoStWTZ1mV4xub4zd"}`

### `relay_error_529` (529)

- **Firma**: `c7300ed019ebc481`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:04:57Z
- **Ultima volta**: 2026-07-20T08:04:57Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCofMJGfryGeKCWz2ZPX"}`

### `relay_error_529` (529)

- **Firma**: `14ba52b88678e9f7`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:04:53Z
- **Ultima volta**: 2026-07-20T08:04:53Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCof67xmYRc1dorEeo39"}`

### `relay_error_529` (529)

- **Firma**: `9a5401817bea2fd1`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:04:51Z
- **Ultima volta**: 2026-07-20T08:04:51Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCoetwGGht8VZFreEci1"}`

### `relay_error_529` (529)

- **Firma**: `7ad73b1dcad43549`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-20T08:04:49Z
- **Ultima volta**: 2026-07-20T08:04:49Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},"request_id":"req_011CdCoeiqYfYMPqhx3zYTf3"}`

### `relay_error_400` (400)

- **Firma**: `792107db8f2c1ad4`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T20:57:17Z
- **Ultima volta**: 2026-07-19T20:57:17Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `extra_headers=['x-ai-verified'] upstream_headers={'server': 'cloudflare', 'cf-ray': 'a1dbf36ff9e0eda2-MXP'} url=https://api.anthropic.com/v1/messages?beta=true`

### `relay_error_400` (400)

- **Firma**: `044dc1780c1dc29a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T20:56:47Z
- **Ultima volta**: 2026-07-19T20:56:47Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `extra_headers=['x-ai-verified'] upstream_headers={'server': 'cloudflare', 'cf-ray': 'a1dbf2b29c0ced15-MXP'} url=https://api.anthropic.com/v1/messages?beta=true`

### `relay_error_400` (400)

- **Firma**: `1ff20ce9bcd0300e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T20:55:55Z
- **Ultima volta**: 2026-07-19T20:55:55Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `extra_headers=['x-ai-verified'] upstream_headers={'server': 'cloudflare', 'cf-ray': 'a1dbf16dba6bed92-MXP'} url=https://api.anthropic.com/v1/messages?beta=true`

### `relay_error_400` (400)

- **Firma**: `4a8723905d65df25`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T20:55:55Z
- **Ultima volta**: 2026-07-19T20:55:55Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `extra_headers=['x-ai-verified'] upstream_headers={'server': 'cloudflare', 'cf-ray': 'a1dbf173cfafed92-MXP'} url=https://api.anthropic.com/v1/messages?beta=true`

### `rate_limit_429`

- **Firma**: `e648db5b3f6e697e`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 312
- **Prima volta**: 2026-07-22T13:08:58Z
- **Ultima volta**: 2026-08-04T02:40:09+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic 429: retry 2/2 retry-after=None sleep=0.72s`

### `burst_limiter_429` (429)

- **Firma**: `dcbba7265b9b5693`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 39
- **Prima volta**: 2026-07-19T20:19:06Z
- **Ultima volta**: 2026-07-22T11:04:05Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `retry-after=1.5s`

### `rate_limit_429`

- **Firma**: `500f69599f86d10e`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 18
- **Prima volta**: 2026-07-23T15:31:34Z
- **Ultima volta**: 2026-08-04T00:42:34+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic 429: retry 2/2 retry-after=1.0 sleep=1.00s`

### `rate_limit_429`

- **Firma**: `d2210ec5c7c88a45`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 4
- **Prima volta**: 2026-08-07T14:40:52+0200
- **Ultima volta**: 2026-08-07T14:41:57+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic EXC: retry 2/2 sleep=0.67s ('list' object has no attribute 'get')`

### `rate_limit_429`

- **Firma**: `b5af13ec30de3db5`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T12:31:17Z
- **Ultima volta**: 2026-07-29T18:14:03+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic EXC: retry 1/2 sleep=0.36s (Timeout on reading data from socket)`

### `rate_limit_429` (429)

- **Firma**: `435d593edd4ea264`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T11:14:41Z
- **Ultima volta**: 2026-07-22T11:14:42Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `attempt=2/2 retry-after=None sleep=0.60s`

### `rate_limit_429`

- **Firma**: `1b10f072fcd4c6c9`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-08-04T04:04:12+0200
- **Ultima volta**: 2026-08-04T04:04:12+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic EXC: retry 1/2 sleep=0.40s ([Errno 104] Connection reset by peer)`

### `tool_isolation_strip`

- **Firma**: `7446f99926bbc6ef`
- **Severita'**: info
- **Occorrenze**: 18895
- **Prima volta**: 2026-07-26T17:54:14Z
- **Ultima volta**: 2026-08-08T00:43:34+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__zai__web_search_prime'] kept=73/74`

### `tool_isolation_strip`

- **Firma**: `1b910b74f7b880e0`
- **Severita'**: info
- **Occorrenze**: 7285
- **Prima volta**: 2026-07-19T21:40:09Z
- **Ultima volta**: 2026-07-28T07:58:11+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search', 'mcp__zai__web_search_prime'] kept=60/63`

### `tool_isolation_strip`

- **Firma**: `5eb9aca25569b15f`
- **Severita'**: info
- **Occorrenze**: 355
- **Prima volta**: 2026-07-19T19:41:54Z
- **Ultima volta**: 2026-07-28T07:44:40+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search'] kept=60/62`

### `ctx_gate` (ok)

- **Firma**: `77864e7a51a32f02`
- **Severita'**: info
- **Occorrenze**: 19
- **Prima volta**: 2026-07-30T03:21:40+0200
- **Ultima volta**: 2026-08-01T14:18:10+0200
- **Modalita' coinvolte**: anthropic

### `tool_isolation_strip`

- **Firma**: `3f14d8ef8e1d0fab`
- **Severita'**: info
- **Occorrenze**: 8
- **Prima volta**: 2026-07-22T06:53:40Z
- **Ultima volta**: 2026-07-22T06:56:57Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__zai__web_search_prime', 'mcp__MiniMax__web_search'] kept=1/3`

### `ctx_gate`

- **Firma**: `164bcf4c47d65947`
- **Severita'**: info
- **Occorrenze**: 2
- **Prima volta**: 2026-07-26T18:23:30Z
- **Ultima volta**: 2026-07-26T18:25:46Z
- **Modalita' coinvolte**: anthropic

### `tool_isolation_strip`

- **Firma**: `379bc13a99c2fc04`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-08-03T19:52:34+0200
- **Ultima volta**: 2026-08-03T19:52:34+0200
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__websearch__search', 'mcp__MiniMax__web_search', 'mcp__zai__web_search_prime'] kept=2/5`

### `tool_isolation_strip`

- **Firma**: `9118774d9a6c02eb`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T21:35:33Z
- **Ultima volta**: 2026-07-19T21:35:33Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__zai__webSearchPrime', 'mcp__MiniMax__web_search'] kept=1/3`

## Modalita': `glm`

38 tipi distinti, 2165 occorrenze.

### `forward_exception`

- **Firma**: `03cbee9d411a9fcc`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 61
- **Prima volta**: 2026-07-30T08:45:05+0200
- **Ultima volta**: 2026-07-31T08:23:05+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `'Response' object has no attribute 'content'`

### `glm_client_error`

- **Firma**: `ad6e7113c00559b1`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 45
- **Prima volta**: 2026-07-19T20:25:21Z
- **Ultima volta**: 2026-07-28T07:13:46+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `[Errno 104] Connection reset by peer`

### `glm_timeout`

- **Firma**: `7659e6cc54c309c3`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 24
- **Prima volta**: 2026-07-22T06:58:45Z
- **Ultima volta**: 2026-07-22T12:27:31Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 model=claude-fable-5`

### `forward_exception`

- **Firma**: `1e06758b94aea517`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 23
- **Prima volta**: 2026-07-30T08:45:18+0200
- **Ultima volta**: 2026-07-31T08:22:28+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `'Response' object has no attribute 'release'`

### `glm_exhausted` (502)

- **Firma**: `23d215ec974ab90f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 23
- **Prima volta**: 2026-07-30T08:45:18+0200
- **Ultima volta**: 2026-07-31T08:22:27+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-opus-5`

### `glm_exhausted` (502)

- **Firma**: `e15f53f84c2a124d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 21
- **Prima volta**: 2026-07-22T06:30:25Z
- **Ultima volta**: 2026-07-22T08:22:26Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-fable-5`

### `glm_exhausted` (502)

- **Firma**: `14ae20e2de2fcb47`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 16
- **Prima volta**: 2026-07-22T08:40:54Z
- **Ultima volta**: 2026-07-22T13:32:11Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-opus-4-8`

### `glm_client_error`

- **Firma**: `c70a31881f6ba0c6`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 8
- **Prima volta**: 2026-07-19T20:22:07Z
- **Ultima volta**: 2026-07-31T07:27:45+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `Server disconnected`

### `glm_exhausted` (502)

- **Firma**: `35ea096780db1793`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 7
- **Prima volta**: 2026-07-22T08:45:29Z
- **Ultima volta**: 2026-07-25T17:23:59Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=glm-5.2`

### `forward_exception`

- **Firma**: `84d2e99403a775b7`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 6
- **Prima volta**: 2026-07-30T08:12:22+0200
- **Ultima volta**: 2026-08-06T17:26:37+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `Cannot write to closing transport`

### `glm_exhausted` (502)

- **Firma**: `ba67794095935766`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 4
- **Prima volta**: 2026-07-22T08:43:17Z
- **Ultima volta**: 2026-07-22T08:51:25Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-sonnet-5`

### `glm_timeout`

- **Firma**: `fbb49ec93aed9691`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 3
- **Prima volta**: 2026-07-22T08:26:50Z
- **Ultima volta**: 2026-07-22T08:37:14Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 model=claude-opus-4-8`

### `glm_exhausted` (502)

- **Firma**: `127473aa11da8b96`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T10:38:15Z
- **Ultima volta**: 2026-07-22T10:40:22Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-haiku-4-5-20251001`

### `glm_client_error`

- **Firma**: `edbd4780a15b4288`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-30T20:49:43+0200
- **Ultima volta**: 2026-07-30T20:49:43+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `[Errno 32] Broken pipe`

### `glm_5xx_retry` (500)

- **Firma**: `619d51bc1581791b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T11:45:12Z
- **Ultima volta**: 2026-07-22T11:45:12Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 model=claude-fable-5`

### `glm_timeout`

- **Firma**: `2ddeefa718bb585c`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T20:44:47Z
- **Ultima volta**: 2026-07-19T20:44:47Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 model=claude-sonnet-5`

### `glm_ratelimit_exhausted` (429)

- **Firma**: `6e43cd08801003c3`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 61
- **Prima volta**: 2026-07-30T08:45:05+0200
- **Ultima volta**: 2026-07-31T08:23:05+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-opus-5 glm rate-limit: budget 8s esaurito (waited 0s)`

### `glm_429_backoff` (429)

- **Firma**: `0445c79fb5ecb1f4`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 50
- **Prima volta**: 2026-07-30T08:44:58+0200
- **Ultima volta**: 2026-07-31T08:22:27+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=2 backoff=60s model=claude-opus-5`

### `glm_ratelimit_exhausted` (429)

- **Firma**: `5d2ba46f12bd738f`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 40
- **Prima volta**: 2026-07-22T10:37:17Z
- **Ultima volta**: 2026-08-04T03:16:32+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `model=glm-4.7 glm rate-limit: budget 8s esaurito (waited 6s)`

### `glm_ratelimit_exhausted` (429)

- **Firma**: `4ede846b53432b7d`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 34
- **Prima volta**: 2026-07-22T08:40:55Z
- **Ultima volta**: 2026-07-22T13:32:51Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-opus-4-8 glm rate-limit: budget 8s esaurito (waited 0s)`

### `glm_429_backoff` (429)

- **Firma**: `af19763f9b35d220`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 30
- **Prima volta**: 2026-07-22T08:40:45Z
- **Ultima volta**: 2026-07-22T13:33:15Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 backoff=60s model=claude-opus-4-8`

### `glm_429_backoff` (429)

- **Firma**: `f2484b535f2ec78c`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 26
- **Prima volta**: 2026-07-22T08:44:25Z
- **Ultima volta**: 2026-08-03T23:15:22+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 backoff=5s model=glm-4.7`

### `glm_429_rpm_tpm` (429)

- **Firma**: `905bdf64f46d8ba0`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 19
- **Prima volta**: 2026-08-03T23:50:35+0200
- **Ultima volta**: 2026-08-04T03:16:32+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 backoff=5s kind=rpm_tpm model=glm-4.7`

### `glm_429_backoff` (429)

- **Firma**: `d85ac5ad795c0e8f`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 13
- **Prima volta**: 2026-07-22T06:30:15Z
- **Ultima volta**: 2026-07-22T08:41:33Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 backoff=5s model=claude-fable-5`

### `glm_ratelimit_exhausted` (429)

- **Firma**: `09cf98b4b1531f25`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 11
- **Prima volta**: 2026-07-22T08:41:39Z
- **Ultima volta**: 2026-07-22T08:45:18Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-fable-5 glm rate-limit: budget 8s esaurito (waited 0s)`

### `glm_429_backoff` (429)

- **Firma**: `75d382f27e393a0c`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 7
- **Prima volta**: 2026-07-22T08:42:13Z
- **Ultima volta**: 2026-07-22T08:51:25Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=2 backoff=40s model=claude-sonnet-5`

### `glm_ratelimit_exhausted` (429)

- **Firma**: `2bc2ce001dc54653`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 6
- **Prima volta**: 2026-07-22T08:50:21Z
- **Ultima volta**: 2026-07-22T08:51:37Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-sonnet-5 glm rate-limit: budget 8s esaurito (waited 0s)`

### `glm_429_backoff` (429)

- **Firma**: `bea2afb7568d2fad`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T10:39:19Z
- **Ultima volta**: 2026-07-22T10:40:22Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=2 backoff=60s model=claude-haiku-4-5-20251001`

### `tool_isolation_strip`

- **Firma**: `2cf1321f17c5adda`
- **Severita'**: info
- **Occorrenze**: 1191
- **Prima volta**: 2026-07-22T08:07:41Z
- **Ultima volta**: 2026-08-06T17:26:05+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['WebFetch', 'WebSearch'] kept=54/56`

### `tool_isolation_strip`

- **Firma**: `e5dc8d4b2d987612`
- **Severita'**: info
- **Occorrenze**: 308
- **Prima volta**: 2026-07-22T07:29:23Z
- **Ultima volta**: 2026-07-22T13:39:35Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['WebFetch', 'WebSearch', 'mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search'] kept=39/43`

### `tool_isolation_strip`

- **Firma**: `f9d1fac61fb04f47`
- **Severita'**: info
- **Occorrenze**: 109
- **Prima volta**: 2026-07-19T20:18:51Z
- **Ultima volta**: 2026-07-22T06:38:36Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search'] kept=274/276`

### `tool_isolation_strip`

- **Firma**: `8dc3755f203663e5`
- **Severita'**: info
- **Occorrenze**: 5
- **Prima volta**: 2026-07-26T08:34:50Z
- **Ultima volta**: 2026-07-26T08:36:44Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['web_search'] kept=1/2`

### `tool_isolation_strip`

- **Firma**: `c04f3e9878f7fa6c`
- **Severita'**: info
- **Occorrenze**: 2
- **Prima volta**: 2026-07-19T21:35:33Z
- **Ultima volta**: 2026-07-22T06:53:48Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__web_search'] kept=2/3`

### `tool_isolation_strip`

- **Firma**: `0b58ea9d1dfa75c4`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-08-03T19:52:34+0200
- **Ultima volta**: 2026-08-03T19:52:34+0200
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__websearch__search', 'mcp__MiniMax__web_search', 'web_search_20250305'] kept=2/5`

### `tool_isolation_strip`

- **Firma**: `54ae025d11c9e975`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T01:11:32Z
- **Ultima volta**: 2026-07-23T01:11:32Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__zai__web_search_prime'] kept=1/2`

### `tool_isolation_strip`

- **Firma**: `e119ad9c18e6e970`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T07:21:28Z
- **Ultima volta**: 2026-07-22T07:21:28Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['WebSearch', 'WebFetch'] kept=1/3`

### `tool_isolation_strip`

- **Firma**: `3b89b0095896c827`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T06:57:04Z
- **Ultima volta**: 2026-07-22T06:57:04Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__web_search', 'WebSearch'] kept=1/3`

### `tool_isolation_strip`

- **Firma**: `86b15a0bd239ccc0`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-07-21T23:25:21Z
- **Ultima volta**: 2026-07-21T23:25:21Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__understand_image'] kept=0/1`

## Modalita': `local`

5 tipi distinti, 57 occorrenze.

### `relay_error_500` (500)

- **Firma**: `315e830d6f1146df`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 23
- **Prima volta**: 2026-08-04T22:52:58+0200
- **Ultima volta**: 2026-08-04T23:08:48+0200
- **Modalita' coinvolte**: local
- **Esempio**: `{"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - {\"error\":{\"code\":500,\"message\":\"image input is not supported - hint: if this is unexpected, you may need to provide the mmproj\",\"type\":\"server_error\"}}No fallback model group found for original model`

### `forward_exception`

- **Firma**: `e68e94708f150f4b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 7
- **Prima volta**: 2026-08-04T22:49:17+0200
- **Ultima volta**: 2026-08-06T20:32:12+0200
- **Modalita' coinvolte**: local
- **Esempio**: `Cannot write to closing transport`

### `ctx_gate` (error)

- **Firma**: `cd581c096b40b2af`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 3
- **Prima volta**: 2026-08-04T22:34:35+0200
- **Ultima volta**: 2026-08-04T22:35:01+0200
- **Modalita' coinvolte**: local

### `relay_error_400` (400)

- **Firma**: `863b15a6454e6a1c`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-08-04T22:34:38+0200
- **Ultima volta**: 2026-08-04T22:34:41+0200
- **Modalita' coinvolte**: local
- **Esempio**: `{"error":{"message":"litellm.ContextWindowExceededError: litellm.BadRequestError: ContextWindowExceededError: OpenAIException - {\"error\":{\"code\":400,\"message\":\"request (158424 tokens) exceeds the available context size (32768 tokens), try increasing it\",\"type\":\"exceed_context_size_error\"`

### `ctx_gate` (ok)

- **Firma**: `70a910c52d76abb7`
- **Severita'**: info
- **Occorrenze**: 22
- **Prima volta**: 2026-08-04T22:46:18+0200
- **Ultima volta**: 2026-08-06T20:35:18+0200
- **Modalita' coinvolte**: local

## Modalita': `minimax`

14 tipi distinti, 2910 occorrenze.

### `forward_exception`

- **Firma**: `46cf435b9a06795b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 323
- **Prima volta**: 2026-07-26T10:06:51Z
- **Ultima volta**: 2026-08-06T23:37:44+0200
- **Modalita' coinvolte**: minimax
- **Esempio**: `Timeout on reading data from socket`

### `think_failed` (404)

- **Firma**: `9f105e225ad3c1ab`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-19T21:14:58Z
- **Ultima volta**: 2026-07-19T21:16:59Z
- **Modalita' coinvolte**: minimax

### `relay_error_404` (404)

- **Firma**: `9e5bb27fd86c81bf`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-19T21:14:58Z
- **Ultima volta**: 2026-07-19T21:16:59Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `<html> <head><title>404 Not Found</title></head> <body> <center><h1>404 Not Found</h1></center> <hr><center>nginx</center> </body> </html>`

### `forward_exception`

- **Firma**: `aa1542b694e70fef`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-28T17:09:58+0200
- **Ultima volta**: 2026-07-28T17:09:58+0200
- **Modalita' coinvolte**: minimax
- **Esempio**: `Cannot connect to host 127.0.0.1:38617 ssl:default [Connect call failed ('127.0.0.1', 38617)]`

### `forward_exception`

- **Firma**: `75e2c80cbe7c83d0`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-26T19:29:54Z
- **Ultima volta**: 2026-07-26T19:29:54Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `'NoneType' object has no attribute 'request'`

### `think_plan_invalid`

- **Firma**: `d9594c530578e523`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 40
- **Prima volta**: 2026-07-19T21:20:35Z
- **Ultima volta**: 2026-07-22T08:07:37Z
- **Modalita' coinvolte**: minimax

### `minimax_429_rpm` (429)

- **Firma**: `6a84d4c176ef7e21`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 10
- **Prima volta**: 2026-07-29T19:44:18+0200
- **Ultima volta**: 2026-07-29T19:48:25+0200
- **Modalita' coinvolte**: minimax
- **Esempio**: `{"type":"error","error":{"type":"rate_limit_error","message":"Token Plan rate limit reached: Upgrade your Token Plan or switch to pay-as-you-go API usage. (2062)"},"request_id":"06b96fe91c140ad519ecfa8d7eba5833"}`

### `tool_isolation_strip`

- **Firma**: `f2020e9e7f5cab0e`
- **Severita'**: info
- **Occorrenze**: 1562
- **Prima volta**: 2026-07-19T22:00:32Z
- **Ultima volta**: 2026-07-22T06:53:46Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['mcp__zai__web_search_prime'] kept=2/3`

### `tool_isolation_strip`

- **Firma**: `e44837cc566fd378`
- **Severita'**: info
- **Occorrenze**: 958
- **Prima volta**: 2026-07-22T19:20:57Z
- **Ultima volta**: 2026-07-31T06:11:24+0200
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['WebFetch', 'WebSearch', 'mcp__zai__web_search_prime'] kept=22/25`

### `tool_isolation_strip`

- **Firma**: `0de3df722554d9aa`
- **Severita'**: info
- **Occorrenze**: 6
- **Prima volta**: 2026-07-22T23:32:11Z
- **Ultima volta**: 2026-07-29T09:26:03+0200
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['WebFetch', 'WebSearch'] kept=19/21`

### `tool_isolation_strip`

- **Firma**: `a6cb20cc79d422af`
- **Severita'**: info
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T06:56:59Z
- **Ultima volta**: 2026-07-22T06:57:02Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['mcp__zai__web_search_prime', 'WebSearch'] kept=1/3`

### `tool_isolation_strip`

- **Firma**: `a523d7b685872d06`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-08-03T19:52:34+0200
- **Ultima volta**: 2026-08-03T19:52:34+0200
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['mcp__websearch__search', 'mcp__zai__web_search_prime', 'web_search_20250305'] kept=2/5`

### `tool_isolation_strip`

- **Firma**: `3a3b86a151e6a0af`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-07-26T08:28:18Z
- **Ultima volta**: 2026-07-26T08:28:18Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['web_search'] kept=1/2`

### `tool_isolation_strip`

- **Firma**: `d8907f8dc0ec3574`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T21:35:33Z
- **Ultima volta**: 2026-07-19T21:35:33Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['mcp__zai__webSearchPrime'] kept=2/3`

## Modalita': `mix-ag`

4 tipi distinti, 8 occorrenze.

### `relay_error_400` (400)

- **Firma**: `0760b542de558c08`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 4
- **Prima volta**: 2026-07-26T08:34:38Z
- **Ultima volta**: 2026-07-26T08:35:51Z
- **Modalita' coinvolte**: mix-ag

### `glm_act_fail` (502)

- **Firma**: `edf244cbd96bf64f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T08:43:17Z
- **Ultima volta**: 2026-07-22T08:45:28Z
- **Modalita' coinvolte**: mix-ag

### `relay_error_400` (400)

- **Firma**: `90a11f6016d29c33`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-26T08:39:55Z
- **Ultima volta**: 2026-07-26T08:39:55Z
- **Modalita' coinvolte**: mix-ag
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"messages.1.content.0.server_tool_use.id: String should match pattern '^srvtoolu_[a-zA-Z0-9_]+$'"},"request_id":"req_011CdQDBYcUMhEtsmpC4WTAg"}`

### `mixed_rescue_502` (429)

- **Firma**: `dd18d0e8333cf3ef`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T08:43:23Z
- **Ultima volta**: 2026-07-22T08:43:23Z
- **Modalita' coinvolte**: mix-ag

## Modalita': `mix-al`

2 tipi distinti, 2 occorrenze.

### `relay_error_401` (401)

- **Firma**: `4d884a51d70b2507`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-04T19:50:36+0200
- **Ultima volta**: 2026-08-04T19:50:36+0200
- **Modalita' coinvolte**: mix-al
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011Cdi8c9KSQ1zK9KPCoqV2D"}`

### `relay_error_401` (401)

- **Firma**: `8dafb65f54f3d1b3`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-04T15:17:07+0200
- **Ultima volta**: 2026-08-04T15:17:07+0200
- **Modalita' coinvolte**: mix-al
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"},"request_id":"req_011CdhmkY2TohbdDbgJgXSYp"}`

## Modalita': `mix-am`

61 tipi distinti, 1636 occorrenze.

### `relay_error_400` (400)

- **Firma**: `7e8a25a070a650d4`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 245
- **Prima volta**: 2026-08-04T03:57:55+0200
- **Ultima volta**: 2026-08-04T04:57:24+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"messages.3.content.0: Invalid `signature` in `thinking` block"},"request_id":"req_011CdgxVc8sNdXaRxtpWxFNR"}`

### `relay_error_529` (529)

- **Firma**: `68be3329c4f78992`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 61
- **Prima volta**: 2026-07-28T22:08:59+0200
- **Ultima volta**: 2026-08-06T21:49:18+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"The server cluster is currently under high load. Please retry after a short wait and thank you for your patience. (2064) (529)"},"request_id":"06c4183e5d4f1cd7e8f790af3a26a4ec"}`

### `minimax_fallback_5xx` (404)

- **Firma**: `88d4bff48887eba9`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 26
- **Prima volta**: 2026-07-19T19:57:17Z
- **Ultima volta**: 2026-07-19T21:17:02Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `<html> <head><title>404 Not Found</title></head> <body> <center><h1>404 Not Found</h1></center> <hr><center>nginx</center> </body> </html>`

### `minimax_fallback_5xx` (502)

- **Firma**: `9fb265787ba5870f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 23
- **Prima volta**: 2026-07-19T19:40:52Z
- **Ultima volta**: 2026-07-19T20:10:23Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `<html> <head><title>502 Bad Gateway</title></head> <body bgcolor="white"> <center><h1>502 Bad Gateway</h1></center> <hr><center>alb</center> </body> </html>`

### `relay_error_404` (404)

- **Firma**: `0183d419f56edbaa`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 14
- **Prima volta**: 2026-07-19T19:51:42Z
- **Ultima volta**: 2026-07-19T20:35:05Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `<html> <head><title>404 Not Found</title></head> <body> <center><h1>404 Not Found</h1></center> <hr><center>nginx</center> </body> </html>`

### `mixed_rescue_502` (502)

- **Firma**: `9fb8ca0846b7e32d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 11
- **Prima volta**: 2026-07-19T21:49:34Z
- **Ultima volta**: 2026-07-19T22:00:41Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `haiku_stage=200`

### `minimax_fallback_5xx` (404)

- **Firma**: `5e1fe11c7a9f5dbe`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 10
- **Prima volta**: 2026-07-21T20:40:58Z
- **Ultima volta**: 2026-07-21T20:42:48Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `404 page not found`

### `ctx_gate` (error)

- **Firma**: `4f58b6f250a6b49a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 8
- **Prima volta**: 2026-08-02T12:37:58+0200
- **Ultima volta**: 2026-08-02T13:38:30+0200
- **Modalita' coinvolte**: mix-am

### `mixed_rescue_502` (429)

- **Firma**: `9b3ebacbfd731ed8`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 6
- **Prima volta**: 2026-07-19T22:41:01Z
- **Ultima volta**: 2026-07-23T14:05:51Z
- **Modalita' coinvolte**: mix-am

### `minimax_fallback_5xx` (529)

- **Firma**: `0e26f4196a1892d1`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 4
- **Prima volta**: 2026-07-21T07:04:59Z
- **Ultima volta**: 2026-07-21T07:16:01Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"overloaded_error","message":"The server cluster is currently under high load. Please retry after a short wait and thank you for your patience. (2064) (529)"},"request_id":"06ae3391b50f3513a44220f22331796d"}`

### `relay_error_401` (401)

- **Firma**: `7f555ffef175ea17`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-28T16:39:33+0200
- **Ultima volta**: 2026-08-04T19:05:11+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"authentication_error","message":"OAuth access token has been revoked."},"request_id":null}`

### `relay_error_400` (400)

- **Firma**: `90aa86dc348601fe`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-19T22:32:12Z
- **Ultima volta**: 2026-07-26T08:35:51Z
- **Modalita' coinvolte**: mix-am

### `relay_error_404` (404)

- **Firma**: `32299002d5d61611`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-21T20:41:04Z
- **Ultima volta**: 2026-07-21T20:41:13Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"error":{"code":"not_found_error","message":"model: MiniMax-M2.7","type":"invalid_request_error","param":null}}`

### `sse_truncated` (200)

- **Firma**: `85d6aeec47622a46`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:16:18+0200
- **Ultima volta**: 2026-08-01T11:16:18+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `8610473176eda62a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:16:08+0200
- **Ultima volta**: 2026-08-01T11:16:08+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"model":"claude-opus-5","id":"msg_011CdbmwkcnJVhM3gjuvY1Lx","type":"message","role":"assistant","content":[],"stop_reason":null,"stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":757,"cache_re`

### `sse_truncated` (200)

- **Firma**: `1be467d0b61c1b5b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:16:02+0200
- **Ultima volta**: 2026-08-01T11:16:02+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `e596af59ea833911`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:15:24+0200
- **Ultima volta**: 2026-08-01T11:15:24+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `101286811a27ceb4`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:14:44+0200
- **Ultima volta**: 2026-08-01T11:14:44+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `9b483f433739726b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:14:17+0200
- **Ultima volta**: 2026-08-01T11:14:17+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `647fd23d38a56999`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:14:03+0200
- **Ultima volta**: 2026-08-01T11:14:03+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `df507faad6d3a942`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:13:45+0200
- **Ultima volta**: 2026-08-01T11:13:45+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `e77fbe06f6044f58`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:13:34+0200
- **Ultima volta**: 2026-08-01T11:13:34+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `7b328acf67357956`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:13:19+0200
- **Ultima volta**: 2026-08-01T11:13:19+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `bbfece84959f6875`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:12:50+0200
- **Ultima volta**: 2026-08-01T11:12:50+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `1b3eeda60339f00e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:11:55+0200
- **Ultima volta**: 2026-08-01T11:11:55+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `93e7b1cf1b7f37bb`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:11:30+0200
- **Ultima volta**: 2026-08-01T11:11:30+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `7d5e93d3af33606e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:11:23+0200
- **Ultima volta**: 2026-08-01T11:11:23+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `3c4dec68d18c63be`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:11:11+0200
- **Ultima volta**: 2026-08-01T11:11:11+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"model":"claude-opus-5","id":"msg_011CdbmZzgW1kKGPY4ZfVKZV","type":"message","role":"assistant","content":[],"stop_reason":null,"stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":3513,"cache_r`

### `sse_truncated` (200)

- **Firma**: `ccfe7029751c241e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:11:07+0200
- **Ultima volta**: 2026-08-01T11:11:07+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `543b28742f401d0b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:10:16+0200
- **Ultima volta**: 2026-08-01T11:10:16+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `8357f91b95889f97`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:10:09+0200
- **Ultima volta**: 2026-08-01T11:10:09+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `62c23e32ab972104`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:10:03+0200
- **Ultima volta**: 2026-08-01T11:10:03+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `1f8c1800f09ecda9`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:09:51+0200
- **Ultima volta**: 2026-08-01T11:09:51+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `0d74940a2690c272`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:09:24+0200
- **Ultima volta**: 2026-08-01T11:09:24+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `a374cfd6f17b607c`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:08:18+0200
- **Ultima volta**: 2026-08-01T11:08:18+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `91140d36af612e1d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:07:54+0200
- **Ultima volta**: 2026-08-01T11:07:54+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `154105fdbc573f6d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:07:30+0200
- **Ultima volta**: 2026-08-01T11:07:30+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `a9735e18f2390576`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:07:09+0200
- **Ultima volta**: 2026-08-01T11:07:09+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"model":"claude-opus-5","id":"msg_011CdbmG54vTks7CCjz56p72","type":"message","role":"assistant","content":[],"stop_reason":null,"stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":589,"cache_re`

### `sse_truncated` (200)

- **Firma**: `34981a8e96b9e253`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:07:04+0200
- **Ultima volta**: 2026-08-01T11:07:04+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `6e2d4ca323767714`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:06:57+0200
- **Ultima volta**: 2026-08-01T11:06:57+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `d38991877002974a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:06:47+0200
- **Ultima volta**: 2026-08-01T11:06:47+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"model":"claude-opus-5","id":"msg_011CdbmERtkhx563pjKFDuZZ","type":"message","role":"assistant","content":[],"stop_reason":null,"stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":921,"cache_re`

### `sse_truncated` (200)

- **Firma**: `c932fca791b9651b`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:05:57+0200
- **Ultima volta**: 2026-08-01T11:05:57+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `fd00ad6b4d791678`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:05:42+0200
- **Ultima volta**: 2026-08-01T11:05:42+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `54f35add9fc1b39f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:05:23+0200
- **Ultima volta**: 2026-08-01T11:05:23+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `a09675364add326d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:05:16+0200
- **Ultima volta**: 2026-08-01T11:05:16+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `8973ddccfca448e5`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:05:07+0200
- **Ultima volta**: 2026-08-01T11:05:07+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `sse_truncated` (200)

- **Firma**: `45eee877bcc811bc`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:04:52+0200
- **Ultima volta**: 2026-08-01T11:04:52+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `[binario non testuale, 300 caratteri]`

### `relay_error_400` (400)

- **Firma**: `24ef72108617c482`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T10:44:43+0200
- **Ultima volta**: 2026-08-01T10:44:43+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"invalid params, messages must not be empty (2013)"},"request_id":"06bce4fb25cd0a307c882b414c3dae71"}`

### `relay_error_404` (404)

- **Firma**: `90c9d32c570b4531`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T07:46:26+0200
- **Ultima volta**: 2026-08-01T07:46:26+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `404 page not found`

### `pseudo_toolcall_text` (200)

- **Firma**: `12403ca7d27db8dc`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-29T19:00:18+0200
- **Ultima volta**: 2026-07-29T19:00:18+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"id":"06b9649c9328dc6d00e29dad343df477","type":"message","role":"assistant","content":[],"model":"MiniMax-M2.7","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":32260,"cache`

### `pseudo_toolcall_text` (200)

- **Firma**: `502469bd49dab602`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-29T18:01:24+0200
- **Ultima volta**: 2026-07-29T18:01:24+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"id":"06b956c8fa1ba9e86875e04de8763e02","type":"message","role":"assistant","content":[],"model":"MiniMax-M2.7","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1640,"output_tokens":0,"cache_creation_input_tokens":0,"cache_`

### `pseudo_toolcall_text` (200)

- **Firma**: `25d958919f45a214`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-29T17:51:41+0200
- **Ultima volta**: 2026-07-29T17:51:41+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"id":"06b95489ec0f17dd5df34899ad595322","type":"message","role":"assistant","content":[],"model":"minimax-m2.7-hs","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":17913,"ca`

### `pseudo_toolcall_text` (200)

- **Firma**: `9a5dbdcdb3d41af2`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-29T17:51:37+0200
- **Ultima volta**: 2026-07-29T17:51:37+0200
- **Modalita' coinvolte**: mix-am
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"id":"06b954800880ee608d7e144da144165e","type":"message","role":"assistant","content":[],"model":"MiniMax-M2.7","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":17708,"output_tokens":0}}}  event: ping data: {"type":"ping"}`

### `relay_error_400` (400)

- **Firma**: `351d784df2502cb2`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-26T08:39:55Z
- **Ultima volta**: 2026-07-26T08:39:55Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"messages.1.content.0.server_tool_use.id: String should match pattern '^srvtoolu_[a-zA-Z0-9_]+$'"},"request_id":"req_011CdQDBZtNFvWkWaUS383wh"}`

### `mixed_rescue_502` (400)

- **Firma**: `4eeae187d3a31265`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-21T20:41:32Z
- **Ultima volta**: 2026-07-21T20:41:32Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"Tool reference 'mcp__MiniMax__understand_image' not found in available tools"},"request_id":"req_011CdFhATYpN2rySQGKPywEK"}`

### `think_timeout`

- **Firma**: `8a469baab0cd9f7b`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 202
- **Prima volta**: 2026-07-19T22:56:44Z
- **Ultima volta**: 2026-07-21T22:47:06Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `budget=8s, seq=[4, 6, 8]`

### `think_fast_timeout`

- **Firma**: `c81adba93d7107b8`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 122
- **Prima volta**: 2026-07-19T21:04:10Z
- **Ultima volta**: 2026-07-19T22:48:14Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `budget=4.0s`

### `think_status_ko`

- **Firma**: `00d9133b3dc6ef93`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 10
- **Prima volta**: 2026-07-23T07:55:36Z
- **Ultima volta**: 2026-07-23T14:40:16Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `status=0`

### `ctx_gate` (compact)

- **Firma**: `09821937d153c11b`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-29T19:58:10+0200
- **Ultima volta**: 2026-08-02T00:37:42+0200
- **Modalita' coinvolte**: mix-am

### `ctx_gate` (ok)

- **Firma**: `4e55ecf61db90635`
- **Severita'**: info
- **Occorrenze**: 837
- **Prima volta**: 2026-07-26T20:35:53+0200
- **Ultima volta**: 2026-08-08T00:42:28+0200
- **Modalita' coinvolte**: mix-am

### `ctx_gate` (warn)

- **Firma**: `9fad96d29dda127b`
- **Severita'**: info
- **Occorrenze**: 7
- **Prima volta**: 2026-07-30T03:47:32+0200
- **Ultima volta**: 2026-08-07T01:26:50+0200
- **Modalita' coinvolte**: mix-am

## Modalita': `mix-gm`

17 tipi distinti, 103 occorrenze.

### `relay_error_502` (502)

- **Firma**: `25be681a8499774f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 23
- **Prima volta**: 2026-07-30T08:45:18+0200
- **Ultima volta**: 2026-07-31T08:22:27+0200
- **Modalita' coinvolte**: mix-gm

### `minimax_act_fail` (502)

- **Firma**: `c8d3fc78ef3b7841`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 6
- **Prima volta**: 2026-07-23T02:16:46Z
- **Ultima volta**: 2026-07-23T02:17:01Z
- **Modalita' coinvolte**: mix-gm

### `ctx_gate` (error)

- **Firma**: `adf11f28133acf4d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 4
- **Prima volta**: 2026-07-30T04:01:00+0200
- **Ultima volta**: 2026-07-30T20:38:53+0200
- **Modalita' coinvolte**: mix-gm

### `minimax_act_fail` (404)

- **Firma**: `e139ea6c37521d22`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 3
- **Prima volta**: 2026-07-19T21:14:54Z
- **Ultima volta**: 2026-07-19T21:17:06Z
- **Modalita' coinvolte**: mix-gm

### `sse_truncated` (200)

- **Firma**: `7487349871d9731e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:02:31+0200
- **Ultima volta**: 2026-08-01T11:02:31+0200
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `data: test`

### `sse_truncated` (200)

- **Firma**: `6db3c0f0fe060c93`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:02:31+0200
- **Ultima volta**: 2026-08-01T11:02:31+0200
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `data: unodata: duedata: tre`

### `sse_truncated` (200)

- **Firma**: `727632e732c65914`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:02:31+0200
- **Ultima volta**: 2026-08-01T11:02:31+0200
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `data: extra`

### `sse_truncated` (200)

- **Firma**: `c15f7875267aff6a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-01T11:02:22+0200
- **Ultima volta**: 2026-08-01T11:02:22+0200
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `data: {"type":"content_block_delta","delta":{"text":"c0"}}  data: {"type":"content_block_delta","delta":{"text":"c1"}}  data: {"type":"content_block_delta","delta":{"text":"c2"}}  data: {"type":"content_block_delta","delta":{"text":"c3"}}  data: {"type":"content_block_delta","delta":{"text":"c4"}}`

### `pseudo_toolcall_text` (200)

- **Firma**: `dcd8d6730c2e8b6a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-31T06:47:43+0200
- **Ultima volta**: 2026-07-31T06:47:43+0200
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `event: message_start data: {"type":"message_start","message":{"id":"06bb5be8e0edbf315cac928aa7742f6d","type":"message","role":"assistant","content":[],"model":"claude-haiku-4-5-20251001","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1982,"output_tokens":0}}}  event: ping data: {"ty`

### `hhem_warning`

- **Firma**: `481dc62ecee84017`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 3
- **Prima volta**: 2026-07-19T21:20:46Z
- **Ultima volta**: 2026-07-22T06:40:36Z
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `score=0.202 < 0.5`

### `verify_incoherent`

- **Firma**: `80fb93724585f678`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T02:44:12Z
- **Ultima volta**: 2026-07-23T02:44:12Z
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `INCOERENTE: l'output deve contenere esattamente la stringa richiesta dal piano (`

### `verify_incoherent`

- **Firma**: `7251f929859dc81c`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T02:42:40Z
- **Ultima volta**: 2026-07-23T02:42:40Z
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `INCOERENTE: Output vuoto, manca la risposta di conferma prevista dal piano.`

### `verify_incoherent`

- **Firma**: `85bcd87d948d4108`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T02:42:31Z
- **Ultima volta**: 2026-07-23T02:42:31Z
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `INCOERENTE: Output vuoto, manca l'esecuzione del piano (interazione con l'utente`

### `verify_incoherent`

- **Firma**: `34b877180cb5ed79`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T02:16:17Z
- **Ultima volta**: 2026-07-23T02:16:17Z
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `INCOERENTE: L'output è vuoto. Mancano le richieste di chiarimento previste dal p`

### `verify_incoherent`

- **Firma**: `b606c3924f406f57`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T06:32:51Z
- **Ultima volta**: 2026-07-22T06:32:51Z
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `INCOERENTE: L'output contiene preamboli, ragionamento testuale e tag di sistema`

### `verify_incoherent`

- **Firma**: `6aaf039f2be16744`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T21:20:55Z
- **Ultima volta**: 2026-07-19T21:20:55Z
- **Modalita' coinvolte**: mix-gm
- **Esempio**: `INCOERENTE: Il piano richiede di restituire direttamente l'output richiesto ("ok`

### `ctx_gate` (ok)

- **Firma**: `b07fa3b395ea9f7b`
- **Severita'**: info
- **Occorrenze**: 53
- **Prima volta**: 2026-07-30T04:06:45+0200
- **Ultima volta**: 2026-08-01T10:44:43+0200
- **Modalita' coinvolte**: mix-gm

## Modalita': `qwen`

11 tipi distinti, 1290 occorrenze.

### `forward_exception`

- **Firma**: `3dd7ae26d5ea7416`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-08-03T21:34:48+0200
- **Ultima volta**: 2026-08-04T03:18:05+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `Cannot write to closing transport`

### `relay_error_502` (502)

- **Firma**: `fff6b6e96db2df6e`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-08-03T20:48:22+0200
- **Ultima volta**: 2026-08-03T20:53:33+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `{"type": "error", "error": {"type": "qwen_unavailable", "message": "qwen key missing"}}`

### `relay_error_400` (400)

- **Firma**: `fa87d598c192ba37`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-04T03:31:50+0200
- **Ultima volta**: 2026-08-04T03:31:50+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `{"code":"InvalidParameter","message":"Required parameter \"model\" missing from request.","request_id":"6565b2ec-ef71-93eb-b766-05ddebf88c46"}`

### `relay_error_502` (502)

- **Firma**: `b2243ef32a570224`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-03T20:23:42+0200
- **Ultima volta**: 2026-08-03T20:23:42+0200
- **Modalita' coinvolte**: qwen

### `forward_exception`

- **Firma**: `92ed741733190a3d`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-08-03T20:23:42+0200
- **Ultima volta**: 2026-08-03T20:23:42+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `'Response' object has no attribute 'release'`

### `qwen_429_backoff` (429)

- **Firma**: `303e54f4ad58feb1`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 42
- **Prima volta**: 2026-08-03T20:46:18+0200
- **Ultima volta**: 2026-08-04T09:08:06+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `backoff 5s`

### `tool_isolation_strip`

- **Firma**: `1418176599f956bc`
- **Severita'**: info
- **Occorrenze**: 779
- **Prima volta**: 2026-08-04T02:27:56+0200
- **Ultima volta**: 2026-08-04T11:12:05+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `stripped=['WebFetch', 'WebSearch', 'mcp__zai__web_search_prime'] kept=300/303`

### `heavy_connector_strip`

- **Firma**: `4ccd5828ee0b363a`
- **Severita'**: info
- **Occorrenze**: 448
- **Prima volta**: 2026-08-04T03:41:20+0200
- **Ultima volta**: 2026-08-06T23:41:03+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `stripped=2 kept=1/3`

### `ctx_gate` (ok)

- **Firma**: `632bb88617021f96`
- **Severita'**: info
- **Occorrenze**: 7
- **Prima volta**: 2026-08-04T04:02:30+0200
- **Ultima volta**: 2026-08-04T11:10:18+0200
- **Modalita' coinvolte**: qwen

### `tool_isolation_strip`

- **Firma**: `ece54103702c420c`
- **Severita'**: info
- **Occorrenze**: 6
- **Prima volta**: 2026-08-04T04:05:10+0200
- **Ultima volta**: 2026-08-04T04:07:10+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `stripped=['WebFetch', 'WebSearch'] kept=19/21`

### `tool_isolation_strip`

- **Firma**: `23762303131b59d6`
- **Severita'**: info
- **Occorrenze**: 1
- **Prima volta**: 2026-08-03T19:52:34+0200
- **Ultima volta**: 2026-08-03T19:52:34+0200
- **Modalita' coinvolte**: qwen
- **Esempio**: `stripped=['mcp__MiniMax__web_search', 'mcp__zai__web_search_prime', 'web_search_20250305'] kept=2/5`
