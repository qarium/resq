---
description: poll — driving a polling loop over a response wrapper until a success status, or letting a client verb poll for you.
---

# Polling

Pass a `timeout` to a client verb and the verb polls until a success status arrives.
Consumers who want polling as a separate step use the `poll` routine from the
`resq.http` surface on an already-built response wrapper.

`poll` operates on an **already-built** wrapper — it never issues the first request and
never references a client or adapter. It calls `raise_for_status`, and on a bad status
retries through the wrapper's own `reload` until success or until the `timeout` window
elapses. The mode (sync/async) is determined by the wrapper's type: a `Response` runs a
sync loop; an `AsyncResponse` runs an async loop (await it).

```python
from resq.http import poll
```

---

## The easy way — a verb with a timeout

Most consumers never call `poll` themselves: passing a `timeout` to a client verb makes
the verb poll internally and return the already-polled wrapper.

```python
r = client.get("/job/42", timeout=30, delay=2)   # the verb polls for you
```

## Sync poll

Build the primary response with a non-polling verb (`timeout` left at `None`), then poll
it as a separate call:

```python
from resq import Requests
from resq.http import poll

client = Requests("https://api.example.com", adapter="requests", timeout=5)
r = client.get("/job/42")            # single request, a Response
r = poll(r, timeout=30, delay=2)     # poll up to 30s, 2s apart
```

The same `r` object is returned; its underlying is refreshed in place on each retry.

## Async poll

```python
from resq import Requests
from resq.http import poll

async with Requests("https://api.example.com", adapter="httpx", timeout=5) as client:
    r = await client.get("/job/42")        # single request, an AsyncResponse
    r = await poll(r, timeout=30, delay=2) # poll up to 30s, 2s apart
```

## When you do not need poll directly

Reach for `poll` directly only when the primary request and the polling loop must be
separate steps — for example, inspecting the primary response before deciding to poll,
or polling a wrapper obtained from elsewhere.

## Handling exhaustion

If the window elapses without a success-status response, the LAST response is returned
(its status is the final non-2xx). No exception is raised — inspect `ok` /
`status_code`, or call `reload` to retry:

```python
r = poll(r, timeout=30, delay=2)
if not r.ok:
    ...                        # window elapsed, last status non-2xx
    r.reload()                 # sync; await r.reload() for an AsyncResponse
```

## Preconditions

- `timeout` is the polling window in seconds, measured from the start of the call (not
  per attempt). `None` disables polling and returns the wrapper unchanged with no status
  check.
- `delay` (default 1.0) sets the seconds between attempts and is ignored when `timeout`
  is `None`.
- Polling retries only on a bad **status** (4xx/5xx after `raise_for_status`).
  Transport-level failures (connection, TLS, read-timeout) propagate immediately and are
  not retried.
- `poll` does not raise on window expiry; it returns the last response.
- The mode is fixed by the wrapper's type — a sync `Response` yields a sync loop, an
  `AsyncResponse` yields an async loop (await the call and `reload`).
