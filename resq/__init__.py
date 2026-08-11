"""Package facade for resq — re-exports the public surface.

Consumers import directly from ``resq``: ``Requests`` / ``Session`` (the HTTP
clients, sourced from :mod:`resq.http`). This cell adds no logic of its own —
it only re-exports. The response wrappers (``BaseResponse`` / ``Response`` /
``AsyncResponse``) and the unified ``poll`` routine remain reachable through
:mod:`resq.http` for advanced typing needs.

The clients follow the adapter model: a single constructor
``Requests(base_url, adapter='requests'|'httpx', timeout=None)`` selects the
mode on the instance — synchronous via ``requests`` or asynchronous via
``httpx`` — with one set of dual-mode verbs (``get``/``post``/``...``), a
unified ``close``, both context managers, and a unified ``reload`` on the
wrappers. ``poll`` never raises on window exhaustion: when the ``timeout``
window elapses without a success status, the last (bad-status) response is
returned, so a held reference stays valid and ``reload`` can retry it.
"""

from .http import Requests, Session

__all__ = ["Requests", "Session"]
