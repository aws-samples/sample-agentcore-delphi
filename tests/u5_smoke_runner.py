"""U5 smoke-test child runner (LC-U5-6, SD-U5-8; NFR4.3). Support code, not a test module.

    python -I tests/u5_smoke_runner.py <script> [args...]

Replaces ``socket.socket``, ``socket.create_connection`` and ``socket.getaddrinfo`` with a
function that raises ``OSError("network disabled by smoke runner")``, sets ``sys.argv`` to
``[<script>, *args]`` and runs the script as ``__main__`` with ``runpy.run_path``. A script
that tries the network therefore exits non-zero with that message on stderr, and one that
does not runs exactly as a reader would run it.

It is passed by path and imports only the standard library: ``-I`` keeps this directory off
``sys.path``, so nothing beside it can be imported. ``ssl`` is imported before the guard is
installed because it subclasses ``socket.socket`` at import time.
"""
from __future__ import annotations

import runpy
import socket
import ssl  # noqa: F401 - imported before the guard: it subclasses socket.socket at import
import sys

MESSAGE = "network disabled by smoke runner"


def _refuse(*_args, **_kwargs):
    raise OSError(MESSAGE)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -I tests/u5_smoke_runner.py <script> [args...]", file=sys.stderr)
        return 2
    socket.socket = _refuse
    socket.create_connection = _refuse
    socket.getaddrinfo = _refuse
    sys.argv = list(argv)
    runpy.run_path(argv[0], run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
