# resq

Dual-mode HTTP client — one verb surface, adapter-selected engines: `'requests'` for
sync, `'httpx'` for async.

```python
from resq import Requests

with Requests("https://api.example.com", adapter="requests", timeout=5) as client:
    data = client.get("/users/42").json()
```

Async — same verbs, `adapter="httpx"`, await them:

```python
from resq import Session

async with Session("https://api.example.com", adapter="httpx", timeout=5) as client:
    data = (await client.get("/users/42")).json()
```

A method-level `timeout` turns a verb into a polling loop; `r.reload()` re-runs a
request in place.

Documentation: `docs/` (MkDocs — `mkdocs serve`).