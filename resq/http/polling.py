"""Polling routines for the resq HTTP core.

Implements the sync ``poll`` and async ``apoll`` loops: execute a request,
``raise_for_status``, retry on a bad-status HTTP error after ``delay`` until
success or until the ``timeout`` window elapses — on exhaustion the LAST
(bad-status) response is returned, not raised.

TIMEOUT OVERLOAD: the ``timeout`` parameter here is the POLLING window (deadline
measured from call start via ``time.monotonic()``), NOT the network timeout
(which lives on the owning client). When ``timeout is None`` the routines
short-circuit to a single request with no ``raise_for_status`` (plain
engine behavior) and ``delay`` is ignored.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import requests

from .responses import AsyncResponse, Response


def poll(  # noqa: PLR0913 — the six-positional signature is fixed by the CODEMANIFEST contract.
    owner: Any, method: str, path: str, kwargs: dict[str, str], timeout: float | None, delay: float
) -> Response:
    """Execute a sync request, retrying on bad status until success or window expiry.

    Args:
        owner: the owning client exposing ``_request`` (used via ``Response``).
        method: the HTTP method to execute.
        path: the request path to execute.
        kwargs: forwarded keyword arguments, replayed verbatim on each retry.
        timeout: the polling window in seconds; ``None`` disables polling (a
            single request is issued with no status check). ``delay`` is then
            ignored.
        delay: seconds to wait between retries (ignored when ``timeout is None``).

    Returns:
        A ``Response``. Its underlying is a success-status engine response when
        the window is satisfied; if the ``timeout`` window elapses without a
        success status, the LAST (bad-status) response is returned instead — no
        exception is raised, so the caller may inspect ``status_code``/``ok`` or
        call ``reload`` to retry.

    Raises:
        requests.RequestException: transport-level failures (e.g.
            ``ConnectionError``/``Timeout``/``SSLError``) propagate immediately —
            only bad-status ``HTTPError`` triggers a retry.
    """
    if timeout is None:
        return Response._from_request(owner, method, path, kwargs)

    start = time.monotonic()
    deadline = start + timeout

    resp = Response._from_request(owner, method, path, kwargs)
    while True:
        try:
            resp.raise_for_status()
            return resp
        except requests.HTTPError:
            if time.monotonic() >= deadline:
                return resp
            time.sleep(delay)
            resp.reload()


async def apoll(  # noqa: PLR0913 — the six-positional signature is fixed by the CODEMANIFEST contract.
    owner: Any, method: str, path: str, kwargs: dict[str, str], timeout: float | None, delay: float
) -> AsyncResponse:
    """Async counterpart of :func:`poll` over an ``httpx`` response.

    Args:
        owner: the owning client exposing ``_arequest`` (used via
            ``AsyncResponse``).
        method: the HTTP method to execute.
        path: the request path to execute.
        kwargs: forwarded keyword arguments, replayed verbatim on each retry.
        timeout: the polling window in seconds; ``None`` disables polling.
        delay: seconds to wait between retries (ignored when ``timeout is None``).

    Returns:
        An ``AsyncResponse``. Its underlying is a success-status engine response
        when the window is satisfied; if the ``timeout`` window elapses without
        a success status, the LAST (bad-status) response is returned instead —
        no exception is raised, so the caller may inspect ``status_code``/``ok``
        or call ``areload`` to retry.

    Raises:
        httpx.RequestError: transport-level failures propagate immediately —
            only bad-status ``HTTPStatusError`` (a sibling of ``RequestError``,
            not a subclass) triggers a retry.
    """
    if timeout is None:
        return await AsyncResponse._from_arequest(owner, method, path, kwargs)

    start = time.monotonic()
    deadline = start + timeout

    resp = await AsyncResponse._from_arequest(owner, method, path, kwargs)
    while True:
        try:
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError:
            if time.monotonic() >= deadline:
                return resp
            await asyncio.sleep(delay)
            await resp.areload()
