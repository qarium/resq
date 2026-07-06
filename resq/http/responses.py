"""Response wrappers for the resq HTTP core.

Defines the unified attribute-proxy contract (``BaseResponse``) and its sync
(``Response`` over ``requests``) / async (``AsyncResponse`` over ``httpx``)
subclasses with in-place ``reload`` / ``areload``.

The wrapped engine response is NOT a constructor parameter — it is injected by
the owning client after construction via the internal ``_from_request`` /
``_from_arequest`` factories. Proxying is explicit (one declared property per
attribute) — no ``__getattr__`` fallback.
"""

from __future__ import annotations

from typing import Any


class BaseResponse:
    """Common ancestor of the sync/async response wrappers.

    Stores the request recipe (``method``, ``path``, ``kwargs``) and a
    back-reference to the owning client, and exposes the underlying engine
    response through a fixed set of explicit proxy properties. The wrapped
    engine response (``_underlying``) is injected by the owning client after
    construction via ``_from_request`` / ``_from_arequest`` — it is never a
    constructor parameter.

    The ``ok`` property is engine-specific (``requests`` exposes ``ok``,
    ``httpx`` only has ``is_success``), so it is declared abstract here and
    implemented concretely by each subclass.

    Args:
        owner: the owning client (a ``Requests`` / ``Session`` exposing
            ``_request`` / ``_arequest``) used for in-place reload.
        method: the HTTP method of the original request.
        path: the request path of the original request.
        kwargs: the forwarded keyword arguments of the original request,
            replayed verbatim on reload.
    """

    def __init__(self, owner: Any, method: str, path: str, kwargs: dict[str, str]) -> None:
        self._owner = owner
        self._method = method
        self._path = path
        self._kwargs = kwargs

        # Injected by the owning client after construction; None until then.
        self._underlying = None

    @property
    def status_code(self) -> int:
        """int: the HTTP status code of the underlying response."""
        return self._underlying.status_code

    @property
    def text(self) -> str:
        """str: the decoded body of the underlying response."""
        return self._underlying.text

    @property
    def content(self) -> bytes:
        """bytes: the raw body of the underlying response."""
        return self._underlying.content

    @property
    def headers(self) -> dict[str, str]:
        """dict[str, str]: the response headers of the underlying response."""
        return self._underlying.headers

    @property
    def url(self) -> str:
        """str: the final URL of the underlying response."""
        return self._underlying.url

    @property
    def encoding(self) -> str | None:
        """str | None: the encoding of the underlying response."""
        return self._underlying.encoding

    @property
    def ok(self) -> bool:
        """bool: whether the underlying response is a success status.

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
        """Raise the engine HTTP error when the underlying status is 4xx/5xx."""
        return self._underlying.raise_for_status()


class Response(BaseResponse):
    """Sync response wrapper over a ``requests.Response``.

    Args:
        owner: the owning client exposing ``_request``.
        method: the HTTP method of the original request.
        path: the request path of the original request.
        kwargs: the forwarded keyword arguments of the original request.
    """

    @property
    def ok(self) -> bool:
        """bool: ``self._underlying.ok`` (status_code < 400)."""
        return self._underlying.ok

    def reload(self) -> None:
        """Re-execute the stored recipe through the owner in place.

        Replaces ``_underlying`` on the SAME object so every existing reference
        observes the refreshed data (identity preserved). The network timeout
        comes from the owner.
        """
        self._underlying = self._owner._request(self._method, self._path, **self._kwargs)

    @classmethod
    def _from_request(cls, owner: Any, method: str, path: str, kwargs: dict[str, str]) -> Response:
        """Construct and inject a Response for a sync request.

        Args:
            owner: the owning client exposing ``_request``.
            method: the HTTP method to execute.
            path: the request path to execute.
            kwargs: the forwarded keyword arguments to execute.

        Returns:
            A ``Response`` wrapping the freshly executed underlying response.
        """
        underlying = owner._request(method, path, **kwargs)
        resp = cls(owner, method, path, kwargs)
        resp._underlying = underlying

        return resp


class AsyncResponse(BaseResponse):
    """Async response wrapper over an ``httpx.Response``.

    Args:
        owner: the owning client exposing ``_arequest``.
        method: the HTTP method of the original request.
        path: the request path of the original request.
        kwargs: the forwarded keyword arguments of the original request.
    """

    @property
    def ok(self) -> bool:
        """bool: ``self._underlying.is_success`` (200..299)."""
        return self._underlying.is_success

    async def areload(self) -> None:
        """Re-await the stored recipe through the owner in place.

        Replaces ``_underlying`` on the SAME object so every existing reference
        observes the refreshed data (identity preserved). Reuses the owner's
        ``AsyncClient`` (it owns the pool).
        """
        self._underlying = await self._owner._arequest(self._method, self._path, **self._kwargs)

    @classmethod
    async def _from_arequest(cls, owner: Any, method: str, path: str, kwargs: dict[str, str]) -> AsyncResponse:
        """Construct and inject an AsyncResponse for an async request.

        Args:
            owner: the owning client exposing ``_arequest``.
            method: the HTTP method to execute.
            path: the request path to execute.
            kwargs: the forwarded keyword arguments to execute.

        Returns:
            An ``AsyncResponse`` wrapping the freshly awaited underlying
            response.
        """
        underlying = await owner._arequest(method, path, **kwargs)
        resp = cls(owner, method, path, kwargs)
        resp._underlying = underlying

        return resp
