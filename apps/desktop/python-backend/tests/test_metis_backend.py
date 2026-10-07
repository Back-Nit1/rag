import json
import subprocess
import sys
from pathlib import Path

import metis_backend

ENTRY = Path(metis_backend.__file__)


def test_unknown_subcommand_is_rejected():
    assert metis_backend.main([]) == 64
    assert metis_backend.main(["shell"]) == 64


def test_worker_prints_one_structured_record_then_exits_on_stdin_eof(tmp_path):
    env = {
        "METIS_MODE": "local",
        "METIS_SQLITE_PATH": str(tmp_path / "m.sqlite"),
        "METIS_LOCAL_OBJECTS_DIR": str(tmp_path / "obj"),
        "PATH": "/usr/bin:/bin",
    }
    p = subprocess.Popen(
        [sys.executable, str(ENTRY), "worker", "--interval", "0.2"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        # use the active interpreter's site-packages
    )
    record = json.loads(p.stdout.readline())
    assert record["event"] == "started" and record["protocol"] == 1 and record["service"] == "worker"
    p.stdin.close()
    assert p.wait(timeout=10) == 0
