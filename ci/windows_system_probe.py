"""Small probe executed as SYSTEM by the Windows integration workflow."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _current_sid() -> str:
    output = subprocess.check_output(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"S-\d-(?:\d+-)+\d+", output, re.IGNORECASE)
    if not match:
        raise RuntimeError(f"Unable to parse current SID from whoami: {output!r}")
    return match.group(0)


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("marker_path")
    parser.add_argument("programdata_root")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    os.environ["PROGRAMDATA"] = str(Path(args.programdata_root).resolve())

    from client.core import operation_coordinator

    acquired = operation_coordinator.try_acquire_named("ci-system-probe", "global")
    try:
        payload: dict[str, object] = {
            "sid": _current_sid(),
            "lock_acquired": acquired,
            "programdata": os.environ["PROGRAMDATA"],
            "headless_argument": args.headless,
            "python": sys.executable,
        }
        _atomic_json_write(Path(args.marker_path), payload)
    finally:
        if acquired:
            operation_coordinator.release_named("ci-system-probe", "global")

    return 0 if acquired else 2


if __name__ == "__main__":
    raise SystemExit(main())
