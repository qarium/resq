# Reload — re-executing a request in place

**Domain.** Refreshing an already-held response by re-executing its original request,
without losing the object reference.

**Audience.** Consumers who keep a `Response`/`AsyncResponse` reference and need to
re-run the exact same request later.

`reload` re-executes the request the response was created from and replaces
the wrapped data **on the same object** — every reference to that object observes the
update. Both wrapper types expose the same name `reload`: sync on `Response`, async
(await it) on `AsyncResponse`.

---

## Sync reload

```python
r = client.get("/job/42")  # adapter='requests'
...
r.reload()  # same object, refreshed content
print(r.status_code)
```

---

## Async reload

```python
r = await client.get("/job/42")  # adapter='httpx'
...
await r.reload()  # same object, refreshed content
```

---

## Why in-place

Because identity is preserved, a stored reference stays valid:

```python
cache["job-42"] = client.get("/job/42")
...
cache["job-42"].reload()  # the cached entry is updated, no reassignment needed
```

---

## Preconditions

- `reload` replays through the engine and network timeout captured in its
  re-execute source; the original forwarded arguments are replayed verbatim.
- Async reload reuses the same `httpx` client that produced the original response.
- The name is `reload` on both wrappers; the async one must be awaited.
