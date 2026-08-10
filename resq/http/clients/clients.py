"""HTTP clients for the resq core (adapter model).

Two client flavors (``Requests`` — fresh ``requests`` connection per sync call;
``Session`` — one held ``requests.Session``) mutate from a shared ``Client``
base. The mode (sync/async) and the engine are selected by the ``adapter``
constructor argument (``'requests'`` -> sync via the ``requests`` engine;
``'httpx'`` -> async via the ``httpx`` engine) and fixed on the instance — one
instance = one mode. An unknown ``adapter`` raises ``ValueError`` before any
adapter subtype is built.

DUAL-MODE DISPATCH — "a branching ``def`` returning a coroutine in the async
branch": the unified verbs (``get``/``post``/``put``/``delete``/``patch``/
``head``/``options``), ``close`` and the response ``reload`` share ONE name
across modes. Each verb is a plain ``def`` that branches on
``self._adapter.is_async``: in sync mode it returns the ``Response`` directly,
in async mode it RETURNS (does not await) the result of the async ``_averb``
helper — i.e. a coroutine the caller awaits. ``reload`` needs no dispatch:
plain method override (sync ``def`` on ``Response``, ``async def`` on
``AsyncResponse``).

Architecture A (dependency inversion): each verb resolves the URL, builds the
no-arg ``reexec`` (sync) / ``arexec`` (async coroutine function) closure from
the adapter's ``execute`` / ``aexecute`` call (baking in the constructor
network timeout), runs the PRIMARY through it, wraps the underlying in
``Response`` / ``AsyncResponse`` (injecting the closure), and — when a
method-level timeout is set — delegates the polling window to ``poll``. The
wrapper and ``poll`` hold NO back-reference to the client or the adapter.

TIMEOUT OVERLOAD: the constructor ``timeout`` is the NETWORK timeout
(connect/read, set once on the engine via the adapter). The per-verb
``timeout`` is the POLLING window — when it is ``None`` the verb issues a
single request with no status check (plain engine behavior) and ``delay`` is
ignored. Same name, different meaning by position.

This cell imports ``requests`` (the sync flavors) but NOT ``httpx`` — the
``httpx`` engine lives in :mod:`resq.http.adapters`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import requests

from ..adapters import Adapter, HttpxAdapter, RequestsAdapter
from ..polling import poll
from ..responses import AsyncResponse, Response

# The set of adapter names fixed by the contract. An unknown value raises
# ValueError before any adapter subtype is constructed.
_VALID_ADAPTERS = frozenset({"requests", "httpx"})


def _normalize_path(path: str) -> str:
    """Strip a leading slash so the same path string behaves identically across engines.

    Args:
        path: the request path as supplied to a verb.

    Returns:
        The path with any leading ``/`` removed.
    """
    return path.lstrip("/")


def _join_url(base_url: str, path: str) -> str:
    """Join a base URL and a path into the fully-qualified request URL.

    The client (not the adapter) resolves the URL for BOTH modes — the async
    adapter's ``AsyncClient`` is therefore created without ``base_url``. The
    base URL is normalized to a trailing slash and the path is stripped of its
    leading slash before ``urljoin`` so the result matches what an
    ``httpx`` ``base_url`` join would produce (engine parity).

    Args:
        base_url: the client base URL.
        path: the request path as supplied to a verb.

    Returns:
        The fully-qualified request URL.
    """
    base = base_url if base_url.endswith("/") else f"{base_url}/"
    return urljoin(base, _normalize_path(path))


class Client:
    """Shared state and behavior for the adapter-model HTTP client flavors.

    Both ``Requests`` and ``Session`` mutate from this base: they store the base
    URL, the network timeout, and the selected ``Adapter``. They differ only in
    the requests-engine callable they supply to the sync adapter
    (``_sync_engine``): ``Requests`` opens a fresh connection per call
    (module-level ``requests.request``), while ``Session`` reuses a held
    ``requests.Session`` (its bound ``request``). The unified verbs, ``close``,
    and BOTH context-manager protocols are identical and therefore live here.

    ``Client`` is the contract base of the two-flavor model — it is not part of
    the cell facade (not re-exported via ``__all__``); consumers construct
    ``Requests`` or ``Session`` directly.

    Args:
        base_url: the base URL prefixing every request path.
        adapter: the engine+mode binding — ``'requests'`` (sync) or ``'httpx'``
            (async); any other value raises ``ValueError`` before an adapter
            subtype is built. One instance = one mode.
        timeout: the NETWORK timeout (connect/read) set once on the engine via
            the adapter; ``None`` disables it (cross-engine parity).
    """

    _base_url: str
    _timeout: float | None
    _adapter: Adapter

    def __init__(self, base_url: str, adapter: str, timeout: float | None = None) -> None:
        self._base_url = base_url
        self._timeout = timeout

        if adapter not in _VALID_ADAPTERS:
            raise ValueError(
                f"unknown adapter {adapter!r}; expected one of {sorted(_VALID_ADAPTERS)!r}",
            )

        # Build the adapter subtype from the validated name. The sync engine
        # callable is captured here, once, and held by the adapter — patch the
        # engine BEFORE constructing the client (patch-then-construct).
        if adapter == "requests":
            self._adapter = RequestsAdapter(timeout, self._sync_engine())
        else:
            self._adapter = HttpxAdapter(timeout)

    def _sync_engine(self) -> Callable[..., Any]:
        """Return the requests-engine callable the sync adapter will invoke.

        Hook on the base (raises); overridden by each flavor. Only consulted
        when ``adapter == 'requests'``.
        """
        raise NotImplementedError("_sync_engine must be overridden by a client flavor")

    @property
    def base_url(self) -> str:
        """str: the base URL prefixing every request path."""
        return self._base_url

    @property
    def adapter(self) -> str:
        """str: the adapter name / mode (``'requests'`` or ``'httpx'``), fixed at construction."""
        return self._adapter.name

    @property
    def timeout(self) -> float | None:
        """float | None: the NETWORK timeout (connect/read) set on the engine."""
        return self._timeout

    def _verb(self, method: str, path: str, timeout: float | None, delay: float, kwargs: dict[str, Any]) -> Response:
        """Dispatch a sync verb (Architecture A): build ``reexec``, run the primary, return or poll.

        Resolves the full URL, builds the no-arg ``reexec`` closure replaying the
        recipe through the adapter's ``execute`` (the single source of every
        underlying), runs the PRIMARY through it, constructs the wrapper with the
        closure and injects the primary underlying, then returns the wrapper
        (``timeout is None``) or delegates to :func:`poll`.

        Args:
            method: the HTTP method to execute.
            path: the request path to execute.
            timeout: the POLLING window (``None`` = single request).
            delay: seconds between polling attempts (ignored when ``timeout is None``).
            kwargs: forwarded verbatim to the adapter's execute call.

        Returns:
            The sync ``Response`` wrapper.
        """
        url = _join_url(self._base_url, path)

        def reexec() -> Any:
            return self._adapter.execute(method, url, **kwargs)

        resp = Response(method, path, kwargs, reexec)
        resp._underlying = reexec()  # primary
        if timeout is None:
            return resp
        return poll(resp, timeout, delay)

    async def _averb(
        self,
        method: str,
        path: str,
        timeout: float | None,
        delay: float,
        kwargs: dict[str, Any],
    ) -> AsyncResponse:
        """Dispatch an async verb (Architecture A): build ``arexec``, run the primary, return or poll.

        Async mirror of :meth:`_verb` through the adapter's ``aexecute``: builds
        the no-arg ``arexec`` coroutine function, awaits the PRIMARY, constructs
        the wrapper with the closure and injects the primary underlying, then
        returns the wrapper (``timeout is None``) or delegates to :func:`poll`
        (awaiting the coroutine ``poll`` returns for an ``AsyncResponse``).

        Args:
            method: the HTTP method to execute.
            path: the request path to execute.
            timeout: the POLLING window (``None`` = single request).
            delay: seconds between polling attempts (ignored when ``timeout is None``).
            kwargs: forwarded verbatim to the adapter's aexecute call.

        Returns:
            The async ``AsyncResponse`` wrapper.
        """
        url = _join_url(self._base_url, path)

        async def arexec() -> Any:
            return await self._adapter.aexecute(method, url, **kwargs)

        resp = AsyncResponse(method, path, kwargs, arexec)
        resp._underlying = await arexec()  # primary
        if timeout is None:
            return resp
        return await poll(resp, timeout, delay)

    def get(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``GET``; single request when ``timeout`` is None, else poll. Dual-mode by adapter."""
        if self._adapter.is_async:
            return self._averb("GET", path, timeout, delay, kwargs)
        return self._verb("GET", path, timeout, delay, kwargs)

    def post(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``POST``; single request when ``timeout`` is None, else poll. Dual-mode by adapter."""
        if self._adapter.is_async:
            return self._averb("POST", path, timeout, delay, kwargs)
        return self._verb("POST", path, timeout, delay, kwargs)

    def put(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``PUT``; single request when ``timeout`` is None, else poll. Dual-mode by adapter."""
        if self._adapter.is_async:
            return self._averb("PUT", path, timeout, delay, kwargs)
        return self._verb("PUT", path, timeout, delay, kwargs)

    def delete(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``DELETE``; single request when ``timeout`` is None, else poll. Dual-mode by adapter."""
        if self._adapter.is_async:
            return self._averb("DELETE", path, timeout, delay, kwargs)
        return self._verb("DELETE", path, timeout, delay, kwargs)

    def patch(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``PATCH``; single request when ``timeout`` is None, else poll. Dual-mode by adapter."""
        if self._adapter.is_async:
            return self._averb("PATCH", path, timeout, delay, kwargs)
        return self._verb("PATCH", path, timeout, delay, kwargs)

    def head(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue a ``HEAD``; single request when ``timeout`` is None, else poll. Dual-mode by adapter."""
        if self._adapter.is_async:
            return self._averb("HEAD", path, timeout, delay, kwargs)
        return self._verb("HEAD", path, timeout, delay, kwargs)

    def options(self, path, timeout=None, delay=1.0, **kwargs):
        """Issue an ``OPTIONS``; single request when ``timeout`` is None, else poll. Dual-mode by adapter."""
        if self._adapter.is_async:
            return self._averb("OPTIONS", path, timeout, delay, kwargs)
        return self._verb("OPTIONS", path, timeout, delay, kwargs)

    def close(self):
        """Release the engine resources for the instance's mode. Dual-mode by adapter.

        Sync mode: a no-op (the ``requests.Session`` held by the ``Session``
        flavor is released by garbage collection — not closed here). Async mode:
        RETURNS (does not await) the adapter's ``aclose`` coroutine, which the
        caller awaits (also invoked by ``__aexit__``). Idempotent; safe to call
        after closing.

        Returns:
            ``None`` in sync mode; a coroutine resolving to ``None`` in async mode.
        """
        if self._adapter.is_async:
            return self._adapter.aclose()
        return None

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class Requests(Client):
    """Sync-flavor client: a fresh ``requests`` connection per sync call.

    Supplies the module-level ``requests.request`` (fresh connection per sync
    call) as the flavor's requests-engine callable to the adapter. Inherits
    every unified verb, ``close``, and both context-manager protocols from
    ``Client``.

    Args:
        base_url: the base URL prefixing every request path.
        adapter: the engine+mode binding — ``'requests'`` (sync) or ``'httpx'``
            (async).
        timeout: the NETWORK timeout (connect/read) applied via the adapter.
    """

    def __init__(self, base_url: str, adapter: str, timeout: float | None = None) -> None:
        super().__init__(base_url, adapter, timeout)

    def _sync_engine(self) -> Callable[..., Any]:
        """The module-level ``requests.request`` — a fresh connection per sync call."""
        return requests.request


class Session(Client):
    """Persistent-flavor client: one held ``requests.Session`` reused across sync calls.

    Holds one ``requests.Session`` across sync calls and supplies its bound
    ``request`` method as the flavor's requests-engine callable to the adapter.
    In async mode it behaves as ``Requests`` (the shared long-lived
    ``httpx.AsyncClient`` is owned by the adapter, not the flavor). The held
    ``requests.Session`` is created BEFORE ``super().__init__`` (so the base can
    capture the engine callable) and is NOT explicitly closed by ``close`` — it
    relies on garbage collection.

    Args:
        base_url: the base URL prefixing every request path.
        adapter: the engine+mode binding — ``'requests'`` (sync) or ``'httpx'``
            (async).
        timeout: the NETWORK timeout (connect/read) applied via the adapter.
    """

    _session: requests.Session

    def __init__(self, base_url: str, adapter: str, timeout: float | None = None) -> None:
        # Created BEFORE super().__init__ so Client.__init__ can capture
        # self._session.request via self._sync_engine().
        self._session = requests.Session()
        super().__init__(base_url, adapter, timeout)

    def _sync_engine(self) -> Callable[..., Any]:
        """The bound ``Session.request`` of the held ``requests.Session`` (persistent pool)."""
        return self._session.request
