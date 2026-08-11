"""Contract and logic tests for resq.http.clients (adapter model).

Contract tests verify the declared API of the adapter-model client: the
``(base_url, adapter, timeout=None)`` constructor, the ``base_url`` /
``adapter`` / ``timeout`` properties, exactly seven unified dual-mode verbs
(``get``/``post``/``put``/``delete``/``patch``/``head``/``options`` — all plain
``def``, no ``a*`` variants), a unified ``close`` (no public ``aclose``), and
BOTH context-manager protocols (sync ``with`` + async ``async with``).

Logic tests mock only at the engine boundary and use the patch-then-construct
discipline: the engine callable is captured by the adapter ONCE at construction,
so the patch must be in place before the client is built. The sync boundary is
``resq.http.clients.clients.requests.request`` /
``resq.http.clients.clients.requests.Session``; the async boundary is
``resq.http.adapters.adapters.httpx.AsyncClient`` (the client no longer imports
``httpx``).
"""

from __future__ import annotations

import inspect
from unittest import mock
from unittest.mock import AsyncMock

import httpx
import pytest
from resq.http.clients import Requests, Session
from resq.http.clients.clients import _join_url, _normalize_path
from resq.http.polling import polling as polling_module
from resq.http.responses.responses import AsyncResponse, Response

from tests.http.conftest import FakeUnderlying

SYNC_VERBS = ["get", "post", "put", "delete", "patch", "head", "options"]
ASYNC_VERBS = ["aget", "apost", "aput", "adelete", "apatch", "ahead", "aoptions"]

BASE_URL = "https://api.example.com"


class TestClientContract:
    def test_clients_are_importable_and_constructible_with_adapter(self):
        for cls in (Requests, Session):
            for adapter in ("requests", "httpx"):
                assert cls(BASE_URL, adapter=adapter, timeout=5) is not None
                # default timeout=None flavor
                assert cls(BASE_URL, adapter) is not None

    def test_clients_expose_base_url_adapter_timeout_properties(self):
        for cls in (Requests, Session):
            for name in ("base_url", "adapter", "timeout"):
                assert isinstance(inspect.getattr_static(cls, name), property), (
                    f"{name!r} must be a property on {cls.__name__}"
                )

        for cls in (Requests, Session):
            sync_client = cls(BASE_URL, adapter="requests", timeout=5)
            assert sync_client.base_url == BASE_URL
            assert sync_client.adapter == "requests"
            assert sync_client.timeout == 5

            async_client = cls(BASE_URL, adapter="httpx")
            assert async_client.adapter == "httpx"
            assert async_client.timeout is None

    def test_unified_verbs_exist_and_are_plain_def(self):
        for cls in (Requests, Session):
            for verb in SYNC_VERBS:
                method = getattr(cls, verb)
                assert callable(method)
                assert not inspect.iscoroutinefunction(method), f"{verb} must be a plain def"

    def test_no_async_a_verbs(self):
        for cls in (Requests, Session):
            for verb in ASYNC_VERBS:
                assert not hasattr(cls, verb), f"{cls.__name__} must not expose {verb}"

    def test_close_exists_and_is_plain_def(self):
        for cls in (Requests, Session):
            assert callable(cls.close)
            assert not inspect.iscoroutinefunction(cls.close)

    def test_no_public_aclose(self):
        for cls in (Requests, Session):
            assert not hasattr(cls, "aclose")

    def test_both_context_managers_present(self):
        for cls in (Requests, Session):
            for dunder in ("__enter__", "__exit__", "__aenter__", "__aexit__"):
                assert hasattr(cls, dunder), f"{cls.__name__} missing {dunder}"
            assert inspect.iscoroutinefunction(cls.__aenter__)
            assert inspect.iscoroutinefunction(cls.__aexit__)

    def test_verb_signatures_accept_path_timeout_delay_and_arbitrary_kwargs(self):
        for cls in (Requests, Session):
            for verb in SYNC_VERBS:
                params = inspect.signature(getattr(cls, verb)).parameters
                assert "path" in params, f"{verb} missing `path`"
                assert "timeout" in params, f"{verb} missing `timeout`"
                assert "delay" in params, f"{verb} missing `delay`"
                assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()), (
                    f"{verb} must accept arbitrary keyword forwarding"
                )


class TestHelpers:
    def test_normalize_path_strips_leading_slashes(self):
        assert _normalize_path("/users/42") == "users/42"
        assert _normalize_path("users/42") == "users/42"
        assert _normalize_path("//double") == "double"

    def test_join_url_ensures_trailing_slash_on_base(self):
        assert _join_url(BASE_URL, "/health") == "https://api.example.com/health"
        assert _join_url(f"{BASE_URL}/", "/health") == "https://api.example.com/health"

    def test_join_url_parity_with_full_url(self):
        assert _join_url(f"{BASE_URL}/v1", "/users/42") == "https://api.example.com/v1/users/42"


class TestAdapterSelection:
    def test_adapter_arg_selects_requests_mode_sync(self):
        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            client = Requests(BASE_URL, adapter="requests", timeout=5)
            resp = client.get("/health")

        assert isinstance(resp, Response)
        assert resp.status_code == 200
        mock_request.assert_called_once_with("GET", "https://api.example.com/health", timeout=5)
        assert client.adapter == "requests"

    async def test_adapter_arg_selects_httpx_mode_async(self):
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))
            mock_client.aclose = AsyncMock()

            client = Requests(BASE_URL, adapter="httpx", timeout=5)
            resp = await client.get("/health")

        assert isinstance(resp, AsyncResponse)
        assert resp.status_code == 200
        assert mock_client_cls.call_count == 1  # lazily created once, then reused
        kwargs = mock_client_cls.call_args.kwargs
        assert "base_url" not in kwargs  # URL resolved by the client, not the adapter
        assert kwargs["timeout"] == httpx.Timeout(5)
        assert client.adapter == "httpx"


class TestDualModeVerbs:
    def test_unified_verbs_dual_mode_no_a_verbs(self):
        # The same verb name is dual-mode: sync returns a Response, async returns a
        # coroutine resolving to an AsyncResponse. No a* verbs exist.
        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            client = Requests(BASE_URL, adapter="requests")
            result = client.get("/health")

        assert isinstance(result, Response)
        assert not inspect.iscoroutine(result)
        assert not hasattr(client, "aget")

    async def test_async_verb_returns_coroutine_resolving_to_async_response(self):
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))
            client = Requests(BASE_URL, adapter="httpx")

            coro = client.get("/health")
            assert inspect.iscoroutine(coro)
            result = await coro

        assert isinstance(result, AsyncResponse)


class TestSyncPollThenReload:
    def test_sync_poll_then_reload_succeeds(self, monkeypatch):
        # Two timeouts: constructor network=5 (every call), method polling=10
        # (the window). reload replays in place through reexec.
        sleeps = []
        monkeypatch.setattr(polling_module.time, "sleep", sleeps.append)

        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.side_effect = [
                FakeUnderlying(status_code=503),
                FakeUnderlying(status_code=200),
            ]
            client = Requests(BASE_URL, adapter="requests", timeout=5)
            resp = client.get("/job", timeout=10, delay=0)

        assert isinstance(resp, Response)
        assert resp.status_code == 200
        # Primary request + one in-place reload across the 503 -> 200 transition.
        assert mock_request.call_count == 2
        # Every dispatch carries the constructor (network) timeout, full URL.
        for call in mock_request.call_args_list:
            assert call.args == ("GET", "https://api.example.com/job")
            assert call.kwargs == {"timeout": 5}
        assert sleeps == [0]  # exactly one sleep between the two attempts


class TestCloseAndContextManagers:
    def test_sync_context_manager_is_noop(self):
        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            with Requests(BASE_URL, adapter="requests") as client:
                assert client is not None
                resp = client.get("/health")

        assert resp.status_code == 200

    async def test_close_dual_mode_and_context_managers(self):
        # async `async with` -> aclose awaited once; double close idempotent.
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))
            mock_client.aclose = AsyncMock()

            client = Requests(BASE_URL, adapter="httpx")
            async with client as entered:
                assert entered is client  # __aenter__ returns self
                await client.get("/health")

            # __aexit__ released the long-lived AsyncClient exactly once.
            mock_client.aclose.assert_awaited_once()

            # Explicit close is idempotent: the adapter's aclose is a no-op once
            # the client is None, so aclose is still awaited exactly once total.
            await client.close()
            mock_client.aclose.assert_awaited_once()


class TestUnknownAdapter:
    def test_unknown_adapter_raises_value_error_before_building_adapter(self):
        # adapter is validated before any adapter subtype is constructed; an
        # unknown name raises ValueError for both flavors.
        for cls in (Requests, Session):
            with pytest.raises(ValueError, match="unknown adapter"):
                cls(BASE_URL, adapter="aiohttp")


class TestUrlResolvedByClient:
    def test_url_resolved_by_client_not_adapter_sync(self):
        # The client joins the path onto base_url; the adapter receives the
        # full URL. Parity with the async mode below.
        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            Requests(BASE_URL, adapter="requests", timeout=5).get("/health")

        mock_request.assert_called_once_with("GET", "https://api.example.com/health", timeout=5)

    async def test_url_resolved_by_client_not_adapter_async(self):
        # The async path resolves the full URL too; the AsyncClient is created
        # WITHOUT base_url, and request() receives the full URL.
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))

            await Requests(BASE_URL, adapter="httpx", timeout=5).get("/health")

            assert "base_url" not in mock_client_cls.call_args.kwargs
            assert mock_client.request.call_args.args == ("GET", "https://api.example.com/health")


class TestSyncClientInAsyncWith:
    async def test_sync_client_in_async_with_raises_typeerror(self):
        # One instance = one mode: a sync-mode client in `async with` exits via
        # __aexit__ -> await self.close() -> sync close() returns None ->
        # await None -> TypeError (documented misuse).
        client = Requests(BASE_URL, adapter="requests")

        with pytest.raises(TypeError):
            async with client:
                pass


class TestAsyncClientInSyncWith:
    def test_async_client_in_sync_with_raises_typeerror(self):
        # Symmetric misuse: an httpx-mode client in sync `with` exits via __exit__,
        # which cannot await the adapter's aclose coroutine. Raise TypeError
        # rather than silently dropping the coroutine (which would leak the
        # long-lived AsyncClient) — one instance = one mode.
        client = Requests(BASE_URL, adapter="httpx")

        with pytest.raises(TypeError), client:
            pass


class TestSession:
    def test_session_routes_sync_through_held_session(self):
        # The held requests.Session.request is the flavor's sync engine; the
        # module-level requests.request is never consulted.
        with (
            mock.patch("resq.http.clients.clients.requests.Session") as mock_session_cls,
            mock.patch("resq.http.clients.clients.requests.request") as mock_module_request,
        ):
            mock_session = mock_session_cls.return_value
            mock_session.request.return_value = FakeUnderlying(status_code=200)

            session = Session(BASE_URL, adapter="requests", timeout=5)
            session.get("/users/42")

        mock_session.request.assert_called_once_with(
            "GET",
            "https://api.example.com/users/42",
            timeout=5,
        )
        mock_module_request.assert_not_called()  # never the fresh-connection path

    def test_session_does_not_construct_session_in_httpx_mode(self):
        # The held requests.Session is the sync engine's pool; in async mode the
        # engine is the adapter's AsyncClient, so no Session must be built.
        with mock.patch("resq.http.clients.clients.requests.Session") as mock_session_cls:
            Session(BASE_URL, adapter="httpx", timeout=5)
        mock_session_cls.assert_not_called()

    def test_session_does_not_construct_session_before_unknown_adapter_raises(self):
        # An unknown adapter fails validation in the base; the sync-only Session
        # is never built, so no requests.Session is constructed before the
        # ValueError.
        with (
            mock.patch("resq.http.clients.clients.requests.Session") as mock_session_cls,
            pytest.raises(ValueError, match="unknown adapter"),
        ):
            Session(BASE_URL, adapter="aiohttp", timeout=5)
        mock_session_cls.assert_not_called()


class TestAllVerbsDispatch:
    """Every unified verb dispatches its HTTP method string to the engine.

    Guards against copy-paste method-string typos (e.g. ``POST`` accidentally
    dispatching ``GET``) — the six non-``get`` verbs are otherwise unexercised by
    the logic tests.
    """

    @pytest.mark.parametrize(
        ("verb", "method"),
        [
            ("get", "GET"),
            ("post", "POST"),
            ("put", "PUT"),
            ("delete", "DELETE"),
            ("patch", "PATCH"),
            ("head", "HEAD"),
            ("options", "OPTIONS"),
        ],
    )
    def test_each_sync_verb_dispatches_its_method(self, verb, method):
        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            client = Requests(BASE_URL, adapter="requests", timeout=5)
            result = getattr(client, verb)("/health")

        assert isinstance(result, Response)
        mock_request.assert_called_once_with(method, "https://api.example.com/health", timeout=5)

    @pytest.mark.parametrize(
        ("verb", "method"),
        [
            ("get", "GET"),
            ("post", "POST"),
            ("put", "PUT"),
            ("delete", "DELETE"),
            ("patch", "PATCH"),
            ("head", "HEAD"),
            ("options", "OPTIONS"),
        ],
    )
    async def test_each_async_verb_dispatches_its_method(self, verb, method):
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))
            client = Requests(BASE_URL, adapter="httpx", timeout=5)
            result = await getattr(client, verb)("/health")

        assert isinstance(result, AsyncResponse)
        assert mock_client.request.call_args.args == (method, "https://api.example.com/health")


class TestVerbKwargsForwarding:
    """Client verbs forward ``**kwargs`` verbatim to the underlying engine."""

    def test_sync_verb_forwards_kwargs_to_engine(self):
        with mock.patch("resq.http.clients.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            client = Requests(BASE_URL, adapter="requests", timeout=5)
            client.get("/search", params={"q": "1"}, headers={"X-Test": "y"})

        mock_request.assert_called_once_with(
            "GET",
            "https://api.example.com/search",
            timeout=5,
            params={"q": "1"},
            headers={"X-Test": "y"},
        )

    async def test_async_verb_forwards_kwargs_to_engine(self):
        with mock.patch("resq.http.adapters.adapters.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(return_value=FakeUnderlying(status_code=200, engine="httpx"))
            client = Requests(BASE_URL, adapter="httpx", timeout=5)
            await client.get("/search", params={"q": "1"}, headers={"X-Test": "y"})

        assert mock_client.request.call_args.args == ("GET", "https://api.example.com/search")
        assert mock_client.request.call_args.kwargs == {"params": {"q": "1"}, "headers": {"X-Test": "y"}}
