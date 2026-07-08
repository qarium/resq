"""Integration tests for the resq.http cell.

These tests close the integration gaps left open by the unit tests:

* the **cell facade** runtime import (deferred from Task 3) — the seven public
  symbols must import live from ``resq.http`` and originate from the correct
  source modules;
* **cross-engine parity** — a single request through ``Requests`` (sync,
  ``requests`` mocked) and through ``Session.aget`` (async, ``httpx`` mocked)
  both produce a wrapper whose proxy attributes read through to the engine
  response;
* the **full polling paths** end-to-end through a client — exercising
  ``Requests.get → poll → Response._from_request → reload`` (sync) and the async
  mirror, with the engines and sleeps mocked at their boundaries.

Engines are mocked only at the external boundary (``requests.request``,
``httpx.AsyncClient``); the business logic in between runs unmocked.
"""

from unittest import mock
from unittest.mock import AsyncMock

import resq.http as http_cell
import resq.http.clients as clients_module
import resq.http.polling.polling as polling_module
import resq.http.responses.responses as responses_module
from resq.http import AsyncResponse, BaseResponse, Requests, Response, Session

from tests.http.conftest import FakeUnderlying

BASE_URL = "https://api.example.com"
FINAL_URL = "https://api.example.com/health"
PROXIED = {
    "status_code": 200,
    "text": "hello",
    "content": b"hello",
    "headers": {"X-Test": "1"},
    "url": FINAL_URL,
    "encoding": "utf-8",
}


class TestFacade:
    def test_all_five_symbols_importable_from_cell(self):
        # The live facade import resolves every re-exported name.
        for name in ("Requests", "Session", "BaseResponse", "Response", "AsyncResponse"):
            assert hasattr(http_cell, name), f"resq.http missing {name}"

    def test_polling_routines_not_reexported_from_cell(self):
        # poll/apoll stay in resq.http.polling; the http facade does not carry them.
        assert not hasattr(http_cell, "poll")
        assert not hasattr(http_cell, "apoll")

    def test_symbols_originate_from_their_source_modules(self):
        # Each re-exported facade name IS the object from its declared source module.
        assert Requests is clients_module.Requests
        assert Session is clients_module.Session
        assert BaseResponse is responses_module.BaseResponse
        assert Response is responses_module.Response
        assert AsyncResponse is responses_module.AsyncResponse

    def test_facade_all_lists_the_five_symbols(self):
        expected = {"Requests", "Session", "BaseResponse", "Response", "AsyncResponse"}
        assert set(http_cell.__all__) == expected


class TestCrossEngineParity:
    def test_sync_request_through_requests_proxies_engine_response(self):
        underlying = FakeUnderlying(json_return={"key": "value"}, **PROXIED)
        with mock.patch("resq.http.clients.clients.requests.request", return_value=underlying):
            client = Requests(BASE_URL, timeout=5)
            resp = client.get("/health")

        assert isinstance(resp, Response)
        assert resp.status_code == 200
        assert resp.text == "hello"
        assert resp.content == b"hello"
        assert resp.headers == {"X-Test": "1"}
        assert resp.url == FINAL_URL
        assert resp.encoding == "utf-8"
        assert resp.json() == {"key": "value"}
        assert resp.ok is True  # mirrors underlying.ok (status < 400)

    async def test_async_request_through_session_aget_proxies_engine_response(self):
        underlying = FakeUnderlying(engine="httpx", json_return={"key": "value"}, **PROXIED)
        with mock.patch("resq.http.clients.clients.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=underlying)

            session = Session(BASE_URL, timeout=5)
            resp = await session.aget("/health")

        assert isinstance(resp, AsyncResponse)
        assert resp.status_code == 200
        assert resp.text == "hello"
        assert resp.content == b"hello"
        assert resp.headers == {"X-Test": "1"}
        assert resp.url == FINAL_URL
        assert resp.encoding == "utf-8"
        assert resp.json() == {"key": "value"}
        assert resp.ok is True  # mirrors underlying.is_success (200..299)

    async def test_both_engines_yield_identical_proxy_reads(self):
        sync_underlying = FakeUnderlying(json_return={"a": 1}, **PROXIED)
        async_underlying = FakeUnderlying(engine="httpx", json_return={"a": 1}, **PROXIED)

        with mock.patch("resq.http.clients.clients.requests.request", return_value=sync_underlying):
            sync_resp = Requests(BASE_URL, timeout=5).get("/health")

        with mock.patch("resq.http.clients.clients.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=async_underlying)
            async_resp = await Session(BASE_URL, timeout=5).aget("/health")

        for attr in ("status_code", "text", "content", "headers", "url", "encoding"):
            assert getattr(sync_resp, attr) == getattr(async_resp, attr)
        assert sync_resp.json() == async_resp.json()


class TestSyncPollingPath:
    def test_client_get_polls_503_then_returns_200(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.side_effect = [
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
            # constructor timeout = NETWORK (5); verb timeout = POLLING window (10).
            client = Requests(BASE_URL, timeout=5)
            resp = client.get("/job", timeout=10, delay=0)

        assert isinstance(resp, Response)
        assert resp.status_code == 200
        # Initial request + one in-place reload across the 503 → 200 transition.
        assert mock_request.call_count == 2
        # Every dispatch hits the same joined URL with the network timeout.
        for call in mock_request.call_args_list:
            assert call.args == ("GET", f"{BASE_URL}/job")
            assert call.kwargs == {"timeout": 5}
        assert sleeps == [0]  # exactly one sleep between the two attempts


class TestAsyncPollingPath:
    async def test_client_aget_polls_503_then_returns_200(self, monkeypatch):
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        with mock.patch("resq.http.clients.clients.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(
                side_effect=[
                    FakeUnderlying(status_code=503, engine="httpx"),
                    FakeUnderlying(status_code=200, engine="httpx"),
                ],
            )
            mock_client.aclose = AsyncMock()

            client = Requests(BASE_URL, timeout=5)
            resp = await client.aget("/job", timeout=10, delay=0)

            await client.aclose()  # release the (mocked) long-lived client

        assert isinstance(resp, AsyncResponse)
        assert resp.status_code == 200
        # Initial request + one in-place reload.
        assert mock_client.request.call_count == 2
        for call in mock_client.request.call_args_list:
            assert call.args == ("GET", "job")  # normalized path joined by httpx base_url
        assert sleeps == [0]  # exactly one sleep between the two attempts
        mock_client.aclose.assert_awaited_once()
