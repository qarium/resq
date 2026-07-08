"""Polling routines for the resq HTTP core.

Implements the sync ``poll`` and async ``apoll`` loops over an ALREADY-BUILT
response wrapper: ``raise_for_status``, retry on a bad-status HTTP error after
``delay`` until success or until the ``timeout`` window elapses — on exhaustion
the LAST (bad-status) response is returned, not raised. The client (the verb)
builds the wrapper, runs the primary, then hands it to ``poll`` / ``apoll``;
``reload`` / ``areload`` on the wrapper replay the recipe through the injected
``reexec`` / ``arexec`` source. Neither routine holds or references any client
type.

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


def poll(response: Response, timeout: float | None, delay: float) -> Response:
    """Retry a pre-built sync ``Response`` on bad status until success or window expiry.

    Args:
        response: a pre-built ``Response`` whose underlying is the PRIMARY engine
            response (the verb already ran the first request and injected it).
        timeout: the polling window in seconds; ``None`` disables polling (the
            wrapper is returned unchanged with no status check). ``delay`` is
            then ignored.
        delay: seconds to wait between retries (ignored when ``timeout is None``).

    Returns:
        The SAME ``response``. Its underlying is a success-status engine response
        when the window is satisfied; if the ``timeout`` window elapses without a
        success status, the LAST (bad-status) response is returned instead — no
        exception is raised, so the caller may inspect ``status_code``/``ok`` or
        call ``reload`` to retry.

    Raises:
        requests.RequestException: transport-level failures (e.g.
            ``ConnectionError``/``Timeout``/``SSLError``) propagate immediately —
            only bad-status ``HTTPError`` triggers a retry.
    """
    if timeout is None:
        return response

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


async def apoll(response: AsyncResponse, timeout: float | None, delay: float) -> AsyncResponse:
    """Async counterpart of :func:`poll` over a pre-built ``AsyncResponse``.

    Args:
        response: a pre-built ``AsyncResponse`` whose underlying is the PRIMARY
            engine response (the verb already awaited the first request and
            injected it).
        timeout: the polling window in seconds; ``None`` disables polling.
        delay: seconds to wait between retries (ignored when ``timeout is None``).

    Returns:
        The SAME ``response``. Its underlying is a success-status engine response
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
        return response

    deadline = time.monotonic() + timeout

    while True:
        try:
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError:
            if time.monotonic() >= deadline:
                return response
            await asyncio.sleep(delay)
            await response.areload()
