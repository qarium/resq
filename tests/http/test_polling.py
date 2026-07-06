"""Contract and logic tests for resq.http.polling.

Contract tests verify the declared API (importability, sync vs coroutine,
six-parameter signature, and that ``Response`` /
``AsyncResponse`` are imported into the module). Logic tests exercise the
polling loops mock-free against ``FakeOwner`` / ``FakeUnderlying``, patching
only ``time.sleep`` / ``asyncio.sleep`` / ``time.monotonic`` where the deadline
or sleep behavior must be controlled.
"""

import inspect

import httpx
import pytest
import requests
from resq.http import polling as polling_module
from resq.http.polling import apoll, poll
from resq.http.responses import AsyncResponse, Response

from tests.http.conftest import FakeOwner, FakeUnderlying


def _always_after_first(value_after: float):
    """Return a monotonic fake: first call -> 0.0, every later call -> value_after."""

    state = {"first": True}

    def fake():
        if state["first"]:
            state["first"] = False
            return 0.0
        return value_after

    return fake


class TestPollingContract:
    def test_routines_are_importable(self):
        assert callable(poll)
        assert callable(apoll)

    def test_poll_is_sync_apoll_is_coroutine(self):
        assert not inspect.iscoroutinefunction(poll)
        assert inspect.iscoroutinefunction(apoll)

    def test_poll_signature_is_six_params(self):
        params = list(inspect.signature(poll).parameters)
        assert params == ["owner", "method", "path", "kwargs", "timeout", "delay"]

    def test_apoll_signature_is_six_params(self):
        params = list(inspect.signature(apoll).parameters)
        assert params == ["owner", "method", "path", "kwargs", "timeout", "delay"]

    def test_contract_types_imported_into_module(self):
        assert polling_module.Response is Response
        assert polling_module.AsyncResponse is AsyncResponse


class TestPoll:
    def test_timeout_none_returns_response_without_raise(self):
        # A bad status is returned as-is: no raise_for_status in the None path.
        owner = FakeOwner(responses=[FakeUnderlying(status_code=500)])
        resp = poll(owner, "GET", "/job/42", {}, None, 1.0)

        assert isinstance(resp, Response)
        assert resp.status_code == 500
        assert owner.calls == [("GET", "/job/42", {})]

    def test_get_polls_until_success(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        owner = FakeOwner(
            responses=[
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )
        resp = poll(owner, "GET", "/job/42", {}, 10, 0)

        assert resp.status_code == 200
        # Two dispatches (initial + one in-place reload), one sleep between them.
        assert owner.calls == [("GET", "/job/42", {}), ("GET", "/job/42", {})]
        assert sleeps == [0]

    def test_poll_returns_last_response_on_timeout(self, monkeypatch):
        # deadline computed from the first monotonic() (0.0 -> deadline = 10);
        # every later monotonic() returns 100 -> past deadline on first retry.
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        owner = FakeOwner(responses=[FakeUnderlying(status_code=503)])

        resp = poll(owner, "GET", "/job/42", {}, 10, 0)

        assert isinstance(resp, Response)
        assert resp.status_code == 503
        # The deadline is exceeded on the first retry, so no sleep occurs.
        assert sleeps == []

    def test_poll_timeout_last_response_is_reloadable(self, monkeypatch):
        # The response returned on exhaustion keeps its recipe, so reload retries.
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        owner = FakeOwner(
            responses=[
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )

        resp = poll(owner, "GET", "/job/42", {}, 10, 0)
        assert resp.status_code == 503

        resp.reload()  # manual retry after window exhaustion

        assert resp.status_code == 200
        assert owner.calls == [("GET", "/job/42", {}), ("GET", "/job/42", {})]
        assert sleeps == []  # the deadline is hit before any sleep

    def test_poll_propagates_transport_error(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        owner = FakeOwner(responses=[requests.ConnectionError("boom")])

        with pytest.raises(requests.ConnectionError):
            poll(owner, "GET", "/job/42", {}, 10, 0)

        # Transport errors are NOT retried -> time.sleep never reached.
        assert sleeps == []

    def test_poll_forwards_nonzero_delay_to_sleep(self, monkeypatch):
        # A non-zero delay must reach time.sleep verbatim — guards against a
        # regression hardcoding the sleep duration (delay=0 alone cannot tell
        # time.sleep(delay) from time.sleep(0)).
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        owner = FakeOwner(
            responses=[
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )
        poll(owner, "GET", "/job/42", {}, 10, 0.25)

        assert sleeps == [0.25]

    def test_poll_replays_nonempty_kwargs_verbatim_on_retry(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        kwargs = {"params": {"q": "1"}, "headers": {"X-Trace": "y"}}
        owner = FakeOwner(
            responses=[
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )
        poll(owner, "GET", "/job/42", kwargs, 10, 0)

        # Non-empty kwargs prove the stored recipe is replayed verbatim on each
        # reload — a regression dropping kwargs on retry would surface here.
        assert owner.calls == [("GET", "/job/42", kwargs), ("GET", "/job/42", kwargs)]

    def test_poll_retries_more_than_once_until_success(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        owner = FakeOwner(
            responses=[
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
        )
        resp = poll(owner, "GET", "/job/42", {}, 10, 0)

        assert resp.status_code == 200
        # Three dispatches (initial + two in-place reloads), two sleeps between.
        assert owner.calls == [("GET", "/job/42", {})] * 3
        assert sleeps == [0, 0]


class TestApoll:
    async def test_timeout_none_returns_response_without_raise(self):
        owner = FakeOwner(
            engine="httpx",
            responses=[FakeUnderlying(status_code=500, engine="httpx")],
        )
        resp = await apoll(owner, "GET", "/job/42", {}, None, 1.0)

        assert isinstance(resp, AsyncResponse)
        assert resp.status_code == 500
        assert owner.calls == [("GET", "/job/42", {})]

    async def test_aget_polls_until_success(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        owner = FakeOwner(
            engine="httpx",
            responses=[
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ],
        )
        resp = await apoll(owner, "GET", "/job/42", {}, 10, 0)

        assert resp.status_code == 200
        assert owner.calls == [("GET", "/job/42", {}), ("GET", "/job/42", {})]
        assert sleeps == [0]

    async def test_apoll_returns_last_response_on_timeout(self, monkeypatch):
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        owner = FakeOwner(
            engine="httpx",
            responses=[FakeUnderlying(status_code=503, engine="httpx")],
        )

        resp = await apoll(owner, "GET", "/job/42", {}, 10, 0)

        assert isinstance(resp, AsyncResponse)
        assert resp.status_code == 503
        assert sleeps == []

    async def test_apoll_timeout_last_response_is_areloadable(self, monkeypatch):
        # The response returned on exhaustion keeps its recipe, so areload retries.
        monkeypatch.setattr(polling_module.time, "monotonic", _always_after_first(100.0))

        async def fake_sleep(d):
            pass

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        owner = FakeOwner(
            engine="httpx",
            responses=[
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ],
        )

        resp = await apoll(owner, "GET", "/job/42", {}, 10, 0)
        assert resp.status_code == 503

        await resp.areload()  # manual retry after window exhaustion

        assert resp.status_code == 200
        assert owner.calls == [("GET", "/job/42", {}), ("GET", "/job/42", {})]

    async def test_apoll_propagates_httpx_transport_error(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        owner = FakeOwner(
            engine="httpx",
            responses=[httpx.ConnectError("boom")],
        )

        with pytest.raises(httpx.RequestError):
            await apoll(owner, "GET", "/job/42", {}, 10, 0)

        assert sleeps == []

    async def test_apoll_forwards_nonzero_delay_to_sleep(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        owner = FakeOwner(
            engine="httpx",
            responses=[
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ],
        )
        await apoll(owner, "GET", "/job/42", {}, 10, 0.25)

        assert sleeps == [0.25]

    async def test_apoll_replays_nonempty_kwargs_verbatim_on_retry(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        kwargs = {"params": {"q": "1"}, "headers": {"X-Trace": "y"}}
        owner = FakeOwner(
            engine="httpx",
            responses=[
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ],
        )
        await apoll(owner, "GET", "/job/42", kwargs, 10, 0)

        assert owner.calls == [("GET", "/job/42", kwargs), ("GET", "/job/42", kwargs)]

    async def test_apoll_retries_more_than_once_until_success(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        owner = FakeOwner(
            engine="httpx",
            responses=[
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=503, engine="httpx"),
                FakeUnderlying(status_code=200, engine="httpx"),
            ],
        )
        resp = await apoll(owner, "GET", "/job/42", {}, 10, 0)

        assert resp.status_code == 200
        assert owner.calls == [("GET", "/job/42", {})] * 3
        assert sleeps == [0, 0]
