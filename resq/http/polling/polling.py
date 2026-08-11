"""Polling routine for the resq HTTP core.

Implements the unified ``poll`` loop over an ALREADY-BUILT response wrapper:
``raise_for_status``, retry on a bad-status HTTP error after ``delay`` until
success or until the ``timeout`` window elapses — on exhaustion the LAST
(bad-status) response is returned, not raised. The client (the verb) builds the
wrapper, runs the primary, then hands it to ``poll``; ``reload`` on the wrapper
replays the recipe through the injected ``reexec`` / ``arexec`` source. ``poll``
holds NO back-reference to any client or adapter type.

``poll`` is a single plain ``def`` dispatched by the wrapper's type — the
"branching ``def`` returning a coroutine in the async branch" pattern. A
``Response`` runs the sync loop (``_poll``) and is returned directly; an
``AsyncResponse`` returns the coroutine of the async loop (``_apoll``), which the
caller awaits (the owning client's ``_averb`` does ``return await poll(...)``).

TIMEOUT OVERLOAD: the ``timeout`` parameter here is the POLLING window (deadline
measured from call start via ``time.monotonic()``), NOT the network timeout
(which is baked into the wrapper's ``reexec`` / ``arexec``). When ``timeout is
None`` the routine short-circuits, returning the wrapper unchanged with no
``raise_for_status`` (plain engine behavior) and ``delay`` is ignored.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import requests

from ..responses import AsyncResponse, Response


def poll(
    response: Response | AsyncResponse,
    timeout: float | None,
    delay: float,
) -> Response | AsyncResponse:
    """Poll a pre-built wrapper on bad status until success or window expiry.

    A single plain ``def`` dispatched by the wrapper's type. For a sync
    ``Response`` it runs the sync loop and returns the same ``Response``
    directly; for an ``AsyncResponse`` it returns the coroutine of the async
    loop, which the caller awaits.

    Args:
        response: a pre-built ``Response`` or ``AsyncResponse`` whose underlying
            is the PRIMARY engine response (the verb already ran the first
            request and injected it).
        timeout: the polling window in seconds; ``None`` disables polling (the
            wrapper is returned unchanged with no status check). ``delay`` is
            then ignored.
        delay: seconds to wait between retries (ignored when ``timeout is None``).

    Returns:
        The SAME ``response``. For a sync ``Response`` its underlying is a
        success-status engine response when the window is satisfied; if the
        ``timeout`` window elapses without a success status, the LAST
        (bad-status) response is returned instead — no exception is raised. For
        an ``AsyncResponse`` the return value is the coroutine of the async loop
        (await it to obtain the same semantics).

    Raises:
        requests.RequestException: sync transport-level failures (e.g.
            ``ConnectionError``/``Timeout``/``SSLError``) propagate immediately —
            only bad-status ``HTTPError`` triggers a retry.
        httpx.RequestError: async transport-level failures propagate
            immediately — only bad-status ``HTTPStatusError`` (a sibling of
            ``RequestError``, not a subclass) triggers a retry.
    """
    if timeout is None:
        return response

    if isinstance(response, AsyncResponse):
        return _apoll(response, timeout, delay)

    return _poll(response, timeout, delay)


def _poll(response: Response, timeout: float, delay: float) -> Response:
    """Sync polling loop — retry on a bad status via ``reload`` until success or window expiry.

    Args:
        response: a pre-built sync ``Response`` (primary already injected).
        timeout: the polling window in seconds (``None`` is handled by the caller).
        delay: seconds to wait between retries.

    Returns:
        The SAME ``response``. Its underlying is a success-status engine response
        when the window is satisfied; otherwise the LAST (bad-status) response.

    Raises:
        requests.RequestException: transport-level failures propagate
            immediately — only bad-status ``HTTPError`` triggers a retry.
    """
    deadline = time.monotonic() + timeout

    while True:
        try:
            response.raise_for_status()
            return response
        except requests.HTTPError:
            if time.monotonic() >= deadline:
                return response
            time.sleep(delay)
            response.reload()


async def _apoll(response: AsyncResponse, timeout: float, delay: float) -> AsyncResponse:
    """Async polling loop — retry on a bad status via ``await reload`` until success or window expiry.

    Args:
        response: a pre-built ``AsyncResponse`` (primary already injected).
        timeout: the polling window in seconds (``None`` is handled by the caller).
        delay: seconds to wait between retries.

    Returns:
        The SAME ``response``. Its underlying is a success-status engine response
        when the window is satisfied; otherwise the LAST (bad-status) response.

    Raises:
        httpx.RequestError: transport-level failures propagate immediately —
            only bad-status ``HTTPStatusError`` triggers a retry.
    """
    deadline = time.monotonic() + timeout

    while True:
        try:
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError:
            if time.monotonic() >= deadline:
                return response
            await asyncio.sleep(delay)
            await response.reload()
