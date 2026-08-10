"""HTTP clients for the resq core.

Implements ``Requests`` (a fresh ``requests`` connection per sync call) and
``Session`` (one persistent ``requests.Session`` reused across sync calls), each
backed by a lazily-created, long-lived ``httpx`` ``AsyncClient`` for the async
verbs.

TIMEOUT OVERLOAD: the constructor ``timeout`` is the NETWORK timeout (connect/read,
set once on the engine). The per-verb ``timeout`` is the POLLING window — when it is
``None`` the verb issues a single request with no status check (plain engine
behavior) and ``delay`` is ignored. Same name, different meaning by position.

Dependency-inversion dispatch: each verb builds a no-arg ``reexec`` (sync) /
``arexec`` (async coroutine function) closure that replays the recipe through the
private ``_request`` / ``_arequest`` engine hooks (the closures bake in the
network timeout). The verb runs the PRIMARY through that closure, constructs the
wrapper with the closure, and injects the primary underlying post-construction;
it then returns the wrapper (``timeout is None``) or delegates to
``poll``/``apoll``. ``reload``/``areload`` on the wrapper replay the recipe
through the same closure — so neither the wrapper nor ``poll``/``apoll`` holds a
back-reference to the client.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx
import requests

from ..polling import poll
from ..responses import AsyncResponse, Response


def _normalize_path(path: str) -> str:
    """Strip a leading slash so the same path string behaves identically across engines.

    Args:
        path: the request path as supplied to a verb.

    Returns:
        The path with any leading ``/`` removed.
    """
    return path.lstrip("/")


def _join_url(base_url: str, path: str) -> str:
    """Join a base URL and a path for the sync engine.

    ``requests`` has no native ``base_url``, so the base URL is normalized to a
    trailing slash and the path is stripped of its leading slash before
    ``urljoin``. The result matches what ``httpx`` (which has a native ``base_url``)
    produces, keeping the two engines in parity.

    Args:
        base_url: the client base URL.
        path: the request path as supplied to a verb.

    Returns:
        The fully-qualified request URL.
    """
    base = base_url if base_url.endswith("/") else f"{base_url}/"
    return urljoin(base, _normalize_path(path))


class Client:
    """Shared state and behavior for the sync/async HTTP client flavors.

    Both ``Requests`` and ``Session`` mutate from this base: they store the base URL,
    the network timeout, and a lazily-created long-lived ``httpx`` ``AsyncClient``.
    They differ only in the sync dispatch (``_request``): ``Requests`` opens a fresh
    connection per call, while ``Session`` reuses a persistent ``requests.Session``.
    The async dispatch, the lazy async client, the verbs, ``aclose`` and the
    async-context-manager lifecycle are identical and therefore live here.

    ``Client`` is the contract base of the two-flavor model — it is not part of the
    cell facade (not re-exported via ``__all__``); consumers construct ``Requests`` or
    ``Session`` directly.

    Args:
        base_url: the base URL prefixing every request path.
        timeout: the NETWORK timeout (connect/read) set once on the engine; ``None``
            leaves it at the engine default.
    """

    _base_url: str
    _timeout: float | None
    _async_client: httpx.AsyncClient | None

    def __init__(self, base_url: str, timeout: float | None = None) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._async_client = None

    @property
    def base_url(self) -> str:
        """str: the base URL prefixing every request path."""
        return self._base_url

    @property
    def timeout(self) -> float | None:
        """float | None: the NETWORK timeout (connect/read) set on the engine."""
        return self._timeout

    async def _arequest(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Execute an async request against the lazily-created long-lived ``AsyncClient``.

        Args:
            method: the HTTP method to execute.
            path: the request path to execute (normalized for ``base_url`` parity).
            **kwargs: forwarded keyword arguments.

        Returns:
            The ``httpx`` response.
        """
        client = self._get_async_client()
        return await client.request(method, _normalize_path(path), **kwargs)

    def _get_async_client(self) -> httpx.AsyncClient:
        """Return the lazily-created long-lived ``AsyncClient`` (created on first use)."""
        if self._async_client is None:
            timeout = httpx.Timeout(self._timeout) if self._timeout is not None else None
            self._async_client = httpx.AsyncClient(
                base_url=self._base_url,
                follow_redirects=True,
                timeout=timeout,
            )
        return self._async_client

    def _verb(self, method, path, timeout, delay, kwargs):
        """Dispatch a sync verb (Architecture A): build ``reexec``, run the primary, return or poll.

        Builds the no-arg ``reexec`` closure replaying the recipe through the sync
        engine (the single source of every underlying), runs the PRIMARY through
        it, constructs the wrapper with the closure and injects the primary
        underlying, then returns the wrapper (``timeout is None``) or delegates to
        :func:`poll`.
        """

        def reexec():
            return self._request(method, path, **kwargs)

        resp = Response(method, path, kwargs, reexec)
        resp._underlying = reexec()  # primary
        if timeout is None:
            return resp
        return poll(resp, timeout, delay)

    async def _averb(self, method, path, timeout, delay, kwargs):
        """Dispatch an async verb (Architecture A): build ``arexec``, run the primary, return or apoll.

        Async mirror of :meth:`_verb` through the lazy ``AsyncClient``: builds the
        no-arg ``arexec`` coroutine function, awaits the PRIMARY, constructs the
        wrapper with the closure and injects the primary underlying, then returns
        the wrapper (``timeout is None``) or delegates to :func:`apoll`.
        """

        async def arexec():
            return await self._arequest(method, path, **kwargs)

        resp = AsyncResponse(method, path, kwargs, arexec)
        resp._underlying = await arexec()  # primary
        if timeout is None:
            return resp
        return await poll(resp, timeout, delay)

    def get(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``GET``; a single request when ``timeout`` is None, else poll."""
        return self._verb("GET", path, timeout, delay, kwargs)

    def post(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``POST``; a single request when ``timeout`` is None, else poll."""
        return self._verb("POST", path, timeout, delay, kwargs)

    def put(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``PUT``; a single request when ``timeout`` is None, else poll."""
        return self._verb("PUT", path, timeout, delay, kwargs)

    def delete(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``DELETE``; a single request when ``timeout`` is None, else poll."""
        return self._verb("DELETE", path, timeout, delay, kwargs)

    def patch(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``PATCH``; a single request when ``timeout`` is None, else poll."""
        return self._verb("PATCH", path, timeout, delay, kwargs)

    def head(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``HEAD``; a single request when ``timeout`` is None, else poll."""
        return self._verb("HEAD", path, timeout, delay, kwargs)

    def options(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue an ``OPTIONS``; a single request when ``timeout`` is None, else poll."""
        return self._verb("OPTIONS", path, timeout, delay, kwargs)

    async def aget(self, path, timeout=None, delay=1.0, **kwargs):
        """Await a ``GET``; a single request when ``timeout`` is None, else apoll."""
        return await self._averb("GET", path, timeout, delay, kwargs)

    async def apost(self, path, timeout=None, delay=1.0, **kwargs):
        """Await a ``POST``; a single request when ``timeout`` is None, else apoll."""
        return await self._averb("POST", path, timeout, delay, kwargs)

    async def aput(self, path, timeout=None, delay=1.0, **kwargs):
        """Await a ``PUT``; a single request when ``timeout`` is None, else apoll."""
        return await self._averb("PUT", path, timeout, delay, kwargs)

    async def adelete(self, path, timeout=None, delay=1.0, **kwargs):
        """Await a ``DELETE``; a single request when ``timeout`` is None, else apoll."""
        return await self._averb("DELETE", path, timeout, delay, kwargs)

    async def apatch(self, path, timeout=None, delay=1.0, **kwargs):
        """Await a ``PATCH``; a single request when ``timeout`` is None, else apoll."""
        return await self._averb("PATCH", path, timeout, delay, kwargs)

    async def ahead(self, path, timeout=None, delay=1.0, **kwargs):
        """Await a ``HEAD``; a single request when ``timeout`` is None, else apoll."""
        return await self._averb("HEAD", path, timeout, delay, kwargs)

    async def aoptions(self, path, timeout=None, delay=1.0, **kwargs):
        """Await an ``OPTIONS``; a single request when ``timeout`` is None, else apoll."""
        return await self._averb("OPTIONS", path, timeout, delay, kwargs)

    async def aclose(self) -> None:
        """Close the lazily-created ``AsyncClient`` if it was ever created.

        Idempotent: a no-op when no async request was ever issued (the client was
        never created). Only the async client is torn down — the held
        ``requests.Session`` (on ``Session``) is left to garbage collection per the
        contract.
        """
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class Requests(Client):
    """Sync-flavor client: a fresh ``requests`` connection per sync call.

    Each sync verb opens a brand-new connection via module-level ``requests.request``.
    The async verbs share a single lazily-created, long-lived ``httpx.AsyncClient``.

    Args:
        base_url: the base URL prefixing every request path.
        timeout: the NETWORK timeout (connect/read) applied to every sync request.
    """

    def __init__(self, base_url: str, timeout: float | None = None) -> None:
        super().__init__(base_url, timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Execute a sync request via module-level ``requests.request`` (fresh connection)."""
        url = _join_url(self._base_url, path)
        return requests.request(method, url, timeout=self._timeout, **kwargs)


class Session(Client):
    """Persistent-flavor client: one held ``requests.Session`` reused across sync calls.

    Sync verbs route through the held ``requests.Session`` (persistent pool/cookies).
    The async verbs share a single lazily-created, long-lived ``httpx.AsyncClient``.
    The held ``requests.Session`` is NOT explicitly closed by ``aclose`` — teardown is
    scoped to the async client only; the session relies on garbage collection.

    Args:
        base_url: the base URL prefixing every request path.
        timeout: the NETWORK timeout (connect/read) applied to every sync request.
    """

    def __init__(self, base_url: str, timeout: float | None = None) -> None:
        super().__init__(base_url, timeout)
        self._session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Execute a sync request via the held ``requests.Session`` (persistent pool)."""
        url = _join_url(self._base_url, path)
        return self._session.request(method, url, timeout=self._timeout, **kwargs)
