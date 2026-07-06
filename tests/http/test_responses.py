"""Contract and logic tests for resq.http.responses.

Contract tests verify the declared API (class hierarchy, properties, methods,
factories, constructor signature). Logic tests exercise the proxy mapping and
in-place reload semantics mock-free against `FakeUnderlying` / `FakeOwner`.
"""

import inspect

import pytest
import requests
from resq.http.responses import AsyncResponse, BaseResponse, Response

from tests.http.conftest import FakeOwner, FakeUnderlying


class TestResponseContract:
    def test_wrappers_are_importable(self):
        assert BaseResponse is not None
        assert Response is not None
        assert AsyncResponse is not None

    def test_subclasses_inherit_from_base_response(self):
        assert issubclass(Response, BaseResponse)
        assert issubclass(AsyncResponse, BaseResponse)

    def test_base_response_declares_proxy_properties(self):
        for name in ["status_code", "ok", "text", "content", "headers", "url", "encoding"]:
            attr = inspect.getattr_static(BaseResponse, name)
            assert isinstance(attr, property), f"{name!r} must be a property on BaseResponse"

    def test_base_response_declares_common_methods(self):
        for name in ["json", "raise_for_status"]:
            assert hasattr(BaseResponse, name)
            assert callable(getattr(BaseResponse, name))

    def test_response_declares_reload_and_from_request(self):
        assert callable(Response.reload)
        assert isinstance(inspect.getattr_static(Response, "_from_request"), classmethod)

    def test_async_response_declares_areload_and_from_arequest(self):
        assert inspect.iscoroutinefunction(AsyncResponse.areload)
        descriptor = inspect.getattr_static(AsyncResponse, "_from_arequest")
        assert isinstance(descriptor, classmethod)
        assert inspect.iscoroutinefunction(descriptor.__func__)

    def test_base_response_constructor_signature_is_four_params(self):
        params = [name for name in inspect.signature(BaseResponse.__init__).parameters if name != "self"]
        assert params == ["owner", "method", "path", "kwargs"]


class TestResponseProxy:
    """Proxy properties forward to the underlying engine response verbatim."""

    def _wrap(self, status_code=200, **attrs):
        underlying = FakeUnderlying(status_code=status_code, **attrs)
        owner = FakeOwner(responses=[underlying])
        return Response._from_request(owner, "GET", "/x", {})

    def test_status_code_forwarded(self):
        assert self._wrap(status_code=202).status_code == 202

    def test_text_forwarded(self):
        assert self._wrap(text="hello").text == "hello"

    def test_content_forwarded(self):
        assert self._wrap(content=b"raw").content == b"raw"

    def test_headers_forwarded(self):
        headers = {"X-Test": "1"}
        assert self._wrap(headers=headers).headers is headers

    def test_url_forwarded(self):
        assert self._wrap(url="https://example.com/x").url == "https://example.com/x"

    def test_encoding_forwarded(self):
        assert self._wrap(encoding="utf-8").encoding == "utf-8"

    def test_encoding_defaults_to_none(self):
        assert self._wrap().encoding is None


class TestResponseBody:
    def test_json_returns_underlying_body_unchanged(self):
        body = {"a": 1, "b": [2, 3]}
        owner = FakeOwner(responses=[FakeUnderlying(status_code=200, json_return=body)])
        resp = Response._from_request(owner, "GET", "/data", {})

        assert resp.json() == {"a": 1, "b": [2, 3]}

    def test_raise_for_status_delegates_to_underlying(self):
        underlying = FakeUnderlying(status_code=500)
        owner = FakeOwner(responses=[underlying])
        resp = Response._from_request(owner, "GET", "/x", {})

        with pytest.raises(requests.HTTPError):
            resp.raise_for_status()

        good = FakeUnderlying(status_code=200)
        owner2 = FakeOwner(responses=[good])
        resp2 = Response._from_request(owner2, "GET", "/x", {})

        assert resp2.raise_for_status() is None


class TestResponseOkMapping:
    def test_response_ok_mirrors_underlying_ok(self):
        # status 304: requests `ok` is True (< 400), httpx `is_success` is False
        # (not 2xx) — the divergence proves `ok` maps to the sync engine source.
        underlying = FakeUnderlying(status_code=304)
        owner = FakeOwner(responses=[underlying])
        resp = Response._from_request(owner, "GET", "/x", {})

        assert resp.ok is True
        assert resp.ok is underlying.ok

    async def test_async_response_ok_mirrors_underlying_is_success(self):
        underlying = FakeUnderlying(status_code=304, engine="httpx")
        owner = FakeOwner(engine="httpx", responses=[underlying])
        resp = await AsyncResponse._from_arequest(owner, "GET", "/x", {})

        assert resp.ok is False
        assert resp.ok is underlying.is_success


class TestResponseReload:
    def test_reload_replaces_underlying_in_place(self):
        owner = FakeOwner(
            responses=[
                FakeUnderlying(status_code=200),
                FakeUnderlying(status_code=201),
            ]
        )
        resp = Response._from_request(owner, "GET", "/job/42", {})

        assert resp.status_code == 200

        captured_ref = resp
        resp.reload()

        assert captured_ref is resp
        assert resp.status_code == 201

    def test_from_request_stores_recipe_and_dispatches_once(self):
        owner = FakeOwner(responses=[FakeUnderlying(status_code=200)])
        resp = Response._from_request(owner, "POST", "/items", {"q": "1"})

        assert resp._method == "POST"
        assert resp._path == "/items"
        assert resp._kwargs == {"q": "1"}
        assert owner.calls == [("POST", "/items", {"q": "1"})]

    async def test_areload_replaces_underlying_in_place(self):
        owner = FakeOwner(
            engine="httpx",
            responses=[
                FakeUnderlying(status_code=200, engine="httpx"),
                FakeUnderlying(status_code=201, engine="httpx"),
            ],
        )
        resp = await AsyncResponse._from_arequest(owner, "GET", "/job/42", {})

        assert resp.status_code == 200

        captured_ref = resp
        await resp.areload()

        assert captured_ref is resp
        assert resp.status_code == 201


class TestBaseResponseInternals:
    def test_underlying_is_none_until_injected(self):
        resp = BaseResponse(owner=None, method="GET", path="/x", kwargs={})

        assert resp._underlying is None

    def test_base_ok_is_abstract(self):
        base = BaseResponse(owner=None, method="GET", path="/x", kwargs={})

        with pytest.raises(NotImplementedError, match="ok"):
            _ = base.ok
