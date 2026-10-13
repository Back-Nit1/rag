"""One packaged executable, three services: `metis-backend api|worker|speech`.

Each subcommand keeps its own process, port and lifecycle (the desktop
supervisor starts them separately). This file only adds what the supervisor
needs and the services do not provide yet:

* every subcommand exits when its stdin closes (the parent died or asked us to
  stop), so no orphan survives the app;

Readiness records (stdout, one line each, never secrets):
  api    METIS_API_PORT=<n>            (service-owned)
  speech {"event":"listening","protocol":1,"host","port","pid"}   (service-owned)
  worker {"event":"started","service":"worker","protocol":1,"pid"}  (service-owned)
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import types
from collections.abc import Callable

SERVICES = ("api", "worker", "speech")


def _on_stdin_eof(action: Callable[[], None]) -> None:
    def watch() -> None:
        try:
            # Raw fd reads: a buffered reader's lock aborts interpreter shutdown
            # ("could not acquire lock for <stdin>") while this thread is blocked.
            while os.read(0, 4096):
                pass
        except OSError:
            pass
        action()

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def run_api(argv: list[str]) -> int:
    from metis_api.__main__ import main

    # uvicorn installs SIGTERM/SIGINT handlers that drain and exit.
    _on_stdin_eof(lambda: os.kill(os.getpid(), signal.SIGINT if os.name == "nt" else signal.SIGTERM))
    main(argv)
    return 0


def run_speech(argv: list[str]) -> int:
    from metis_speech.server import main

    return main([*argv, "--exit-on-stdin-eof"])


def run_worker(argv: list[str]) -> int:
    from metis_pipeline import local_worker

    stop = threading.Event()
    original_stop = local_worker._request_stop

    def request_stop(signum: int, frame: object) -> None:
        original_stop(signum, frame)  # type: ignore[arg-type]
        stop.set()

    def stop_now() -> None:
        local_worker._stop = True
        stop.set()

    local_worker._request_stop = request_stop
    # Idle sleeps wake immediately on stop instead of finishing a 2-10s nap.
    local_worker.time = types.SimpleNamespace(sleep=lambda seconds: stop.wait(seconds))  # type: ignore[assignment]
    _on_stdin_eof(stop_now)
    return local_worker.main(argv)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in SERVICES:
        sys.stderr.write(f"usage: metis-backend {'|'.join(SERVICES)} [service args]\n")
        return 64
    runner = {"api": run_api, "worker": run_worker, "speech": run_speech}[args[0]]
    return runner(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
