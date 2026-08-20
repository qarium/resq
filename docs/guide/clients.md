---
description: Constructing the resq HTTP clients and issuing sync and async requests — the unified verb surface and the two flavors.
---

# Clients & requests

This chapter covers constructing the `resq` HTTP clients and issuing sync and async
requests. The two client flavors are `Requests` and `Session`.

`resq` exposes two clients with the SAME unified verb surface; the mode (sync/async) and
engine are chosen by the `adapter` argument:

- `Requests` — a fresh connection per call in sync mode (module-level `requests`
  behavior).
- `Session` — one persistent `requests.Session` reused across sync calls (shared pool and
  cookie jar).

In async mode both flavors behave identically: each instance owns one long-lived
`httpx.AsyncClient` (held internally), shared across that instance's calls and reloads;
release it via `async with` (preferred) or `await client.close()`.

---

## Construction

!!! warning "Two different timeouts"

    The constructor `timeout` is the **network** timeout (connect/read), set once on the
    engine. It is NOT the polling window.

`adapter` selects the mode+engine and is fixed per instance.

```python
from resq import Requests, Session

client = Requests("https://api.example.com", adapter="requests", timeout=5)
async_client = Session("https://api.example.com", adapter="httpx", timeout=5)
```

Valid `adapter` values are exactly `'requests'` and `'httpx'`; any other value raises
`ValueError` (before an adapter is built).

## Sync requests (adapter='requests')

The unified verbs return the wrapper directly:

```python
with Requests("https://api.example.com", adapter="requests", timeout=5) as client:
    r = client.get("/users/42", params={"detail": "full"})
    r = client.post("/users", json={"name": "ada"})
    r = client.put("/users/42", json={"name": "ada"})
    r = client.delete("/users/42")
    r = client.patch("/users/42", json={"role": "admin"})
    r = client.head("/users/42")
    r = client.options("/users/42")
```

Any keyword arguments (`params`, `headers`, `json`, `data`, `cookies`, `files`, …) are
forwarded verbatim to the underlying engine.

## Async requests (adapter='httpx')

The SAME verbs return a coroutine that resolves to the wrapper — await them:

```python
async with Session("https://api.example.com", adapter="httpx", timeout=5) as client:
    r = await client.get("/users/42", params={"detail": "full"})
    r = await client.post("/users", json={"name": "ada"})
```

Release the async engine via `async with` (preferred) or `await client.close()`.

## Closing

- Sync mode: `with` (or no explicit close — the `requests.Session` held by `Session` is
  released by garbage collection, not closed explicitly).
- Async mode: `async with`, or `await client.close()` — releases the long-lived
  `httpx.AsyncClient` (idempotent; a coroutine).

## Behavior & preconditions

- One instance = one mode, fixed at construction by `adapter`.
- Paths are joined onto `base_url`; a leading `/` on the path is normalized so the
  same path string behaves identically across modes.
- The constructor `timeout` is the network timeout; the method-level `timeout`
  (when passed) is the polling window. `delay` (default 1.0) sets the seconds
  between polling attempts and is ignored when the method `timeout` is not passed.
- With the method `timeout` left at `None` (default), a verb issues a single request
  and does not auto-raise on a non-2xx status — inspect `r.ok` or call
  `r.raise_for_status()` yourself.
- The verb name is the same in both modes; in async mode it must be awaited.
