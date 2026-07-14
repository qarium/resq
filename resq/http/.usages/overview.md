# resq.http — the aggregated HTTP surface

**Domain.** Importing the full HTTP-core surface of `resq` through the
`resq.http` submodule.

**Audience.** Consumers who need a type that the top-level `resq` package does not
re-export — the response wrappers or the polling routines — or who prefer a single
import location for the whole surface.

`resq.http` is an aggregation point: it re-exports the public names of the three child
cells and adds no logic of its own.

```python
from resq.http import Requests, Session            # clients
from resq.http import Response, AsyncResponse      # response wrappers
from resq.http import BaseResponse                 # common ancestor (typing only)
from resq.http import poll, apoll                  # polling routines
```

---

## When to import from resq.http vs resq

The top-level `resq` package re-exports only the two clients:

```python
from resq import Requests, Session                 # the recommended entry point
```

Reach for `resq.http` only when you need a type beyond those two clients:

- The response wrappers — `Response`, `AsyncResponse` — to annotate a variable that
  holds a client result, or to call `reload` / `areload` against a typed reference.
- `poll` / `apoll` to drive a polling loop directly over a pre-built wrapper.
- `BaseResponse` for code that must accept both the sync and async wrapper uniformly.

Everything you can do through `resq.http`, you can also do through `resq` for the client
verbs alone — the wrappers and routines are the only names exclusive to `resq.http`.

---

## Preconditions

- `resq.http` re-exports names; it owns no behavior. Construction, request semantics,
  polling, and reload live in the child cells — see their own practices for the
  per-type contracts.
- `BaseResponse` is the common ancestor and is not meant to be constructed directly —
  you always receive a `Response` (sync) or `AsyncResponse` (async) from a client verb.