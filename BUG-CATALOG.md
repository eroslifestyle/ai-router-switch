# BUG-CATALOG.md

> Generato automaticamente da `scripts/generate_bug_report.py` a partire da `logs/BUG-CATALOG.jsonl`. Non modificare a mano — rilanciare lo script. Vedi `DEBUG-CATALOG-SPEC.md` per lo schema completo.

**112 tipi distinti di bug/blocco/errore** · **8974 occorrenze totali** su 6 modalita'.

## Modalita': `anthropic`

51 tipi distinti, 5272 occorrenze.

### `rate_limit_429_exhausted` (429)

- **Firma**: `9c7020f92cb35d8a`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 65
- **Prima volta**: 2026-07-22T11:14:43Z
- **Ultima volta**: 2026-07-25T16:04:15Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `retry-after=?`

### `relay_error_400` (400)

- **Firma**: `752f347e44651cc7`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 25
- **Prima volta**: 2026-07-21T20:44:09Z
- **Ultima volta**: 2026-07-23T15:26:58Z
- **Modalita' coinvolte**: anthropic

### `rate_limit_429_exhausted` (429)

- **Firma**: `a0c07b40348d36be`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 3
- **Prima volta**: 2026-07-23T15:33:35Z
- **Ultima volta**: 2026-07-23T15:42:52Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `retry-after=80227`

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

### `tool_isolation_strip`

- **Firma**: `1b910b74f7b880e0`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 4612
- **Prima volta**: 2026-07-19T21:40:09Z
- **Ultima volta**: 2026-07-25T19:14:39Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search', 'mcp__zai__web_search_prime'] kept=302/305`

### `tool_isolation_strip`

- **Firma**: `5eb9aca25569b15f`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 343
- **Prima volta**: 2026-07-19T19:41:54Z
- **Ultima volta**: 2026-07-25T16:02:43Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search'] kept=302/304`

### `rate_limit_429`

- **Firma**: `e648db5b3f6e697e`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 128
- **Prima volta**: 2026-07-22T13:08:58Z
- **Ultima volta**: 2026-07-25T16:04:14Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic 429: retry 2/2 retry-after=None sleep=0.62s`

### `burst_limiter_429` (429)

- **Firma**: `dcbba7265b9b5693`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 39
- **Prima volta**: 2026-07-19T20:19:06Z
- **Ultima volta**: 2026-07-22T11:04:05Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `retry-after=1.5s`

### `tool_isolation_strip`

- **Firma**: `3f14d8ef8e1d0fab`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 8
- **Prima volta**: 2026-07-22T06:53:40Z
- **Ultima volta**: 2026-07-22T06:56:57Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__zai__web_search_prime', 'mcp__MiniMax__web_search'] kept=1/3`

### `rate_limit_429`

- **Firma**: `500f69599f86d10e`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 6
- **Prima volta**: 2026-07-23T15:31:34Z
- **Ultima volta**: 2026-07-23T15:41:51Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic 429: retry 2/2 retry-after=80288.0 sleep=60.00s`

### `rate_limit_429` (429)

- **Firma**: `435d593edd4ea264`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T11:14:41Z
- **Ultima volta**: 2026-07-22T11:14:42Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `attempt=2/2 retry-after=None sleep=0.60s`

### `rate_limit_429`

- **Firma**: `b5af13ec30de3db5`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T12:31:17Z
- **Ultima volta**: 2026-07-22T12:31:17Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `anthropic EXC: retry 1/2 sleep=0.28s (Timeout on reading data from socket)`

### `tool_isolation_strip`

- **Firma**: `9118774d9a6c02eb`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T21:35:33Z
- **Ultima volta**: 2026-07-19T21:35:33Z
- **Modalita' coinvolte**: anthropic
- **Esempio**: `stripped=['mcp__zai__webSearchPrime', 'mcp__MiniMax__web_search'] kept=1/3`

## Modalita': `glm`

28 tipi distinti, 693 occorrenze.

### `glm_client_error`

- **Firma**: `ad6e7113c00559b1`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 44
- **Prima volta**: 2026-07-19T20:25:21Z
- **Ultima volta**: 2026-07-25T17:23:59Z
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

### `glm_exhausted` (502)

- **Firma**: `35ea096780db1793`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 7
- **Prima volta**: 2026-07-22T08:45:29Z
- **Ultima volta**: 2026-07-25T17:23:59Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=glm-5.2`

### `glm_exhausted` (502)

- **Firma**: `ba67794095935766`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 4
- **Prima volta**: 2026-07-22T08:43:17Z
- **Ultima volta**: 2026-07-22T08:51:25Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-sonnet-5`

### `glm_client_error`

- **Firma**: `c70a31881f6ba0c6`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 4
- **Prima volta**: 2026-07-19T20:22:07Z
- **Ultima volta**: 2026-07-19T20:49:46Z
- **Modalita' coinvolte**: glm
- **Esempio**: `Server disconnected`

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

### `tool_isolation_strip`

- **Firma**: `e5dc8d4b2d987612`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 308
- **Prima volta**: 2026-07-22T07:29:23Z
- **Ultima volta**: 2026-07-22T13:39:35Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['WebFetch', 'WebSearch', 'mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search'] kept=39/43`

### `tool_isolation_strip`

- **Firma**: `f9d1fac61fb04f47`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 109
- **Prima volta**: 2026-07-19T20:18:51Z
- **Ultima volta**: 2026-07-22T06:38:36Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__understand_image', 'mcp__MiniMax__web_search'] kept=274/276`

### `glm_ratelimit_exhausted` (429)

- **Firma**: `4ede846b53432b7d`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 34
- **Prima volta**: 2026-07-22T08:40:55Z
- **Ultima volta**: 2026-07-22T13:32:51Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=claude-opus-4-8 glm rate-limit: budget 8s esaurito (waited 0s)`

### `tool_isolation_strip`

- **Firma**: `2cf1321f17c5adda`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 31
- **Prima volta**: 2026-07-22T08:07:41Z
- **Ultima volta**: 2026-07-22T10:46:19Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['WebFetch', 'WebSearch'] kept=22/24`

### `glm_429_backoff` (429)

- **Firma**: `af19763f9b35d220`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 30
- **Prima volta**: 2026-07-22T08:40:45Z
- **Ultima volta**: 2026-07-22T13:33:15Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=1 backoff=60s model=claude-opus-4-8`

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

### `glm_429_backoff` (429)

- **Firma**: `f2484b535f2ec78c`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 7
- **Prima volta**: 2026-07-22T08:44:25Z
- **Ultima volta**: 2026-07-22T08:48:37Z
- **Modalita' coinvolte**: glm
- **Esempio**: `attempt=2 backoff=60s model=glm-5.2`

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

### `glm_ratelimit_exhausted` (429)

- **Firma**: `5d2ba46f12bd738f`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T10:37:17Z
- **Ultima volta**: 2026-07-22T10:37:27Z
- **Modalita' coinvolte**: glm
- **Esempio**: `model=glm-4.7 glm rate-limit: budget 8s esaurito (waited 0s)`

### `tool_isolation_strip`

- **Firma**: `c04f3e9878f7fa6c`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-19T21:35:33Z
- **Ultima volta**: 2026-07-22T06:53:48Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__web_search'] kept=2/3`

### `tool_isolation_strip`

- **Firma**: `54ae025d11c9e975`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-23T01:11:32Z
- **Ultima volta**: 2026-07-23T01:11:32Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__zai__web_search_prime'] kept=1/2`

### `tool_isolation_strip`

- **Firma**: `e119ad9c18e6e970`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T07:21:28Z
- **Ultima volta**: 2026-07-22T07:21:28Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['WebSearch', 'WebFetch'] kept=1/3`

### `tool_isolation_strip`

- **Firma**: `3b89b0095896c827`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T06:57:04Z
- **Ultima volta**: 2026-07-22T06:57:04Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__web_search', 'WebSearch'] kept=1/3`

### `tool_isolation_strip`

- **Firma**: `86b15a0bd239ccc0`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-21T23:25:21Z
- **Ultima volta**: 2026-07-21T23:25:21Z
- **Modalita' coinvolte**: glm
- **Esempio**: `stripped=['mcp__MiniMax__understand_image'] kept=0/1`

## Modalita': `minimax`

9 tipi distinti, 2556 occorrenze.

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
- **Esempio**: `<html> <head><title>404 Not Found</title></head> <body> <center><h1>404 Not Found</h1></center> <hr><center>nginx</center> </body> </html>`

### `tool_isolation_strip`

- **Firma**: `f2020e9e7f5cab0e`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1562
- **Prima volta**: 2026-07-19T22:00:32Z
- **Ultima volta**: 2026-07-22T06:53:46Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['mcp__zai__web_search_prime'] kept=2/3`

### `tool_isolation_strip`

- **Firma**: `e44837cc566fd378`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 939
- **Prima volta**: 2026-07-22T19:20:57Z
- **Ultima volta**: 2026-07-23T14:47:15Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['WebFetch', 'WebSearch', 'mcp__zai__web_search_prime'] kept=43/46`

### `think_plan_invalid`

- **Firma**: `d9594c530578e523`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 40
- **Prima volta**: 2026-07-19T21:20:35Z
- **Ultima volta**: 2026-07-22T08:07:37Z
- **Modalita' coinvolte**: minimax

### `minimax_429_rpm` (429)

- **Firma**: `2aa4770210ced1e8`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 6
- **Prima volta**: 2026-07-22T11:43:41Z
- **Ultima volta**: 2026-07-22T11:45:55Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `{"type": "error", "error": {"type": "rate_limit_error", "message": "fake rate limit"}}`

### `tool_isolation_strip`

- **Firma**: `0de3df722554d9aa`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T23:32:11Z
- **Ultima volta**: 2026-07-22T23:39:55Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['WebFetch', 'WebSearch'] kept=38/40`

### `tool_isolation_strip`

- **Firma**: `a6cb20cc79d422af`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T06:56:59Z
- **Ultima volta**: 2026-07-22T06:57:02Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['mcp__zai__web_search_prime', 'WebSearch'] kept=1/3`

### `tool_isolation_strip`

- **Firma**: `d8907f8dc0ec3574`
- **Severita'**: 🟡 Blocchi
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T21:35:33Z
- **Ultima volta**: 2026-07-19T21:35:33Z
- **Modalita' coinvolte**: minimax
- **Esempio**: `stripped=['mcp__zai__webSearchPrime'] kept=2/3`

## Modalita': `mix-ag`

2 tipi distinti, 3 occorrenze.

### `glm_act_fail` (502)

- **Firma**: `edf244cbd96bf64f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-22T08:43:17Z
- **Ultima volta**: 2026-07-22T08:45:28Z
- **Modalita' coinvolte**: mix-ag

### `mixed_rescue_502` (429)

- **Firma**: `dd18d0e8333cf3ef`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-22T08:43:23Z
- **Ultima volta**: 2026-07-22T08:43:23Z
- **Modalita' coinvolte**: mix-ag

## Modalita': `mix-am`

13 tipi distinti, 432 occorrenze.

### `minimax_fallback_5xx` (404)

- **Firma**: `88d4bff48887eba9`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 26
- **Prima volta**: 2026-07-19T19:57:17Z
- **Ultima volta**: 2026-07-19T21:17:02Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `<html> <head><title>404 Not Found</title></head> <body> <center><h1>404 Not Found</h1></center> <hr><center>nginx</center> </body> </html>`

### `minimax_fallback_5xx` (502)

- **Firma**: `9fb265787ba5870f`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 23
- **Prima volta**: 2026-07-19T19:40:52Z
- **Ultima volta**: 2026-07-19T20:10:23Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `<html> <head><title>502 Bad Gateway</title></head> <body bgcolor="white"> <center><h1>502 Bad Gateway</h1></center> <hr><center>alb</center> </body> </html>`

### `relay_error_404` (404)

- **Firma**: `0183d419f56edbaa`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 14
- **Prima volta**: 2026-07-19T19:51:42Z
- **Ultima volta**: 2026-07-19T20:35:05Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `<html> <head><title>404 Not Found</title></head> <body> <center><h1>404 Not Found</h1></center> <hr><center>nginx</center> </body> </html>`

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

### `relay_error_404` (404)

- **Firma**: `32299002d5d61611`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 2
- **Prima volta**: 2026-07-21T20:41:04Z
- **Ultima volta**: 2026-07-21T20:41:13Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"error":{"code":"not_found_error","message":"model: MiniMax-M2.7","type":"invalid_request_error","param":null}}`

### `mixed_rescue_502` (400)

- **Firma**: `4eeae187d3a31265`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-21T20:41:32Z
- **Ultima volta**: 2026-07-21T20:41:32Z
- **Modalita' coinvolte**: mix-am
- **Esempio**: `{"type":"error","error":{"type":"invalid_request_error","message":"Tool reference 'mcp__MiniMax__understand_image' not found in available tools"},"request_id":"req_011CdFhATYpN2rySQGKPywEK"}`

### `relay_error_400` (400)

- **Firma**: `90aa86dc348601fe`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 1
- **Prima volta**: 2026-07-19T22:32:12Z
- **Ultima volta**: 2026-07-19T22:32:12Z
- **Modalita' coinvolte**: mix-am

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

## Modalita': `mix-gm`

9 tipi distinti, 18 occorrenze.

### `minimax_act_fail` (502)

- **Firma**: `c8d3fc78ef3b7841`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 6
- **Prima volta**: 2026-07-23T02:16:46Z
- **Ultima volta**: 2026-07-23T02:17:01Z
- **Modalita' coinvolte**: mix-gm

### `minimax_act_fail` (404)

- **Firma**: `e139ea6c37521d22`
- **Severita'**: 🔴 Errori
- **Occorrenze**: 3
- **Prima volta**: 2026-07-19T21:14:54Z
- **Ultima volta**: 2026-07-19T21:17:06Z
- **Modalita' coinvolte**: mix-gm

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
