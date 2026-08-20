"""Response wrappers for the resq HTTP core.

Defines the unified attribute-proxy contract (``BaseResponse``) and its sync
(``Response`` over ``requests``) / async (``AsyncResponse`` over ``httpx``)
subclasses with in-place ``reload`` (sync ``def`` on ``Response``, awaited on
``AsyncResponse`` — one name, dispatched by plain method override).

Architecture A (dependency inversion): the wrapper stores the request recipe
(``method``, ``path``, ``kwargs``) plus a no-arg ``reexec`` callable injected by
the owning client. ``reload`` replays the recipe through that callable — the
wrapper holds NO back-reference to the client object. The wrapped engine response
(``_underlying``) is NOT a constructor parameter; the owning client runs the
primary request and injects the underlying post-construction.
Proxying is explicit (one declared property per attribute) — no ``__getattr__``
fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class BaseResponse:
    """Common ancestor of the sync/async response wrappers.

    Stores the request recipe (``method``, ``path``, ``kwargs``) and the injected
    ``reexec`` re-execute source (the dependency-inversion seam), and exposes the
    underlying engine response through a fixed set of explicit proxy properties.
    The wrapped engine response (``_underlying``) is injected by the owning
    client after construction (the client runs the primary request) — it is never
    a constructor parameter.

    ``reexec`` semantics (from contract): a no-arg callable supplied by the
    owning client; invoking it (sync) / awaiting it (async coroutine function)
    replays the stored recipe through the client's engine with the client's
    network timeout and returns a fresh underlying response. It is the single
    source of every underlying — primary and reload. The base does NOT reference
    any client type and does NOT re-execute at this level (subclasses own
    ``reload``).

    The ``ok`` property is engine-specific (``requests`` exposes ``ok``,
    ``httpx`` only has ``is_success``), so it is declared abstract here and
    implemented concretely by each subclass.

    Args:
        method: the HTTP method of the original request.
        path: the request path of the original request.
        kwargs: the forwarded keyword arguments of the original request,
            replayed verbatim on reload.
        reexec: the injected no-arg re-execute callable (sync) that replays the
            recipe through the owning client's engine.
    """

    def __init__(self, method: str, path: str, kwargs: dict[str, Any], reexec: Callable[[], Any]) -> None:
        self._method = method
        self._path = path
        self._kwargs = kwargs
        self._reexec = reexec

        # Injected by the owning client after construction; None until then.
        self._underlying = None

    @property
    def status_code(self) -> int:
        """The HTTP status code of the underlying response."""
        return self._underlying.status_code

    @property
    def text(self) -> str:
        """The decoded body of the underlying response."""
        return self._underlying.text

    @property
    def content(self) -> bytes:
        """The raw body of the underlying response."""
        return self._underlying.content

    @property
    def headers(self) -> dict[str, str]:
        """The response headers of the underlying response."""
        return self._underlying.headers

    @property
    def url(self) -> str:
        """The final URL of the underlying response."""
        return self._underlying.url

    @property
    def encoding(self) -> str | None:
        """The encoding of the underlying response."""
        return self._underlying.encoding

    @property
    def ok(self) -> bool:
        """Whether the underlying response is a success status.

        Abstract on the ancestor — the engines disagree (``requests`` exposes
        ``ok`` while ``httpx`` only has ``is_success``), so each subclass
        implements it concretely.

        Raises:
            NotImplementedError: always, when accessed on the base class.
        """
        raise NotImplementedError("ok is engine-specific; use Response or AsyncResponse")

    def json(self) -> Any:
        """Return the parsed body of the underlying response.

        Returns:
            The heterogeneous body returned verbatim by the underlying engine
            response (no coercion).
        """
        return self._underlying.json()

    def raise_for_status(self) -> None:
        """Raise the engine HTTP error when the underlying status is 4xx/5xx.

        Raises:
            requests.HTTPError: raised by the sync underlying response on a 4xx/5xx status.
            httpx.HTTPStatusError: raised by the async underlying response on a 4xx/5xx status.
        """
        return self._underlying.raise_for_status()


class Response(BaseResponse):
    """Sync response wrapper over a ``requests.Response``.

    Args:
        method: the HTTP method of the original request.
        path: the request path of the original request.
        kwargs: the forwarded keyword arguments of the original request.
        reexec: the injected no-arg re-execute callable that replays the recipe
            through the owning client's sync engine.
    """

    @property
    def ok(self) -> bool:
        """Mirror of ``self._underlying.ok`` (status_code < 400)."""
        return self._underlying.ok

    def reload(self) -> None:
        """Re-execute the stored recipe through the injected source in place.

        Replaces ``_underlying`` on the SAME object so every existing reference
        observes the refreshed data (identity preserved). The recipe replays
        through ``self._reexec()`` (the owning client's sync engine with the
        network timeout baked into the closure).
        """
        self._underlying = self._reexec()


class AsyncResponse(BaseResponse):
    """Async response wrapper over an ``httpx.Response``.

    Args:
        method: the HTTP method of the original request.
        path: the request path of the original request.
        kwargs: the forwarded keyword arguments of the original request.
        reexec: the injected no-arg coroutine function that replays the recipe
            through the owning client's long-lived ``AsyncClient``.
    """

    @property
    def ok(self) -> bool:
        """Mirror of ``self._underlying.is_success`` (200..299)."""
        return self._underlying.is_success

    async def reload(self) -> None:
        """Re-await the stored recipe through the injected source in place.

        Replaces ``_underlying`` on the SAME object so every existing reference
        observes the refreshed data (identity preserved). The recipe replays
        through ``await self._reexec()`` (the owning client's long-lived
        ``AsyncClient`` — it owns the pool).
        """
        self._underlying = await self._reexec()
