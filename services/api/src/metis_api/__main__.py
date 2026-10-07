"""``python -m metis_api``: serve on loopback and report the chosen port.

Prints ``METIS_API_PORT=<port>`` on stdout once the socket is bound, which is
how the desktop supervisor learns the ephemeral port. Never prints the secret.
"""

from __future__ import annotations

import argparse
import socket
import sys

import uvicorn
from metis_contracts import settings

from .desktop import require_loopback


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="metis_api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    if settings().desktop.enabled:
        try:
            require_loopback(args.host)
        except ValueError as exc:
            parser.error(str(exc))
    sock = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET)
    sock.bind((args.host, args.port))
    sys.stdout.write(f"METIS_API_PORT={sock.getsockname()[1]}\n")
    sys.stdout.flush()
    from .main import app

    uvicorn.run(app, fd=sock.fileno(), log_level="info")


if __name__ == "__main__":
    main()
