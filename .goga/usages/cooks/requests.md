# requests — synchronous HTTP

**Domain.** Usage of the `requests` library for synchronous HTTP. This is the **sync
engine of `resq`**, selected by `adapter='requests'`: the `Requests` and `Session`
flavors, their unified verbs (`get`, `post`, `put`, `patch`, `delete`, `head`, `options`)
and `Response.reload()` are built on top of `requests` in this mode.

**Audience.** Anyone implementing or consuming the sync side of `resq`.

`requests` is added to `pyproject.toml` under `[project.dependencies]`.

In the adapter model the verb names are unified across modes — there are **no** `a*`
verbs. Reload is `reload()` on both wrapper types (sync on `Response`, awaited on
`AsyncResponse`). The client constructor takes the `adapter` argument; sync mode uses the
sync `with` context manager.

---

## Two call styles

`requests` exposes HTTP verbs at two levels. The distinction maps directly to the two
`resq` sync flavors.

**Module-level verbs** — one new connection per call, no cookie/connection state carried
across calls. Under the hood each call spins up a throwaway `Session`:

```python
import requests

resp = requests.get("https://example.com/path", params={"q": "1"})
resp = requests.post("https://example.com/path", json={"a": 1})
```

**`Session`** — persistent connection pool and cookie jar shared across calls. Preferred for
repeated calls to the same host:

```python
import requests

with requests.Session() as session:
    session.headers.update({"Authorization": "Bearer ..."})
    resp = session.get("https://example.com/path")
    resp2 = session.post("https://example.com/path", json={"a": 1})
```

RULE: in `resq`, the `Requests` flavor supplies the module-level `requests.request` call
(fresh connection per sync call) as its engine callable to the adapter, and the `Session`
flavor supplies a bound `requests.Session.request` (persistent). The flavor's engine
callable is injected into the `requests`-mode adapter; the verb names are the same
unified set on both flavors.

`requests.request(method, url, **kwargs)` is the generic dispatcher behind every verb and
the entry point the adapter calls.

---

## URL construction — no native base_url

`requests` has no `base_url` concept. The caller supplies a fully-qualified URL on every
call. When wrapping a fixed origin, build the full URL with `urllib.parse.urljoin`:

```python
from urllib.parse import urljoin

url = urljoin("https://example.com/api/", "users/42")
# -> "https://example.com/api/users/42"
```

`urljoin` quirks to keep in mind:

- `urljoin("https://example.com/api/", "users")` → `.../api/users` (path appended)
- `urljoin("https://example.com/api", "users")` → `.../users` (last segment replaced)
- `urljoin("https://example.com/api", "/users")` → `https://example.com/users`
  (leading `/` resets the path)

To get predictable "append-to-base" behavior regardless of how the caller writes the path,
normalize the base to end with `/` and strip a leading `/` from the path before joining.

RULE: the owning client (not the adapter) resolves the full URL by joining the request
path onto `base_url`; the adapter receives a resolved `url` and never knows `base_url`.

---

## Request kwargs

Common keyword arguments accepted by every verb (forwarded by `resq`):

```python
requests.get(
    url,
    params={"q": "1", "page": 2},   # query string
    headers={"Accept": "application/json"},
    cookies={"session": "abc"},
    json={"a": 1},                  # JSON body, sets Content-Type automatically
    data={"a": "1"},                # form-encoded body
    files={"f": ("name.txt", b"x")},
    timeout=5.0,                    # network timeout (see below)
    allow_redirects=True,
    auth=("user", "pass"),
    verify=True,                    # TLS verification
    proxies={"https": "..."},
)
```

---

## Network timeout (the constructor-timeout of `resq`)

In `requests`, the `timeout` keyword is the **network** timeout — connect and read. It is
NOT a polling window.

```python
# float: applies to both connect and read (seconds)
requests.get(url, timeout=5.0)

# tuple: separate connect and read
requests.get(url, timeout=(3.0, 10.0))

# default None: wait indefinitely — avoid in production
```

RULE: the `resq` constructor `timeout` (the network timeout, set once on
`Requests`/`Session`) maps to this `requests` `timeout` keyword as a single float. The
adapter holds it and applies it on every execute call; per-call timeouts are NOT forwarded
at this layer.

---

## Response object

A successful verb returns `requests.Response`. Key surface used by `resq`:

```python
resp = requests.get(url)

resp.status_code        # int, e.g. 200
resp.ok                 # bool, True when status_code < 400
resp.reason             # str, e.g. "OK"
resp.headers            # CaseInsensitiveDict
resp.text               # str, decoded body
resp.content            # bytes, raw body
resp.json()             # parsed JSON body; raises requests.JSONDecodeError on bad JSON
resp.url                # final URL after redirects
resp.cookies            # CookieJar
resp.encoding           # str | None
resp.raise_for_status() # raises requests.HTTPError when status_code >= 400 (see below)
```

`resp.json()` accepts `**kwargs` forwarded to `json.loads` (e.g. `resp.json(strict=False)`).

The body is fully buffered by default; `resp.text`/`resp.content` are safe to read multiple
times.

---

## raise_for_status and the exception model

A non-2xx response is **not** raised automatically. The caller opts in via
`raise_for_status()`:

```python
resp = requests.get(url)
resp.raise_for_status()   # no-op for 2xx; raises requests.HTTPError for 4xx/5xx
```

This is the exact hook `resq`'s `timeout`/`delay` polling loop relies on: call
`raise_for_status()`, and on the raised `HTTPError` retry after `delay` seconds until success
or until the `timeout` window elapses.

Exception hierarchy (`requests.RequestException` is the base):

```python
requests.RequestException            # base for all request-related errors
├── requests.ConnectionError         # network problem (DNS, refused, reset)
│   └── requests.ConnectTimeout      # timed out during connect
├── requests.HTTPError               # from raise_for_status() on 4xx/5xx
├── requests.Timeout                 # timed out during read
│   └── requests.ReadTimeout
├── requests.TooManyRedirects
├── requests.SSLError
└── requests.JSONDecodeError         # from resp.json() on malformed body
```

To distinguish "bad status" from "transport failure" inside `resq`:

```python
import requests

try:
    resp = requests.get(url, timeout=5.0)
    resp.raise_for_status()
except requests.HTTPError:
    ...  # 4xx/5xx — retryable by the polling loop
except requests.RequestException:
    ...  # transport-level — connection, timeout, TLS, etc.
```

---

## Pattern: capturing a request recipe for reload

To support `Response.reload()` (re-execute the original request), the sync `Response` must
remember enough to replay the call. The minimal recipe is the verb name plus the forwarded
kwargs; the `requests` call is then reproducible verbatim:

```python
import requests

def execute(method, url, **kwargs):
    resp = requests.request(method, url, **kwargs)
    # store (method, url, kwargs) on the wrapper so reload() can replay them
    return resp

# reload = execute the stored (method, url, kwargs) again and overwrite the wrapped response
```

`requests.request(method, url, **kwargs)` is the generic dispatcher behind every verb — the
same one used to replay a recipe without branching on the verb name. The owning client
builds a no-arg re-exec closure from the adapter's `execute` and injects it into the
wrapper; `reload()` invokes (or, in async mode, awaits) that closure (Architecture A — the
wrapper holds no reference to the client or the adapter).
