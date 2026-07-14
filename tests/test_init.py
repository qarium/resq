"""Contract tests for the resq package facade (resq/__init__.py).

Verifies that the public surface re-exports resolve live from the top-level
``resq`` namespace and originate from their declared source cell:

* ``Requests`` / ``Session`` → ``resq.http.clients``

The facade adds no logic of its own — it only re-exports.
"""

import resq as resq_pkg
import resq.http as http_cell
from resq import Requests, Session


class TestFacadeReexports:
    def test_public_symbols_importable_from_top_level(self):
        # The two facade embeddings must resolve directly from `resq`.
        for name in ("Requests", "Session"):
            assert hasattr(resq_pkg, name), f"resq facade missing {name}"

    def test_requests_and_session_originate_from_http_cell(self):
        # Re-exported through resq.http, which in turn sources them from resq.http.clients.
        assert Requests is http_cell.Requests
        assert Session is http_cell.Session

    def test_facade_all_lists_exactly_the_public_surface(self):
        assert resq_pkg.__all__ == ["Requests", "Session"]
