# Getting started — the resq public surface

**Domain.** Consuming the `resq` package through its top-level import — construction,
requests, and polling.

**Audience.** Anyone importing `resq` for the first time.

The package exposes exactly two names from the top level, and that is the recommended
way to use it:

```python
from resq import Requests, Session
```

- `Requests` — HTTP client, sync verbs open a fresh connection per call.
- `Session` — HTTP client with a persistent sync connection reused across calls.

The response wrappers (`Response` / `AsyncResponse`) and the polling routines
(`poll` / `apoll`) are **not** re-exported here — reach them through the `resq.http`
submodule only when you need their types directly. Everything below works off the two
top-level names alone.

---

## Quick sync request

The constructor `timeout` is the **network** timeout (connect/read), set once. It is
NOT the polling window.

```python
from resq import Requests

client = Requests("https://api.example.com", timeout=5)
r = client.get("/users/42", params={"detail": "full"})

print(r.status_code, r.ok)   # int, bool
data = r.json()              # parsed body
```

Every verb forwards keyword arguments (`params`, `headers`, `json`, `data`, …) verbatim
to the underlying engine.

---

## Quick async request

The `a*` verbs run on a long-lived `httpx` client and must be awaited. Release it with
`async with` (preferred) or `aclose`:

```python
from resq import Session

async with Session("https://api.example.com", timeout=5) as client:
    r = await client.aget("/users/42")
    data = r.json()
# async with closes the async client automatically
```

`aclose` is idempotent and a no-op if no `a*` call ever ran.

---

## Polling for a success status

Pass a `timeout` **on the method** to poll until a 2xx response arrives. This is a
different `timeout` from the constructor one — same name, different meaning by position:

```python
from resq import Requests

client = Requests("https://api.example.com", timeout=5)   # network timeout
r = client.get("/job/42", timeout=30, delay=2)            # poll up to 30s, 2s apart
```

Async is identical with `aget` / `apost` / … awaited.

---

## Handling window exhaustion

If the polling window elapses without a success-status response, the LAST response is
returned (its status is the final non-2xx) — no exception is raised. Inspect `ok` /
`status_code`, or call `reload` / `areload` to retry:

```python
from resq import Requests

client = Requests("https://api.example.com", timeout=5)
r = client.get("/job/42", timeout=30, delay=2)
if not r.ok:
    ...                  # window elapsed, last status non-2xx
    r.reload()           # one more manual attempt (sync); await r.areload() async
```

---

## Requests vs Session

- `Requests` — a fresh connection per sync call (module-level `requests` behavior). Pick
  it for one-off calls or when you do not need cookie/connection persistence.
- `Session` — one persistent `requests.Session` reused across sync calls (shared pool and
  cookie jar). Pick it for repeated calls to the same host.

Both share a single lazily-created, long-lived `httpx.AsyncClient` for the `a*` verbs;
release it via `aclose` or `async with`.

---

## Preconditions

- Constructor `timeout` = network timeout (connect/read), set once; method-level
  `timeout` = polling window. Same name, different meaning by position.
- With the method `timeout` left at `None` (default), a verb issues a single request and
  does not auto-raise on a non-2xx status — inspect `r.ok` or call `r.raise_for_status()`
  yourself.
- The facade re-exports only `Requests` and `Session`; the response wrappers and the
  `poll` / `apoll` routines live in `resq.http` for advanced typing.
- With a method-level `timeout` set, a verb polls until 2xx or until the window elapses;
  on exhaustion the last (bad-status) response is returned (no exception), and `reload`
  / `areload` retries it.