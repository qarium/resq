"""Contract and logic tests for resq.http.polling.

Contract tests verify the declared API (importability, sync vs coroutine, the
three-parameter signature over a pre-built wrapper, and that ``Response`` /
``AsyncResponse`` are imported into the module). Logic tests exercise the
polling loops over a pre-built wrapper against the ``reexec`` / ``arexec``
injection seams (``build_response`` / ``build_async_response``), patching only
``time.sleep`` / ``asyncio.sleep`` / ``time.monotonic`` where the deadline or
sleep behavior must be controlled.
"""

import inspect

import httpx
import pytest
import requests
from resq.http.polling import polling as polling_module
from resq.http.polling.polling import apoll, poll
from resq.http.responses.responses import AsyncResponse, Response

from tests.http.conftest import FakeUnderlying, build_async_response, build_response


def _always_after_first(value_after: float):
    """Return a monotonic fake: first call -> 0.0, every later call -> value_after."""

    state = {"first": True}

    def fake():
        if state["first"]:
            state["first"] = False
            return 0.0
        return value_after

    return fake


def _monotonic_seq(values):
    """Return a monotonic fake yielding ``values`` in order, one per call."""

    iterator = iter(values)

    def fake():
        return next(iterator)

    return fake


class TestPollingContract:
    def test_routines_are_importable(self):
        assert callable(poll)
        assert callable(apoll)

    def test_poll_is_sync_apoll_is_coroutine(self):
        assert not inspect.iscoroutinefunction(poll)
        assert inspect.iscoroutinefunction(apoll)

    def test_poll_signature_is_three_params(self):
        params = list(inspect.signature(poll).parameters)
        assert params == ["response", "timeout", "delay"]

    def test_apoll_signature_is_three_params(self):
        params = list(inspect.signature(apoll).parameters)
        assert params == ["response", "timeout", "delay"]

    def test_contract_types_imported_into_module(self):
        assert polling_module.Response is Response
        assert polling_module.AsyncResponse is AsyncResponse


class TestPoll:
    def test_timeout_none_returns_response_without_raise(self):
        # A bad status is returned as-is: no raise_for_status in the None path.
        resp, reexec = build_response([FakeUnderlying(status_code=500)])

        result = poll(resp, None, 1.0)

        assert isinstance(result, Response)
        assert result is resp
        assert resp.status_code == 500
        assert reexec.call_count == 1  # primary only; poll short-circuits

    def test_get_polls_until_success(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        resp, reexec = build_response(
            [
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )
        resp = poll(resp, 10, 0)

        assert resp.status_code == 200
        # primary (injected by build_response) + one in-place reload.
        assert reexec.call_count == 2
        assert sleeps == [0]

    def test_poll_returns_last_response_on_timeout(self, monkeypatch):
        # deadline computed from the first monotonic() (0.0 -> deadline = 10);
        # every later monotonic() returns 100 -> past deadline on first retry.
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        resp, _ = build_response([FakeUnderlying(status_code=503)])

        result = poll(resp, 10, 0)

        assert isinstance(result, Response)
        assert result.status_code == 503
        # The deadline is exceeded on the first retry, so no sleep occurs.
        assert sleeps == []

    def test_poll_timeout_last_response_is_reloadable(self, monkeypatch):
        # The response returned on exhaustion keeps its recipe, so reload retries.
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        resp, reexec = build_response(
            [
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )

        result = poll(resp, 10, 0)
        assert result.status_code == 503

        resp.reload()  # manual retry after window exhaustion

        assert resp.status_code == 200
        assert reexec.call_count == 2  # primary + manual reload
        assert sleeps == []  # the deadline is hit before any sleep

    def test_poll_propagates_transport_error(self, monkeypatch):
        # Re-scoped for Architecture A: poll meets transport only on reload.
        # Primary is a bad-status 503 (enters the retry branch); the reload
        # raises a transport error, which propagates immediately (not retried).
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        resp, reexec = build_response(
            [
                FakeUnderlying(status_code=503),
                requests.ConnectionError("boom"),
            ]
        )

        with pytest.raises(requests.ConnectionError):
            poll(resp, 10, 0)

        # Transport errors are NOT retried -> exactly one sleep before the failed reload.
        assert sleeps == [0]
        assert reexec.call_count == 2  # primary + the failed reload

    def test_poll_forwards_nonzero_delay_to_sleep(self, monkeypatch):
        # A non-zero delay must reach time.sleep verbatim — guards against a
        # regression hardcoding the sleep duration (delay=0 alone cannot tell
        # time.sleep(delay) from time.sleep(0)).
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        resp, _ = build_response(
            [
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )
        poll(resp, 10, 0.25)

        assert sleeps == [0.25]

    def test_poll_retries_more_than_once_until_success(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        resp, reexec = build_response(
            [
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )
        resp = poll(resp, 10, 0)

        assert resp.status_code == 200
        # primary + two in-place reloads, two sleeps between.
        assert reexec.call_count == 3
        assert sleeps == [0, 0]

    def test_poll_window_overshoot_bounded_by_delay_plus_request(self, monkeypatch):
        # monotonic: deadline (0.0) -> first retry check (0.5) -> second (100.0).
        # With timeout=1 and delay=10, exactly ONE reload happens before the
        # window is exceeded — the overshoot is bounded by delay + one request.
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)
        monkeypatch.setattr(polling_module.time, "monotonic", _monotonic_seq([0.0, 0.5, 100.0]))

        resp, reexec = build_response(
            [
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=503),
            ]
        )
        resp = poll(resp, timeout=1, delay=10)

        assert resp.status_code == 503
        assert reexec.call_count == 2  # primary + exactly one reload past the window
        assert sleeps == [10]


class TestApoll:
    async def test_timeout_none_returns_response_without_raise(self):
        resp, arexec = await build_async_response([FakeUnderlying(status_code=500, engine="httpx")])

        result = await apoll(resp, None, 1.0)

        assert isinstance(result, AsyncResponse)
        assert result is resp
        assert resp.status_code == 500
        assert arexec.call_count == 1

    async def test_apolls_until_success(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        resp, arexec = await build_async_response(
            [
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ]
        )
        resp = await apoll(resp, 10, 0)

        assert resp.status_code == 200
        # Confirms the async loop catches httpx.HTTPStatusError (not RequestError):
        # the 503 raises HTTPStatusError -> retry; the 200 returns.
        assert arexec.call_count == 2
        assert sleeps == [0]

    async def test_apoll_returns_last_response_on_timeout(self, monkeypatch):
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        resp, _ = await build_async_response([FakeUnderlying(status_code=503, engine="httpx")])

        result = await apoll(resp, 10, 0)

        assert isinstance(result, AsyncResponse)
        assert result.status_code == 503
        assert sleeps == []

    async def test_apoll_timeout_last_response_is_areloadable(self, monkeypatch):
        # The response returned on exhaustion keeps its recipe, so areload retries.
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))

        async def fake_sleep(d):
            pass

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        resp, arexec = await build_async_response(
            [
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ]
        )

        result = await apoll(resp, 10, 0)
        assert result.status_code == 503

        await resp.areload()  # manual retry after window exhaustion

        assert resp.status_code == 200
        assert arexec.call_count == 2  # primary + manual areload

    async def test_apoll_propagates_transport_error(self, monkeypatch):
        # Re-scoped for Architecture A: apoll meets transport only on areload.
        # Primary is a bad-status 503 (enters the retry branch); the areload
        # raises a transport error (a httpx.RequestError, sibling of
        # HTTPStatusError), which propagates immediately (not retried).
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        resp, arexec = await build_async_response(
            [
                FakeUnderlying(status_code=503, engine="httpx"),
                httpx.ConnectError("boom"),
            ]
        )

        with pytest.raises(httpx.ConnectError):
            await apoll(resp, 10, 0)

        assert sleeps == [0]
        assert arexec.call_count == 2  # primary + the failed areload

    async def test_apoll_forwards_nonzero_delay_to_sleep(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        resp, _ = await build_async_response(
            [
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ]
        )
        await apoll(resp, 10, 0.25)

        assert sleeps == [0.25]

    async def test_apoll_retries_more_than_once_until_success(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        resp, arexec = await build_async_response(
            [
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ]
        )
        resp = await apoll(resp, 10, 0)

        assert resp.status_code == 200
        assert arexec.call_count == 3
        assert sleeps == [0, 0]
