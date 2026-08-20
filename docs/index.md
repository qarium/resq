---
description: Getting started with resq — the public surface, quick sync and async requests, polling, and the two timeout meanings.
---

# resq

`resq` is a dual-mode HTTP client library: one set of verbs, with the mode and
engine selected by the `adapter` argument — `'requests'` for synchronous calls,
`'httpx'` for asynchronous ones.

The package exposes exactly two names from the top level, and that is the recommended way
to use it:

```python
from resq import Requests, Session
```

- `Requests` — HTTP client, fresh connection per call in sync mode.
- `Session` — HTTP client with a persistent sync connection reused across calls.

The response wrappers (`Response` / `AsyncResponse`) and the polling routine (`poll`) are
**not** re-exported here — reach them through the `resq.http` submodule only when needed
(see [The resq.http surface](reference/http-surface.md)).

---

## Quick sync request

The constructor `timeout` is the **network** timeout (connect/read), set once. It is NOT
the polling window.

```python
from resq import Requests

with Requests("https://api.example.com", adapter="requests", timeout=5) as client:
    r = client.get("/users/42", params={"detail": "full"})
    print(r.status_code, r.ok)   # int, bool
    data = r.json()              # parsed body
```

Every verb forwards keyword arguments (`params`, `headers`, `json`, `data`, …) verbatim
to the underlying engine.

## Quick async request

The same verbs run on a long-lived `httpx` client and must be awaited. Release it with
`async with` (preferred) or `await client.close()`:

```python
from resq import Session

async with Session("https://api.example.com", adapter="httpx", timeout=5) as client:
    r = await client.get("/users/42")
    data = r.json()
```

## Polling for a success status

Pass a `timeout` **on the method** to poll until a 2xx response arrives. This is a
different `timeout` from the constructor one — same name, different meaning by position:

```python
from resq import Requests

client = Requests("https://api.example.com", adapter="requests", timeout=5)  # network
r = client.get("/job/42", timeout=30, delay=2)                               # poll up to 30s
```

Async is identical, awaited (`adapter='httpx'`). See [Polling](guide/polling.md).

## Handling window exhaustion

If the polling window elapses without a success-status response, the LAST response is
returned (its status is the final non-2xx) — no exception. Inspect `ok` /
`status_code`, or call `reload` to retry:

```python
from resq import Requests

client = Requests("https://api.example.com", adapter="requests", timeout=5)
r = client.get("/job/42", timeout=30, delay=2)
if not r.ok:
    r.reload()           # sync; await r.reload() for an AsyncResponse
```

## Requests vs Session

- `Requests` — a fresh connection per sync call (module-level `requests` behavior). Pick
  it for one-off calls or when you do not need cookie/connection persistence.
- `Session` — one persistent `requests.Session` reused across sync calls (shared pool and
  cookie jar). Pick it for repeated calls to the same host.

Both share a single long-lived `httpx.AsyncClient` in async mode; release it via
`async with` or `await client.close()`.

## Where to go next

- [Clients & requests](guide/clients.md) — construction, all verbs, sync and async
  lifecycles.
- [Reading a response](guide/responses.md) — the unified attribute surface.
- [Reload](guide/reload.md) — re-executing a request in place.
- [Polling](guide/polling.md) — the polling window semantics.

## Preconditions

- `adapter` selects mode+engine: `'requests'` (sync) or `'httpx'` (async); other values
  raise. Fixed per instance.
- Constructor `timeout` = network timeout (connect/read), set once; method-level
  `timeout` = polling window. Same name, different meaning by position.
- With the method `timeout` left at `None` (default), a verb issues a single request and
  does not auto-raise on a non-2xx status — inspect `r.ok` or call `r.raise_for_status()`.
- Verb names are the same in both modes; in async mode they must be awaited.
- The facade re-exports only `Requests` and `Session`; the wrappers and `poll` live in
  `resq.http` for advanced typing.
