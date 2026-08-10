"""Adapters cell facade — re-exports the engine-binding adapters.

``Adapter``/``RequestsAdapter``/``HttpxAdapter`` are exposed here for tests and
typing only; they are NOT re-exported through the public facades ``resq`` or
``resq.http`` — consumers select a mode via the owning client's ``adapter``
argument.
"""

from .adapters import Adapter, HttpxAdapter, RequestsAdapter

__all__ = ["Adapter", "HttpxAdapter", "RequestsAdapter"]
