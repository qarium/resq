"""Contract and logic tests for resq.http.responses.

Contract tests verify the declared API (class hierarchy, properties, methods,
constructor signature). Logic tests exercise the proxy mapping and in-place
reload semantics against `FakeUnderlying` and the `reexec`/`arexec` injection
seams (`build_response` / `build_async_response`), which mirror the
Architecture-A verb recipe (run the primary through the seam, inject the
underlying post-construction).
"""

import inspect
import unittest.mock

import pytest
import requests
from resq.http.responses.responses import AsyncResponse, BaseResponse, Response

from tests.http.conftest import FakeUnderlying, build_async_response, build_response, make_reexec


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

    def test_response_declares_reload(self):
        assert callable(Response.reload)

    def test_async_response_declares_areload(self):
        assert inspect.iscoroutinefunction(AsyncResponse.areload)

    def test_base_response_constructor_signature_is_four_params(self):
        params = [name for name in inspect.signature(BaseResponse.__init__).parameters if name != "self"]
        assert params == ["method", "path", "kwargs", "reexec"]

    def test_response_has_no_from_request_factory(self):
        # Architecture A removes the _from_request / _from_arequest factories.
        assert not hasattr(Response, "_from_request")
        assert not hasattr(AsyncResponse, "_from_arequest")


class TestResponseProxy:
    """Proxy properties forward to the underlying engine response verbatim."""

    def _wrap(self, status_code=200, **attrs):
        underlying = FakeUnderlying(status_code=status_code, **attrs)
        resp, _ = build_response([underlying], method="GET", path="/x")
        return resp

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
        resp, _ = build_response(
            [FakeUnderlying(status_code=200, json_return=body)],
            method="GET",
            path="/data",
        )

        assert resp.json() == {"a": 1, "b": [2, 3]}

    def test_raise_for_status_delegates_to_underlying(self):
        resp, _ = build_response([FakeUnderlying(status_code=500)], method="GET", path="/x")

        with pytest.raises(requests.HTTPError):
            resp.raise_for_status()

        resp2, _ = build_response([FakeUnderlying(status_code=200)], method="GET", path="/x")

        assert resp2.raise_for_status() is None


class TestResponseOkMapping:
    def test_response_ok_mirrors_underlying_ok(self):
        # status 304: requests `ok` is True (< 400), httpx `is_success` is False
        # (not 2xx) — the divergence proves `ok` maps to the sync engine source.
        underlying = FakeUnderlying(status_code=304)
        resp, _ = build_response([underlying], method="GET", path="/x")

        assert resp.ok is True
        assert resp.ok is underlying.ok

    async def test_async_response_ok_mirrors_underlying_is_success(self):
        underlying = FakeUnderlying(status_code=304, engine="httpx")
        resp, _ = await build_async_response([underlying], method="GET", path="/x")

        assert resp.ok is False
        assert resp.ok is underlying.is_success


class TestResponseReload:
    def test_reload_replaces_underlying_in_place(self):
        resp, reexec = build_response(
            [
                FakeUnderlying(status_code=200),
                FakeUnderlying(status_code=201),
            ],
            method="GET",
            path="/job/42",
        )

        assert resp.status_code == 200

        captured_ref = resp
        resp.reload()

        assert captured_ref is resp
        assert resp.status_code == 201
        # primary (injected by build_response) + reload both replayed via reexec.
        assert reexec.call_count == 2

    def test_reload_stores_recipe_on_construction(self):
        # Architecture A: the recipe lives on the wrapper (no owner, no factory).
        kwargs = {"q": "1"}
        resp, reexec = build_response(
            [FakeUnderlying(status_code=200)],
            method="POST",
            path="/items",
            kwargs=kwargs,
        )

        assert resp._method == "POST"
        assert resp._path == "/items"
        assert resp._kwargs is kwargs
        assert resp._reexec is reexec  # the injected re-execute seam
        assert reexec.call_count == 1  # primary dispatched once

    async def test_areload_replaces_underlying_in_place(self):
        resp, arexec = await build_async_response(
            [
                FakeUnderlying(status_code=200, engine="httpx"),
                FakeUnderlying(status_code=201, engine="httpx"),
            ],
            method="GET",
            path="/job/42",
        )

        assert resp.status_code == 200

        captured_ref = resp
        await resp.areload()

        assert captured_ref is resp
        assert resp.status_code == 201
        assert arexec.call_count == 2  # primary + areload both replayed via arexec.


class TestBaseResponseInternals:
    def test_underlying_is_none_until_injected(self):
        resp = BaseResponse("GET", "/x", {}, make_reexec([]))

        assert resp._underlying is None

    def test_base_ok_is_abstract(self):
        base = BaseResponse("GET", "/x", {}, unittest.mock.Mock())

        with pytest.raises(NotImplementedError, match="ok"):
            _ = base.ok
