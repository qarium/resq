"""Contract and logic tests for resq.http.adapters.

Contract tests verify the declared engine-binding API — the ``Adapter`` base and
its two mutations (``RequestsAdapter`` sync, ``HttpxAdapter`` async): class
hierarchy, mode-introspection properties, constructor signatures, and the
sync/coroutine split of the execute/lifecycle methods.

Logic tests exercise the sync execute call and the lazy long-lived
``AsyncClient`` lifecycle against the engine boundary only — the ``httpx``
engine is mocked at its import point
(``resq.http.adapters.adapters.httpx.AsyncClient``); the sync engine is a plain
``Mock``. No business-logic mocks.
"""

from __future__ import annotations

import inspect
import unittest.mock

import httpx
import pytest
import requests
from resq.http.adapters import Adapter, HttpxAdapter, RequestsAdapter

from tests.http.conftest import FakeUnderlying


class TestAdapterContract:
    def test_adapters_are_importable(self):
        assert Adapter is not None
        assert RequestsAdapter is not None
        assert HttpxAdapter is not None

    def test_subclasses_inherit_from_adapter(self):
        assert issubclass(RequestsAdapter, Adapter)
        assert issubclass(HttpxAdapter, Adapter)

    def test_requests_adapter_constructor_signature(self):
        params = [name for name in inspect.signature(RequestsAdapter.__init__).parameters if name != "self"]
        assert params == ["timeout", "sync_engine"]

    def test_httpx_adapter_constructor_signature(self):
        params = [name for name in inspect.signature(HttpxAdapter.__init__).parameters if name != "self"]
        assert params == ["timeout"]

    def test_name_and_is_async_are_properties_on_adapter(self):
        assert isinstance(inspect.getattr_static(Adapter, "name"), property)
        assert isinstance(inspect.getattr_static(Adapter, "is_async"), property)

    def test_adapter_timeout_property(self):
        assert isinstance(inspect.getattr_static(Adapter, "timeout"), property)
        assert Adapter(timeout=5).timeout == 5
        assert Adapter(timeout=None).timeout is None

    def test_requests_adapter_mode_introspection(self):
        adapter = RequestsAdapter(timeout=5, sync_engine=requests.request)

        assert adapter.name == "requests"
        assert adapter.is_async is False

    def test_httpx_adapter_mode_introspection(self):
        adapter = HttpxAdapter(timeout=5)

        assert adapter.name == "httpx"
        assert adapter.is_async is True

    def test_requests_execute_is_sync(self):
        assert callable(RequestsAdapter.execute)
        assert not inspect.iscoroutinefunction(RequestsAdapter.execute)

    def test_httpx_aexecute_and_aclose_are_coroutines(self):
        assert inspect.iscoroutinefunction(HttpxAdapter.aexecute)
        assert inspect.iscoroutinefunction(HttpxAdapter.aclose)


class TestRequestsAdapterExecute:
    def test_execute_calls_sync_engine_with_network_timeout(self):
        underlying = FakeUnderlying(status_code=200)
        engine = unittest.mock.Mock(return_value=underlying)
        adapter = RequestsAdapter(timeout=5, sync_engine=engine)

        result = adapter.execute("POST", "http://example.com/items", json={"a": 1}, headers={"H": "1"})

        assert result is underlying
        engine.assert_called_once_with(
            "POST",
            "http://example.com/items",
            timeout=5,
            json={"a": 1},
            headers={"H": "1"},
        )

    def test_execute_uses_constructor_timeout_not_per_call(self):
        engine = unittest.mock.Mock(return_value=FakeUnderlying(status_code=200))
        adapter = RequestsAdapter(timeout=7, sync_engine=engine)

        adapter.execute("GET", "http://example.com/x")

        _, kwargs = engine.call_args
        assert kwargs["timeout"] == 7

    def test_execute_passes_none_timeout_when_disabled(self):
        engine = unittest.mock.Mock(return_value=FakeUnderlying(status_code=200))
        adapter = RequestsAdapter(timeout=None, sync_engine=engine)

        adapter.execute("GET", "http://example.com/x")

        _, kwargs = engine.call_args
        assert kwargs["timeout"] is None

    def test_execute_returns_fresh_underlying_each_call(self):
        first = FakeUnderlying(status_code=200)
        second = FakeUnderlying(status_code=201)
        engine = unittest.mock.Mock(side_effect=[first, second])
        adapter = RequestsAdapter(timeout=5, sync_engine=engine)

        assert adapter.execute("GET", "http://example.com/x") is first
        assert adapter.execute("GET", "http://example.com/x") is second
        assert engine.call_count == 2

    def test_execute_propagates_request_exception(self):
        engine = unittest.mock.Mock(side_effect=requests.ConnectionError("boom"))
        adapter = RequestsAdapter(timeout=5, sync_engine=engine)

        with pytest.raises(requests.ConnectionError):
            adapter.execute("GET", "http://example.com/x")

    def test_execute_propagates_http_error(self):
        engine = unittest.mock.Mock(side_effect=requests.HTTPError("bad status"))
        adapter = RequestsAdapter(timeout=5, sync_engine=engine)

        with pytest.raises(requests.HTTPError):
            adapter.execute("GET", "http://example.com/x")


class TestHttpxAdapterAexecute:
    @unittest.mock.patch("resq.http.adapters.adapters.httpx.AsyncClient")
    async def test_aexecute_creates_async_client_lazily_once(self, mock_async_client):
        fake_client = unittest.mock.AsyncMock()
        mock_async_client.return_value = fake_client
        underlying = FakeUnderlying(status_code=200, engine="httpx")
        fake_client.request.return_value = underlying

        adapter = HttpxAdapter(timeout=5)

        first = await adapter.aexecute("GET", "http://example.com/x")
        second = await adapter.aexecute("GET", "http://example.com/y")

        assert first is underlying
        assert second is underlying
        assert mock_async_client.call_count == 1
        assert fake_client.request.call_count == 2

    @unittest.mock.patch("resq.http.adapters.adapters.httpx.AsyncClient")
    async def test_aexecute_async_client_kwargs_no_base_url_with_timeout(self, mock_async_client):
        fake_client = unittest.mock.AsyncMock()
        mock_async_client.return_value = fake_client
        fake_client.request.return_value = FakeUnderlying(status_code=200, engine="httpx")

        adapter = HttpxAdapter(timeout=5)
        await adapter.aexecute("GET", "http://example.com/x")

        _, kwargs = mock_async_client.call_args
        assert "base_url" not in kwargs
        assert kwargs["follow_redirects"] is True
        assert kwargs["timeout"] == httpx.Timeout(5)

    @unittest.mock.patch("resq.http.adapters.adapters.httpx.AsyncClient")
    async def test_aexecute_timeout_none_disables_network_timeout(self, mock_async_client):
        fake_client = unittest.mock.AsyncMock()
        mock_async_client.return_value = fake_client
        fake_client.request.return_value = FakeUnderlying(status_code=200, engine="httpx")

        adapter = HttpxAdapter(timeout=None)
        await adapter.aexecute("GET", "http://example.com/x")

        _, kwargs = mock_async_client.call_args
        assert kwargs["timeout"] is None

    @unittest.mock.patch("resq.http.adapters.adapters.httpx.AsyncClient")
    async def test_aexecute_forwards_kwargs_verbatim(self, mock_async_client):
        fake_client = unittest.mock.AsyncMock()
        mock_async_client.return_value = fake_client
        fake_client.request.return_value = FakeUnderlying(status_code=200, engine="httpx")

        adapter = HttpxAdapter(timeout=5)
        await adapter.aexecute("POST", "http://example.com/items", json={"a": 1}, headers={"H": "1"})

        fake_client.request.assert_called_once_with(
            "POST",
            "http://example.com/items",
            json={"a": 1},
            headers={"H": "1"},
        )

    @unittest.mock.patch("resq.http.adapters.adapters.httpx.AsyncClient")
    async def test_aexecute_propagates_http_status_error(self, mock_async_client):
        fake_client = unittest.mock.AsyncMock()
        mock_async_client.return_value = fake_client
        request = httpx.Request("GET", "http://example.com/x")
        response = httpx.Response(500, request=request)
        fake_client.request.side_effect = httpx.HTTPStatusError("bad status", request=request, response=response)

        adapter = HttpxAdapter(timeout=5)

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.aexecute("GET", "http://example.com/x")


class TestHttpxAdapterAclose:
    async def test_aclose_noop_before_first_async_call(self):
        adapter = HttpxAdapter(timeout=5)

        assert adapter._client is None

        await adapter.aclose()  # no-op — client never created
        await adapter.aclose()  # idempotent

        assert adapter._client is None

    @unittest.mock.patch("resq.http.adapters.adapters.httpx.AsyncClient")
    async def test_aclose_releases_long_lived_client_idempotent(self, mock_async_client):
        fake_client = unittest.mock.AsyncMock()
        mock_async_client.return_value = fake_client
        fake_client.request.return_value = FakeUnderlying(status_code=200, engine="httpx")

        adapter = HttpxAdapter(timeout=5)
        await adapter.aexecute("GET", "http://example.com/x")  # lazy create

        assert adapter._client is not None

        await adapter.aclose()

        assert adapter._client is None
        fake_client.aclose.assert_awaited_once()

        # idempotent — second close does not touch the released client again.
        await adapter.aclose()

        fake_client.aclose.assert_awaited_once()
