"""Contract and logic tests for resq.http.clients.

Contract tests verify the declared API (constructors, properties, the sync/async
verb surface, ``aclose`` / ``__aenter__`` / ``__aexit__``, and the internal dispatch
``_request`` / ``_arequest`` / ``_get_async_client``) and the module helpers. Logic
tests mock only at the engine boundary (``requests.request``,
``requests.Session``, ``httpx.AsyncClient``) — the business logic runs mock-free
against the boundary fakes, per the convention usage.
"""

import inspect
from unittest import mock
from unittest.mock import AsyncMock

from resq.http.clients import Requests, Session, _join_url, _normalize_path
from resq.http.responses import AsyncResponse, Response

from tests.http.conftest import FakeUnderlying

SYNC_VERBS = ["get", "post", "put", "delete", "patch", "head", "options"]
ASYNC_VERBS = ["aget", "apost", "aput", "adelete", "apatch", "ahead", "aoptions"]


class TestClientContract:
    def test_clients_are_importable_and_constructible(self):
        for cls in (Requests, Session):
            client = cls("https://api.example.com", timeout=5)
            assert client is not None
            # default timeout=None flavor
            assert cls("https://api.example.com") is not None

    def test_clients_expose_base_url_and_timeout_properties(self):
        for cls in (Requests, Session):
            assert isinstance(inspect.getattr_static(cls, "base_url"), property)
            assert isinstance(inspect.getattr_static(cls, "timeout"), property)
            client = cls("https://api.example.com", timeout=5)
            assert client.base_url == "https://api.example.com"
            assert client.timeout == 5
            assert cls("https://api.example.com").timeout is None

    def test_sync_verbs_exist_on_both_clients(self):
        for cls in (Requests, Session):
            for verb in SYNC_VERBS:
                method = getattr(cls, verb)
                assert callable(method)
                assert not inspect.iscoroutinefunction(method)

    def test_async_verbs_exist_and_are_coroutines(self):
        for cls in (Requests, Session):
            for verb in ASYNC_VERBS:
                method = getattr(cls, verb)
                assert callable(method)
                assert inspect.iscoroutinefunction(method)

    def test_async_lifecycle_methods_exist(self):
        for cls in (Requests, Session):
            assert inspect.iscoroutinefunction(cls.aclose)
            assert hasattr(cls, "__aenter__")
            assert hasattr(cls, "__aexit__")

    def test_internal_dispatch_present_on_both_clients(self):
        for cls in (Requests, Session):
            assert callable(cls._request)
            assert callable(cls._arequest)
            assert callable(cls._get_async_client)
            # _arequest and aclose are coroutine functions
            assert inspect.iscoroutinefunction(cls._arequest)

    def test_verb_signatures_accept_path_timeout_delay_and_arbitrary_kwargs(self):
        # The verbs use the ergonomic **kwargs approximation (not a named `kwargs`
        # param): assert path/timeout/delay exist plus VAR_KEYWORD forwarding.
        for cls in (Requests, Session):
            for verb in SYNC_VERBS + ASYNC_VERBS:
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
        assert _join_url("https://api.example.com", "/health") == "https://api.example.com/health"
        assert _join_url("https://api.example.com/", "/health") == "https://api.example.com/health"

    def test_join_url_parity_with_httpx_base_url(self):
        # httpx joins base_url + normalized path; the sync join must match.
        assert _join_url("https://api.example.com/v1", "/users/42") == "https://api.example.com/v1/users/42"


class TestRequests:
    def test_get_returns_response_without_raise_when_timeout_none(self):
        with mock.patch("resq.http.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=500)
            client = Requests("https://api.example.com", timeout=5)
            resp = client.get("/health")

        assert isinstance(resp, Response)
        assert resp.status_code == 500  # bad status NOT raised (single-request path)
        mock_request.assert_called_once_with("GET", "https://api.example.com/health", timeout=5)

    async def test_aget_uses_long_lived_async_client(self):
        with mock.patch("resq.http.clients.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(
                return_value=FakeUnderlying(status_code=200, engine="httpx"),
            )
            mock_client.aclose = AsyncMock()

            client = Requests("https://api.example.com")
            resp_a = await client.aget("/a")
            resp_b = await client.aget("/b")

            assert isinstance(resp_a, AsyncResponse)
            assert resp_b.status_code == 200
            assert mock_client_cls.call_count == 1  # lazily created once, then reused

            await client.aclose()
            assert client._async_client is None
            await client.aclose()  # second close is a no-op
            assert mock_client.aclose.await_count == 1

    def test_get_with_leading_slash_normalized_for_parity(self):
        client = Requests("https://api.example.com/v1", timeout=5)
        with mock.patch("resq.http.clients.requests.request") as mock_request:
            mock_request.return_value = FakeUnderlying(status_code=200)
            client.get("/users/42")

        # sync: urljoin(base + "/", "users/42") -> identical final URL
        mock_request.assert_called_once_with(
            "GET",
            "https://api.example.com/v1/users/42",
            timeout=5,
        )

    async def test_aget_with_leading_slash_normalized_for_parity(self):
        client = Requests("https://api.example.com/v1", timeout=5)
        with mock.patch("resq.http.clients.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(
                return_value=FakeUnderlying(status_code=200, engine="httpx"),
            )
            await client.aget("/users/42")

            # async: AsyncClient created with native base_url, request called with the
            # leading-slash-stripped path — httpx joins them to the same final URL.
            assert mock_client_cls.call_args.kwargs["base_url"] == "https://api.example.com/v1"
            assert mock_client.request.call_args.args == ("GET", "users/42")

    async def test_aclose_idempotent_and_noop_before_first_async_call(self):
        client = Requests("https://api.example.com", timeout=5)
        assert client._async_client is None

        await client.aclose()
        await client.aclose()  # idempotent, no error

        assert client._async_client is None

    async def test_async_context_manager_closes_client(self):
        with mock.patch("resq.http.clients.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(
                return_value=FakeUnderlying(status_code=200, engine="httpx"),
            )
            mock_client.aclose = AsyncMock()

            client = Requests("https://api.example.com")
            async with client as entered:
                assert entered is client  # __aenter__ returns self
                await client.aget("/x")

            # __aexit__ released the lazily-created AsyncClient exactly once.
            mock_client.aclose.assert_awaited_once()
            assert client._async_client is None


class TestSession:
    def test_session_routes_sync_through_held_session(self):
        with (
            mock.patch("resq.http.clients.requests.Session") as mock_session_cls,
            mock.patch("resq.http.clients.requests.request") as mock_module_request,
        ):
            mock_session = mock_session_cls.return_value
            mock_session.request.return_value = FakeUnderlying(status_code=200)

            session = Session("https://api.example.com", timeout=5)
            session.get("/users/42")

        mock_session.request.assert_called_once_with(
            "GET",
            "https://api.example.com/users/42",
            timeout=5,
        )
        mock_module_request.assert_not_called()  # never the fresh-connection path

    async def test_session_aget_uses_long_lived_client(self):
        with mock.patch("resq.http.clients.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.request = AsyncMock(
                return_value=FakeUnderlying(status_code=200, engine="httpx"),
            )

            session = Session("https://api.example.com")
            await session.aget("/a")
            await session.aget("/b")

        assert mock_client_cls.call_count == 1  # one long-lived AsyncClient reused
