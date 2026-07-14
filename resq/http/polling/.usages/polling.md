# poll / apoll — driving a polling loop directly

**Domain.** Using the `poll` / `apoll` routines from the `resq.http` surface to poll an
already-built response wrapper until a success status arrives.

**Audience.** Consumers who build a response wrapper themselves (or hold one from a
single, non-polling request) and want to poll it as a separate step rather than through
a client verb's `timeout`.

`poll` (sync) and `apoll` (async) operate on an **already-built** wrapper — they never
issue the first request and never reference a client. They call `raise_for_status`, and
on a bad status retry through the wrapper's own `reload` / `areload` until success or
until the `timeout` window elapses.

```python
from resq.http import poll, apoll
```

---

## Sync poll

Build the primary response with a non-polling verb (`timeout` left at `None`), then poll
it as a separate call:

```python
from resq import Requests
from resq.http import poll

client = Requests("https://api.example.com", timeout=5)   # network timeout
r = client.get("/job/42")                                  # single request, a Response
r = poll(r, timeout=30, delay=2)                           # poll up to 30s, 2s apart
```

The same `r` object is returned; its underlying is refreshed in place on each retry.

---

## Async poll

```python
from resq import Requests
from resq.http import apoll

async with Requests("https://api.example.com", timeout=5) as client:
    r = await client.aget("/job/42")                       # single request, an AsyncResponse
    r = await apoll(r, timeout=30, delay=2)                # poll up to 30s, 2s apart
```

---

## When you do not need poll / apoll directly

Most consumers never call `poll` / `apoll` themselves: passing a `timeout` to a client
verb makes the verb poll internally and return the already-polled wrapper.

```python
r = client.get("/job/42", timeout=30, delay=2)            # the verb polls for you
```

Reach for `poll` / `apoll` directly only when the primary request and the polling loop
must be separate steps — for example, inspecting the primary response before deciding
to poll, or polling a wrapper obtained from elsewhere.

---

## Handling exhaustion

If the window elapses without a success-status response, the LAST response is returned
(its status is the final non-2xx). No exception is raised — inspect `ok` /
`status_code`, or call `reload` / `areload` to retry:

```python
r = poll(r, timeout=30, delay=2)
if not r.ok:
    ...                       # window elapsed, last status non-2xx
    r.reload()                # one more manual attempt
```

The same applies to the async path — `await r.areload()` retries.

---

## Preconditions

- `timeout` is the polling window in seconds, measured from the start of the call (not
  per attempt). `None` disables polling and returns the wrapper unchanged with no status
  check.
- `delay` (default 1.0) sets the seconds between attempts and is ignored when `timeout`
  is `None`.
- Polling retries only on a bad **status** (4xx/5xx after `raise_for_status`).
  Transport-level failures (connection, TLS, read-timeout) propagate immediately and are
  not retried.
- `poll` / `apoll` do not raise on window expiry; they return the last response.