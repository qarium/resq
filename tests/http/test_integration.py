"""Integration tests for the resq.http cell (adapter model).

These tests close the integration gaps left open by the per-cell unit tests:

* the **cell facade** — the six public symbols (``Requests``, ``Session``,
  ``BaseResponse``, ``Response``, ``AsyncResponse``, ``poll``) must import live
  from ``resq.http``, be listed in ``__all__``, and originate from their source
  modules;
* **cross-engine parity** — a single request through ``Requests`` (sync,
  ``adapter='requests'``) and through ``Session`` (async, ``adapter='httpx'``)
  both produce a wrapper whose proxy attributes read through to the engine
  response, with the URL resolved by the CLIENT (full URL on both modes) and the
  async ``AsyncClient`` created WITHOUT ``base_url``;
* the **full polling paths** end-to-end through a client — exercising
  ``Requests.get -> poll -> Response.reload`` (sync) and the async mirror, with
  the two timeout roles (constructor network vs. verb polling window) verified;
* **dual-mode close** + the async context manager through a real client (sync
  ``with`` is a no-op).

Engines are mocked only at the external boundary
(``resq.http.clients.clients.requests.request`` /
``resq.http.adapters.adapters.httpx.AsyncClient``); the business logic in
between runs unmocked. The patch-then-construct discipline applies: the sync
engine callable is captured by the adapter ONCE at construction, so the patch
must be in place before the client is built.
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import AsyncMock

import httpx
import resq.http as http_cell
import resq.http.clients as clients_module
import resq.http.polling.polling as polling_module
import resq.http.responses.responses as responses_module
from resq.http import AsyncResponse, BaseResponse, Requests, Response, Session, poll

from .conftest import FakeUnderlying

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
    def test_all_six_symbols_importable_from_cell(self):
        # The live facade import resolves every re-exported name, including poll.
        for name in ("Requests", "Session", "BaseResponse", "Response", "AsyncResponse", "poll"):
            assert hasattr(http_cell, name), f"resq.http missing {name}"

    def test_poll_reexported_and_apoll_gone(self):
        # poll IS re-exported from resq.http (the aggregator embedding); apoll is gone.
        assert http_cell.poll is poll
        assert not hasattr(http_cell, "apoll")

    def test_facade_all_lists_the_six_symbols(self):
        expected = {"Requests", "Session", "BaseResponse", "Response", "AsyncResponse", "poll"}
        assert set(http_cell.__all__) == expected

    def test_symbols_originate_from_their_source_modules(self):
        # Each re-exported facade name IS the object from its declared source module.
        assert Requests is clients_module.Requests
        assert Session is clients_module.Session
        assert BaseResponse is responses_module.BaseResponse
        assert Response is responses_module.Response
        assert AsyncResponse is responses_module.AsyncResponse
        assert poll is polling_module.poll


class TestCrossEngineParity:
    def test_sync_request_through_requests_proxies_engine_response(self):
        underlying = FakeUnderlying(json_return={"key": "value"}, **PROXIED)
        with mock.patch("resq.http.clients.clients.requests.request", return_value=underlying) as mock_request:
            client = Requests(BASE_URL, adapter="requests", timeout=5)
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
        # URL resolved by the client; network timeout forwarded verbatim.
        mock_request.assert_called_once_with("GET", FINAL_URL, timeout=5)

    async def test_async_request_through_session_proxies_engine_response(self):
        underlying = FakeUnderlying(engine="httpx", json_return={"key": "value"}, **PROXIED)
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=underlying)
            mock_client.aclose = AsyncMock()

            session = Session(BASE_URL, adapter="httpx", timeout=5)
            resp = await session.get("/health")

        assert isinstance(resp, AsyncResponse)
        assert resp.status_code == 200
        assert resp.text == "hello"
        assert resp.content == b"hello"
        assert resp.headers == {"X-Test": "1"}
        assert resp.url == FINAL_URL
        assert resp.encoding == "utf-8"
        assert resp.json() == {"key": "value"}
        assert resp.ok is True  # mirrors underlying.is_success (200..299)
        # URL resolved by the client (full URL); AsyncClient has no base_url.
        mock_client.request.assert_awaited_once_with("GET", FINAL_URL)
        assert "base_url" not in mock_client_cls.call_args.kwargs
        assert mock_client_cls.call_args.kwargs["timeout"] == httpx.Timeout(5)

    async def test_both_engines_yield_identical_proxy_reads(self):
        sync_underlying = FakeUnderlying(json_return={"a": 1}, **PROXIED)
        async_underlying = FakeUnderlying(engine="httpx", json_return={"a": 1}, **PROXIED)

        with mock.patch("resq.http.clients.clients.requests.request", return_value=sync_underlying):
            sync_resp = Requests(BASE_URL, adapter="requests", timeout=5).get("/health")

        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=async_underlying)
            mock_client.aclose = AsyncMock()
            async_resp = await Session(BASE_URL, adapter="httpx", timeout=5).get("/health")

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
            client = Requests(BASE_URL, adapter="requests", timeout=5)
            resp = client.get("/job", timeout=10, delay=0)

        assert isinstance(resp, Response)
        assert resp.status_code == 200
        # Initial request + one in-place reload across the 503 -> 200 transition.
        assert mock_request.call_count == 2
        # Every dispatch hits the same joined URL with the constructor (network) timeout.
        for call in mock_request.call_args_list:
            assert call.args == ("GET", f"{BASE_URL}/job")
            assert call.kwargs == {"timeout": 5}
        assert sleeps == [0]  # exactly one sleep between the two attempts


class TestAsyncPollingPath:
    async def test_client_get_polls_503_then_returns_200(self, monkeypatch):
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(polling_module.asyncio, "sleep", fake_sleep)

        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(
                side_effect=[
                    FakeUnderlying(status_code=503, engine="httpx"),
                    FakeUnderlying(status_code=200, engine="httpx"),
                ],
            )
            mock_client.aclose = AsyncMock()

            client = Session(BASE_URL, adapter="httpx", timeout=5)
            resp = await client.get("/job", timeout=10, delay=0)

            await client.close()  # release the (mocked) long-lived client

        assert isinstance(resp, AsyncResponse)
        assert resp.status_code == 200
        # Initial request + one in-place reload across the 503 -> 200 transition.
        assert mock_client.request.call_count == 2
        # URL resolved by the client (full URL); network timeout set on AsyncClient.
        for call in mock_client.request.call_args_list:
            assert call.args == ("GET", f"{BASE_URL}/job")
        assert mock_client_cls.call_args.kwargs["timeout"] == httpx.Timeout(5)
        assert "base_url" not in mock_client_cls.call_args.kwargs
        assert sleeps == [0]  # exactly one sleep between the two attempts
        mock_client.aclose.assert_awaited_once()  # released via client.close()


class TestCloseAndContextManagers:
    def test_sync_context_manager_is_noop(self):
        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            with Requests(BASE_URL, adapter="requests") as client:
                assert client is not None
                resp = client.get("/health")

        assert resp.status_code == 200
        # Sync close() is a no-op; nothing to assert beyond the request succeeding.

    async def test_async_context_manager_releases_long_lived_client(self):
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))
            mock_client.aclose = AsyncMock()

            async with Session(BASE_URL, adapter="httpx") as client:
                assert client is not None
                resp = await client.get("/health")

            assert resp.status_code == 200
            # __aexit__ -> await self.close() -> adapter.aclose -> client.aclose exactly once.
            mock_client.aclose.assert_awaited_once()

    async def test_async_close_is_idempotent(self):
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))
            mock_client.aclose = AsyncMock()

            client = Session(BASE_URL, adapter="httpx")
            await client.get("/health")
            await client.close()
            await client.close()  # second close: adapter client is None -> no-op

            # The adapter's aclose zeroes its held client, so only the first close awaits.
            mock_client.aclose.assert_awaited_once()
