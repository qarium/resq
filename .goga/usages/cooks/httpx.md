# httpx — asynchronous HTTP

**Domain.** Usage of the `httpx` library for asynchronous HTTP. This is the async engine of
the `resq` package: the `a*`-prefixed methods (`aget`, `apost`, `aput`, `apatch`, `adelete`,
`ahead`, `aoptions`) on the `Requests` and `Session` facades, and `AsyncResponse.areload()`,
are built on top of `httpx.AsyncClient`.

**Audience.** Anyone implementing or consuming the async side of `resq`.

`httpx` is added to `pyproject.toml` under `[project.dependencies]`. Verified against
`httpx` 0.28.

---

## AsyncClient lifecycle

All async calls go through an `httpx.AsyncClient`. A client owns a connection pool and must
be closed to release resources. Two equivalent patterns:

```python
import httpx

# Context manager — closes automatically (preferred for scoped use)
async with httpx.AsyncClient(base_url="https://example.com", timeout=5.0) as client:
    resp = await client.get("/path")

# Manual — required when the client outlives a single scope (e.g. a long-lived facade)
client = httpx.AsyncClient(base_url="https://example.com", timeout=5.0)
try:
    resp = await client.get("/path")
finally:
    await client.aclose()
```

RULE: in `resq`, the `Session` facade owns a long-lived `AsyncClient` and must expose a way
to close it (`aclose`); the `Requests` facade may create a short-lived client per `a*` call
or hold one lazily — either way, it must not leak an unclosed client.

`AsyncClient` constructor defaults worth knowing:

- `base_url` — `""` (no base)
- `timeout` — `httpx.Timeout(5.0)` (5 s on every phase)
- `follow_redirects` — `False` (off by default, unlike some expectations)
- `verify` — `True` (TLS verification on)

---

## base_url and path joining

Unlike `requests`, `httpx` has native `base_url` support on the client. Paths passed to
request methods are joined onto `base_url`. Verified joining behavior in 0.28:

```python
client = httpx.AsyncClient(base_url="https://example.com/api")
await client.get("users")          # -> https://example.com/api/users
await client.get("/users")         # -> https://example.com/api/users  (leading / does NOT reset)
await client.get("sub/x")          # -> https://example.com/api/sub/x
await client.get("https://other/y")# -> https://other/y               (absolute URL replaces base)
```

RULE: leading `/` on the path does **not** discard the base path in `httpx` 0.28 — relative
paths are appended to the base. Use this when relying on `base_url` directly; normalize
paths yourself if the same path string must behave identically across the sync and async
engines.

---

## Request kwargs

Common keyword arguments accepted by every method (forwarded by `resq`):

```python
await client.get(
    "/path",
    params={"q": "1", "page": 2},   # query string
    headers={"Accept": "application/json"},
    cookies={"session": "abc"},
    json={"a": 1},                  # JSON body, sets Content-Type automatically
    data={"a": "1"},                # form-encoded body
    files={"f": ("name.txt", b"x")},
    timeout=5.0,                    # network timeout (see below), overrides client default
    follow_redirects=True,
    auth=("user", "pass"),
)
```

A `timeout` passed on a single call overrides the client-level default for that call only.

---

## Network timeout (the constructor-timeout of `resq`)

In `httpx`, the `timeout` is the **network** timeout, split into four phases. It is NOT a
polling window. A bare float sets every phase; `httpx.Timeout` gives per-phase control:

```python
import httpx

# float: every phase (connect, read, write, pool)
httpx.Timeout(5.0)                       # -> connect=5, read=5, write=5, pool=5

# default + per-phase overrides
httpx.Timeout(5.0, connect=2.0)          # -> connect=2, read=5, write=5, pool=5

# all four explicit — required if no default is given
httpx.Timeout(connect=2.0, read=3.0, write=4.0, pool=5.0)
```

RULE: the `resq` constructor `timeout` (the network timeout, set once on
`Requests`/`Session`) maps to `httpx.Timeout(<float>)`. Construct the client with
`timeout=httpx.Timeout(<float>)`.

---

## Response object

An awaited method returns `httpx.Response`. Key surface used by `resq`:

```python
resp = await client.get("/path")

resp.status_code          # int, e.g. 200
resp.reason_phrase        # str, e.g. "OK"
resp.is_success           # bool, True when 200 <= status_code < 300
resp.is_redirect          # bool, 3xx
resp.is_client_error      # bool, 4xx
resp.is_server_error      # bool, 5xx
resp.headers              # httpx.Headers (case-insensitive)
resp.text                 # str, decoded body
resp.content              # bytes, raw body
resp.json()               # parsed JSON body
resp.url                  # final URL
resp.cookies              # Cookies
resp.encoding             # str | None
resp.raise_for_status()   # raises httpx.HTTPStatusError when status_code >= 400 (see below)
```

NOTE: there is **no** `resp.ok` on `httpx.Response` — use `resp.is_success` for the 2xx check.

The body is fully buffered by default; `resp.text`/`resp.content` are safe to read multiple
times. `resp.raise_for_status()` requires a bound request instance — it is only callable on
responses returned by a real client call, not on synthetic `httpx.Response(...)` objects.

---

## raise_for_status and the exception model

A non-2xx response is **not** raised automatically. The caller opts in via
`raise_for_status()`:

```python
resp = await client.get("/path")
resp.raise_for_status()   # no-op for 2xx; raises httpx.HTTPStatusError for 4xx/5xx
```

This is the exact hook `resq`'s `timeout`/`delay` polling loop relies on: await the call,
call `raise_for_status()`, and on the raised `HTTPStatusError` retry after `delay` seconds
until success or until the `timeout` window elapses.

Exception hierarchy (`httpx.HTTPError` is the root):

```python
httpx.HTTPError                       # root for all httpx errors
├── httpx.HTTPStatusError             # from raise_for_status() on 4xx/5xx (carries .response)
└── httpx.RequestError                # base for transport/protocol failures
    ├── httpx.TransportError
    │   ├── httpx.TimeoutException    # -> ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout
    │   ├── httpx.NetworkError        # -> ConnectError, ReadError, WriteError, CloseError
    │   ├── httpx.ProtocolError       # -> LocalProtocolError, RemoteProtocolError
    │   └── httpx.ProxyError
    ├── httpx.DecodingError
    └── httpx.StreamError
```

NOTE: `HTTPStatusError` is a sibling of `RequestError` under `HTTPError`, not a subclass of
`RequestError`. To distinguish "bad status" from "transport failure" inside `resq`:

```python
import httpx

try:
    resp = await client.get("/path", timeout=5.0)
    resp.raise_for_status()
except httpx.HTTPStatusError:
    ...  # 4xx/5xx — retryable by the polling loop; e.response holds the response
except httpx.RequestError:
    ...  # transport-level — connect, timeout, protocol, etc.
```

---

## Pattern: async reload and lifecycle

To support `AsyncResponse.areload()` (re-await the original request), the async response
must remember enough to replay the call against the same `AsyncClient`:

```python
import httpx

# recipe = (method, url, kwargs); replay against the same client:
resp = await client.request(method, url, **kwargs)
# areload = await client.request(stored_method, stored_url, **stored_kwargs) and overwrite
```

`client.request(method, url, **kwargs)` is the generic dispatcher behind every verb — the
one used to replay a recipe without branching on the verb name. Because the client owns the
connection pool, reload must reuse the same client instance the original call used.