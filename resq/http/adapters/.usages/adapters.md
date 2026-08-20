# Adapters — the engine-binding contract

**Domain.** The internal engine-binding that maps the `adapter` argument
(`'requests'` / `'httpx'`) to a concrete HTTP engine and its lifecycle.

**Audience.** Implementers of the owning client — the only consumers of this cell. End
users never import or construct an adapter; they pass the `adapter` string to
`Requests` / `Session`.

The adapter set is FIXED to {requests, httpx}; there is no registry and no extension
point. Two subtypes mutate from a shared `Adapter` base: `RequestsAdapter` (sync,
requests engine) and `HttpxAdapter` (async, httpx AsyncClient).

---

## Selecting an adapter

The owning client selects the subtype from the `adapter` string and constructs it with
the network timeout and, for the sync mode, the flavor's engine callable:

- `adapter='requests'` → `RequestsAdapter(timeout, sync_engine)` — `sync_engine` is
  `requests.request` for the `Requests` flavor, a bound `requests.Session.request` for
  the `Session` flavor.
- `adapter='httpx'` → `HttpxAdapter(timeout)` — both flavors share one lazily-created,
  long-lived `httpx.AsyncClient`.
- any other value → error (the client raises before constructing an adapter).

---

## Calling the engine

The client resolves the full URL (base_url + path) and passes it; the adapter does not
know `base_url`.

- Sync mode: `adapter.execute(method, url, **kwargs)` → a fresh `requests.Response`.
- Async mode: `await adapter.aexecute(method, url, **kwargs)` → a fresh `httpx.Response`.

The network timeout is the constructor timeout; per-call timeouts are not forwarded at
this layer.

---

## Lifecycle

- Sync mode owns no long-lived resource; the `requests.Session` (Session flavor) is held
  by the flavor and released by garbage collection.
- Async mode owns the long-lived `httpx.AsyncClient`; release it via
  `await adapter.aclose()` (idempotent, lazy-safe) or the owning client's `async with`.

---

## Preconditions

- The adapter never constructs a response wrapper and never references a client type —
  it provides execute + lifecycle only. The client builds the wrapper and the no-arg
  re-exec closure (Architecture A).
- `adapter.is_async` tells the client the mode (wrapper type, context-manager,
  sync-vs-async dispatch).
