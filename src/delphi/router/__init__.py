"""Router interface + implementations.

The router is an in-process library (design D1), not a standing service. It loads
a trained artifact and exposes a single synchronous predict() call on the request path.
"""
from delphi.router.base import Decision, Router, RouterError

__all__ = ["Decision", "Router", "RouterError"]
