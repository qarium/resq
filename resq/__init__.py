"""Package facade for resq — re-exports the public surface.

Consumers import directly from ``resq``: ``Requests`` / ``Session`` (the HTTP
clients, sourced from :mod:`resq.http`). This cell adds no logic of its own —
it only re-exports. The response wrappers (``BaseResponse`` / ``Response`` /
``AsyncResponse``) and the polling routines (``poll`` / ``apoll``) remain
reachable through :mod:`resq.http` for advanced typing needs.

The polling verbs never raise on window exhaustion: when the ``timeout`` window
elapses without a success status, the last (bad-status) response is returned, so
a held reference stays valid and ``reload``/``areload`` can retry it.
"""

from .http import Requests, Session

__all__ = ["Requests", "Session"]
