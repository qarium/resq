---
description: Reading a response through the resq wrapper — the unified attribute surface shared by Response and AsyncResponse.
---

# Reading a response

Both the sync wrapper (`Response`) and the async wrapper (`AsyncResponse`) expose the
same attribute surface for accessing the underlying response data:

```python
r = client.get("/users/42")

r.status_code       # int
r.ok                # bool — True for 2xx
r.text              # str — decoded body
r.content           # bytes — raw body
r.headers           # dict-like, case-insensitive
r.url               # final URL after redirects
r.encoding          # str | None
```

---

## JSON body

```python
data = r.json()           # parsed body (heterogeneous structure)
```

## Status check

Opt into raising on a non-2xx status (no auto-raise by default):

```python
r.raise_for_status()      # raises the engine HTTP error on a bad status — 4xx/5xx for
                          # requests, any non-2xx (incl. unfollowed 3xx) for httpx
```

This is the same hook the polling loop relies on — see [Polling](polling.md).

## Behavior & preconditions

- The body is fully buffered; `text`/`content`/`json` are safe to read more than
  once.
- Attribute names are unified across sync and async (e.g. `ok` maps to `requests`
  `ok` and `httpx` `is_success`).

!!! warning "`ok` can diverge for 3xx"

    `ok` agrees across engines for 2xx and for 4xx/5xx, but diverges for 3xx:
    `requests` counts 3xx as `ok` (`status_code < 400`), while `httpx` `is_success`
    is 2xx-only (`200…299`). Under resq redirects are followed on both engines (the
    adapter enables httpx `follow_redirects`), so a final 3xx is rare; the divergence
    matters for responses such as `304 Not Modified`.
