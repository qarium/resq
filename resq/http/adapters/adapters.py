"""Engine-binding adapters for the resq HTTP core.

Defines the two-mode adapter model: a shared ``Adapter`` base owning the network
timeout and the common mode-introspection surface, and its two mutations —
``RequestsAdapter`` (sync, ``requests`` engine) and ``HttpxAdapter`` (async,
``httpx`` ``AsyncClient``).

LEAF cell: it imports nothing else from the package and references no wrapper,
client, or response type. The owning client selects and constructs the matching
subtype from the adapter string and builds BOTH the response wrapper and the
no-arg re-execute closure from this cell's ``execute``/``aexecute`` calls
(Architecture A — the wrapper holds no back-reference to the client or the
adapter).

The constructor ``timeout`` is the NETWORK timeout (connect/read), set once on
the engine; it is NOT a polling window. ``None`` disables the network timeout
(cross-engine parity: ``requests`` without a timeout and ``httpx`` without a
timeout — not the literal ``httpx`` default of ``Timeout(5.0)``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


class Adapter:
    """Shared base and contract root of the two-mode adapter model.

    Owns the network timeout and the common mode-introspection surface. The two
    modes (``RequestsAdapter``, ``HttpxAdapter``) mutate from it and differ only
    in the engine execute call and the lifecycle they own. The concrete
    ``name``/``is_async`` values are set by the subtypes; the base only stores
    the timeout.

    ``Adapter`` is NOT part of any public facade — it is public only so the
    shared ``timeout`` and mode-introspection surface are declared where the
    extractor reads them.

    Args:
        timeout: the NETWORK timeout (connect/read), set once on the engine;
            maps to the ``requests`` timeout and an ``httpx`` ``Timeout``.
            ``None`` disables the network timeout (cross-engine parity).
    """

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout
        # Mode-introspection fields are set by the concrete subtypes.

    @property
    def timeout(self) -> float | None:
        """float | None: the NETWORK timeout held for the engine calls."""
        return self._timeout

    @property
    def name(self) -> str:
        """str: the adapter name — 'requests' or 'httpx' (the bound mode/engine)."""
        return self._name

    @property
    def is_async(self) -> bool:
        """bool: True when the bound mode is async (httpx).

        The owning client reads this to choose the wrapper type (Response vs
        AsyncResponse), the context-manager protocol, and sync-vs-async dispatch.
        """
        return self._is_async


class RequestsAdapter(Adapter):
    """Sync-mode adapter — the ``requests`` engine binding.

    Executes requests through ``sync_engine`` with the network timeout; owns no
    long-lived resource. The connection policy is fully captured by
    ``sync_engine`` — fresh connection per call (module-level ``requests``) or a
    persistent pool (a bound ``requests.Session``). The engine callable is
    injected by the owning client.

    Args:
        timeout: the NETWORK timeout (connect/read), set once here; maps to the
            ``requests`` timeout.
        sync_engine: the requests-engine callable injected by the owning client
            (module-level ``requests.request`` or a bound ``Session.request``).
    """

    def __init__(self, timeout: float | None, sync_engine: Callable[..., Any]) -> None:
        super().__init__(timeout)

        self._sync_engine = sync_engine
        self._name = "requests"
        self._is_async = False

    def execute(self, method: str, url: str, **kwargs: Any) -> Any:
        """Execute one sync request through ``sync_engine`` with the network timeout.

        Args:
            method: the HTTP verb of the request.
            url: the resolved URL (the owning client joins it onto its base_url).
            **kwargs: forwarded verbatim to the ``requests`` call.

        Returns:
            A fresh underlying ``requests.Response``.

        Raises:
            requests.RequestException: transport and HTTP errors from the engine
                propagate up without interception.
        """
        return self._sync_engine(method, url, timeout=self._timeout, **kwargs)


class HttpxAdapter(Adapter):
    """Async-mode adapter — the ``httpx`` engine binding.

    Executes requests through a lazily-created, long-lived ``httpx``
    ``AsyncClient`` (shared across all calls and reloads) with the network
    timeout; owns that ``AsyncClient`` and releases it via ``aclose``. The
    ``AsyncClient`` is created WITHOUT ``base_url`` — the owning client resolves
    the full URL.

    Args:
        timeout: the NETWORK timeout (connect/read), set once here; maps to an
            ``httpx`` ``Timeout(<float>)``. ``None`` disables the network
            timeout (cross-engine parity).
    """

    def __init__(self, timeout: float | None) -> None:
        super().__init__(timeout)

        self._client: httpx.AsyncClient | None = None
        self._name = "httpx"
        self._is_async = True

    async def aexecute(self, method: str, url: str, **kwargs: Any) -> Any:
        """Execute one async request through the long-lived ``AsyncClient``.

        Creates the long-lived ``AsyncClient`` lazily on the first call (with
        ``follow_redirects=True`` and the constructor network timeout, no
        ``base_url``) and reuses it thereafter.

        Args:
            method: the HTTP verb of the request.
            url: the resolved URL (the owning client joins it onto its base_url).
            **kwargs: forwarded verbatim to the ``httpx`` call.

        Returns:
            A fresh underlying ``httpx.Response``.

        Raises:
            httpx.HTTPError: status and transport errors from the engine
                propagate up without interception.
        """
        if self._client is None:
            to = httpx.Timeout(self._timeout) if self._timeout is not None else None
            self._client = httpx.AsyncClient(follow_redirects=True, timeout=to)

        return await self._client.request(method, url, **kwargs)

    async def aclose(self) -> None:
        """Close the long-lived ``httpx`` ``AsyncClient`` and release its resources.

        Idempotent and lazy-safe: a no-op when no call has created the client
        yet, and safe to call after closing. Also invoked by the owning client's
        ``__aexit__`` (async-with).
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None
