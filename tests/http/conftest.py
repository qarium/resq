"""Shared fixtures and fakes for the resq.http cell tests.

The resq.http business logic (response proxying, in-place reload, the polling
loop) is tested mock-free against the two fakes defined here. `FakeUnderlying`
stands in for the engine response (`requests.Response` / `httpx.Response`);
`FakeOwner` stands in for the owning client (a `Requests` / `Session` exposing
`_request` / `_arequest`). Neither touches the network.
"""

import httpx
import requests


class FakeUnderlying:
    """Stand-in for a `requests.Response` or `httpx.Response`.

    Exposes the unified attribute surface the resq response wrappers proxy to
    (`status_code`, `text`, `content`, `headers`, `url`, `encoding`, `json()`) plus
    the engine-specific status accessors (`ok` for the sync engine, `is_success`
    for the async engine) and `raise_for_status()` which mimics the engine's
    4xx/5xx behavior for the polling loop.

    Args:
        status_code: HTTP status the fake response carries.
        json_return: heterogeneous value returned verbatim by `json()`.
        engine: ``"requests"`` (default) or ``"httpx"`` — selects which engine
            exception `raise_for_status()` raises on a bad status, mirroring the
            single-catch polling contract.
        attrs: optional override of the proxied surface — ``text``, ``content``,
            ``headers``, ``url``, ``encoding``, ``reason_phrase``. Anything not
            supplied defaults to an empty value.
    """

    def __init__(self, status_code=200, json_return=None, engine="requests", **attrs):
        self.status_code = status_code
        self._json_return = {} if json_return is None else json_return
        self.engine = engine

        # Engine-specific status accessors. requests exposes `ok` (< 400); httpx
        # has no `ok` and exposes `is_success` (200..299).
        self.ok = status_code < 400
        self.is_success = 200 <= status_code < 300

        self.text = attrs.get("text", "")
        self.content = attrs.get("content", b"")
        self.headers = attrs.get("headers", {})
        self.url = attrs.get("url", "")
        self.encoding = attrs.get("encoding")
        self.reason_phrase = attrs.get("reason_phrase", "")

    def json(self):
        """Return the stored heterogeneous body verbatim (no coercion)."""
        return self._json_return

    def raise_for_status(self):
        """No-op for a good status; raise the configured engine error otherwise.

        For the sync engine the raised `requests.HTTPError` carries this object
        on `.response`; for the async engine the `httpx.HTTPStatusError` does the
        same. Both let the polling tests assert ``cause.response.status_code``.
        """
        if self.status_code < 400:
            return

        if self.engine == "httpx":
            request = httpx.Request("GET", self.url or "http://test.invalid/")
            raise httpx.HTTPStatusError(
                f"fake status {self.status_code}",
                request=request,
                response=self,
            )

        error = requests.HTTPError(f"fake status {self.status_code}")
        error.response = self
        raise error


class FakeOwner:
    """Stand-in client for polling/reload tests.

    Queues engine responses and hands them out FIFO via `_request` (sync) /
    `_arequest` (async), recording each dispatched recipe. A queued item may be a
    `FakeUnderlying` (returned), an exception instance (raised — for transport
    failure tests), or a callable (called to produce the response).

    Args:
        responses: queued items handed out in order on each dispatched call.
        engine: engine label forwarded to fakes built for transport tests.
        timeout: network timeout the real clients carry (cosmetic for tests).
    """

    def __init__(self, responses=None, engine="requests", timeout=None):
        self._responses = list(responses) if responses else []
        self.engine = engine
        self.timeout = timeout
        self.calls = []

    def _next(self):
        if not self._responses:
            raise AssertionError("FakeOwner response queue exhausted")

        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item

        return item() if callable(item) else item

    def _request(self, method, path, **kwargs):
        """Synchronous dispatch — records the recipe and returns the next response."""
        self.calls.append((method, path, kwargs))

        return self._next()

    async def _arequest(self, method, path, **kwargs):
        """Asynchronous dispatch — records the recipe and returns the next response."""
        self.calls.append((method, path, kwargs))

        return self._next()
