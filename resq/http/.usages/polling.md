# Polling until success-status

**Domain.** Polling a request until a 2xx response arrives, using the method-level
`timeout`/`delay`, and handling the timeout-exhaustion error.

**Audience.** Consumers who must wait for a resource to become available (e.g. a job
that returns 202 until done).

Every request method takes two optional keyword arguments:

- `timeout` — the **polling window** in seconds (None disables polling, default).
- `delay` — seconds between attempts (default 1.0; ignored when `timeout` is None).

---

## Single request (no polling)

With `timeout=None` the method performs one request and returns the response as-is,
without auto-raising on a non-2xx status (plain `requests` behavior):

```python
r = client.get("/job/42")               # one request
if not r.ok:
    ...                                  # handle non-2xx yourself
```

---

## Polling

Pass a `timeout` to poll until success:

```python
# Sync: retry until 2xx or until 30s elapse, waiting 2s between attempts
r = client.get("/job/42", timeout=30, delay=2)

# Async
r = await client.aget("/job/42", timeout=30, delay=2)
```

The window is measured from the start of the call, not per attempt.

---

## Handling exhaustion

If the window elapses without a success-status response, the LAST response is returned
(its status is the final non-2xx). No exception is raised — inspect `ok` / `status_code`,
or call `reload` / `areload` to retry the same request:

```python
r = client.get("/job/42", timeout=30, delay=2)
if not r.ok:
    ...                       # window elapsed, last status non-2xx
    r.reload()                # one more manual attempt
```

The same applies to the async path — `await r.areload()` retries.

---

## Preconditions

- Polling retries only on a bad **status** (4xx/5xx after `raise_for_status`).
  Transport-level failures (connection, TLS, read-timeout) propagate immediately and
  are not retried.
- `delay` is ignored when `timeout` is None.