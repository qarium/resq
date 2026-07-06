# Clients — making requests

**Domain.** Creating `resq` HTTP clients and issuing sync and async requests.

**Audience.** Anyone consuming the `resq` public API to perform HTTP calls.

`resq` exposes two clients with the same verb surface:

- `Requests` — a fresh connection per sync call (module-level `requests` behavior);
  async `a*` verbs reuse a lazily-created, long-lived `httpx.AsyncClient` (one shared
  connection pool across all `a*` calls and `areload`); release it via `aclose` or
  `async with`.
- `Session` — a persistent sync connection (`requests.Session`) and a long-lived
  async client; call `aclose` when done.

---

## Construction

The constructor `timeout` is the **network** timeout (connect/read), set once. It is
NOT the polling window.

```python
from resq import Requests, Session

client = Requests("https://api.example.com", timeout=5)
session = Session("https://api.example.com", timeout=5)
```

---

## Sync requests

```python
r = client.get("/users/42", params={"detail": "full"})
r = client.post("/users", json={"name": "ada"})
r = client.put("/users/42", json={"name": "ada"})
r = client.delete("/users/42")
r = client.patch("/users/42", json={"role": "admin"})
r = client.head("/users/42")
r = client.options("/users/42")
```

Any keyword arguments (`params`, `headers`, `json`, `data`, `cookies`, `files`, …)
are forwarded verbatim to the underlying engine.

---

## Async requests

The `a*`-prefixed verbs run on `httpx` and must be awaited:

```python
r = await client.aget("/users/42", params={"detail": "full"})
r = await session.apost("/users", json={"name": "ada"})
```

---

## Closing the async client

Both `Requests` and `Session` hold a long-lived `httpx.AsyncClient` (created lazily on
the first `a*` call); close it when finished. `aclose` is idempotent and a no-op if no
`a*` call ever created the client:

```python
client = Session("https://api.example.com", timeout=5)  # or Requests(...)
try:
    r = await client.aget("/health")
finally:
    await client.aclose()
```

`aclose` is also invoked by `__aexit__`, so `async with` releases the client automatically.

**Sync connection pool (`Session` only).** `aclose` and `async with` release the
long-lived `httpx.AsyncClient` only. The `requests.Session` held by `Session` (its
persistent sync connection pool and cookie jar) is **not** closed explicitly — it is
released by garbage collection when the `Session` is no longer referenced. Plan for
deterministic sync-pool teardown in long-running processes if needed.

---

## Preconditions

- Paths are joined onto `base_url`; a leading `/` on the path is normalized so the
  same path string behaves identically across the sync and async engines.
- The constructor `timeout` is the network timeout; the method-level `timeout`
  (when passed) is the polling window. `delay` (default 1.0) sets the seconds
  between polling attempts and is ignored when the method `timeout` is not passed.