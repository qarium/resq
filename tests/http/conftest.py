"""Shared fixtures and fakes for the resq.http cell tests.

The resq.http business logic (response proxying, in-place reload, the polling
loop) is tested mock-free against the fakes/seams defined here.

`FakeUnderlying` stands in for the engine response (`requests.Response` /
`httpx.Response`).

Architecture A (dependency injection): wrappers and the polling loop no longer
hold a back-reference to the client. Instead the owning client injects a no-arg
`reexec` (sync) / `arexec` (async coroutine function) callable that replays the
stored recipe. Unit tests stand in for that callable with `make_reexec` /
`make_arexec` (`unittest.mock.Mock` / `AsyncMock` with a FIFO `side_effect`
queue of `FakeUnderlying` / exception instances — this gives `call_count` and
raises queued exceptions naturally). `build_response` / `build_async_response`
construct a pre-built wrapper the way the verb does (run the primary through the
seam, then inject the underlying post-construction).
"""

import unittest.mock

import httpx
import requests
from resq.http.responses.responses import AsyncResponse, Response


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


def make_reexec(side_effect):
    """Build a sync re-exec seam: a no-arg `Mock` with a FIFO `side_effect` queue.

    Each call returns/raises the next queued item: a `FakeUnderlying` is
    returned; an exception instance is raised. Exposes `call_count`.

    Args:
        side_effect: queued items handed out in order on each call.

    Returns:
        A `unittest.mock.Mock` standing in for the injected `reexec` callable.
    """
    return unittest.mock.Mock(side_effect=list(side_effect))


def make_arexec(side_effect):
    """Build an async re-exec seam: an `AsyncMock` with a FIFO `side_effect` queue.

    Async mirror of :func:`make_reexec`: awaiting the returned coroutine
    function yields/raises the next queued item.

    Args:
        side_effect: queued items handed out in order on each awaited call.

    Returns:
        A `unittest.mock.AsyncMock` standing in for the injected `arexec`
        coroutine function.
    """
    return unittest.mock.AsyncMock(side_effect=list(side_effect))


def build_response(side_effect, *, method="GET", path="/job/42", kwargs=None):
    """Construct a pre-built sync `Response` the way the verb does.

    Builds the `reexec` seam, constructs `Response(method, path, kwargs, reexec)`,
    then runs the PRIMARY through `reexec()` (call 1) and injects the underlying
    post-construction — exactly the Architecture-A verb recipe. Subsequent
    `reload()` calls (e.g. from `poll`) advance the queue.

    Args:
        side_effect: queued items; the first is the primary underlying, later
            items feed `reload()`.
        method: the stored HTTP method.
        path: the stored request path.
        kwargs: the stored forwarded kwargs.

    Returns:
        A ``(response, reexec)`` pair (the seam exposes `call_count`).
    """
    recipe_kwargs = {} if kwargs is None else kwargs
    reexec = make_reexec(side_effect)
    resp = Response(method, path, recipe_kwargs, reexec)
    resp._underlying = reexec()  # primary — mirrors the verb
    return resp, reexec


async def build_async_response(side_effect, *, method="GET", path="/job/42", kwargs=None):
    """Construct a pre-built async `AsyncResponse` the way the verb does.

    Async mirror of :func:`build_response`: builds the `arexec` seam, constructs
    `AsyncResponse(method, path, kwargs, arexec)`, then awaits the PRIMARY
    (`await arexec()`, call 1) and injects the underlying. Subsequent `areload()`
    calls advance the queue.

    Args:
        side_effect: queued items; the first is the primary underlying, later
            items feed `areload()`.
        method: the stored HTTP method.
        path: the stored request path.
        kwargs: the stored forwarded kwargs.

    Returns:
        A ``(response, arexec)`` pair (the seam exposes `call_count`).
    """
    recipe_kwargs = {} if kwargs is None else kwargs
    arexec = make_arexec(side_effect)
    resp = AsyncResponse(method, path, recipe_kwargs, arexec)
    resp._underlying = await arexec()  # primary — mirrors the verb
    return resp, arexec
