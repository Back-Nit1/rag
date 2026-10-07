"""Desktop-local identity and loopback launcher.

The per-launch secret only authenticates the caller. Ownership is a stable
per-install subject (``desktop_<uuid4hex>``) that this process creates at
``METIS_DESKTOP_IDENTITY_PATH`` on first use, so data survives relaunches.
"""

from __future__ import annotations

import ipaddress
import os
import re
import threading
import uuid
from pathlib import Path

from metis_contracts import ErrorCode, MetisError

_SUBJECT = re.compile(r"desktop_[0-9a-f]{32}")
_lock = threading.Lock()
_cache: dict[str, str] = {}


def install_subject(path: str) -> str:
    """Return the install's subject, creating it atomically on first use.

    A present but malformed file is an error, never overwritten: replacing it
    would silently move the user to an empty account.
    """
    with _lock:
        if path in _cache:
            return _cache[path]
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            subject = target.read_text(encoding="utf-8").strip()
        else:
            subject = f"desktop_{uuid.uuid4().hex}"
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(subject + "\n")
        if not _SUBJECT.fullmatch(subject):
            raise MetisError(ErrorCode.INTERNAL, "the desktop identity file is invalid")
        _cache[path] = subject
        return subject


def require_loopback(host: str) -> str:
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"desktop API must bind a loopback address; got {host!r}")
    return host
