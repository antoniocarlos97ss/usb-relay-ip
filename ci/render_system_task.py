"""Render the production boot-task XML for the Windows CI SYSTEM probe."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client.core.autostart_manager import _render_boot_task_xml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("xml_path")
    parser.add_argument("marker_path")
    parser.add_argument("programdata_root")
    args = parser.parse_args()

    probe = ROOT / "ci" / "windows_system_probe.py"
    xml = _render_boot_task_xml(
        sys.executable,
        [str(probe), args.marker_path, args.programdata_root],
    )
    Path(args.xml_path).write_bytes(xml.encode("utf-16"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
