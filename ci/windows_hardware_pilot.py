"""Run one destructive, identity-checked reconnect cycle on a pilot Windows runner."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client.api.host_client import HostApiClient
from client.core import usbip_wrapper


logger = logging.getLogger("windows-hardware-pilot")


def _serialize_attached(device) -> dict[str, object]:
    return device.model_dump()


def _normalize_hex(value: str, label: str) -> str:
    normalized = value.strip().lower().removeprefix("0x")
    if len(normalized) != 4 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{label} must contain exactly four hexadecimal characters")
    if normalized == "0000":
        raise ValueError(f"{label}=0000 is a failure sentinel, not an allowed identity")
    return normalized


def _query_payload() -> dict[str, object]:
    result = usbip_wrapper.query_attached_devices(timeout=10)
    return {
        "success": result.success,
        "error": result.error,
        "devices": [_serialize_attached(item) for item in result.devices],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5757)
    parser.add_argument("--busid", required=True)
    parser.add_argument("--vid", required=True)
    parser.add_argument("--pid", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if os.name != "nt":
        raise RuntimeError("The hardware pilot must run on Windows")

    from client.core.scheduled_reconnect import _run_reconnect_cycle

    vid = _normalize_hex(args.vid, "VID")
    pid = _normalize_hex(args.pid, "PID")
    api_key = os.environ.get("USB_RELAY_PILOT_API_KEY", "")
    api = HostApiClient(args.host, args.port, api_key)
    report: dict[str, object] = {
        "host": args.host,
        "port": args.port,
        "busid": args.busid,
        "vid": vid,
        "pid": pid,
        "before": _query_payload(),
    }

    try:
        health = api.get_health()
        if health is None:
            raise RuntimeError("Host health endpoint is unavailable or rejected the API key")
        report["host_health"] = health.model_dump()

        matches = [device for device in api.get_devices(timeout=10) if device.busid == args.busid]
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one host device at {args.busid}; found {len(matches)}")
        device = matches[0]
        if (device.vid.lower(), device.pid.lower()) != (vid, pid):
            raise RuntimeError(
                f"Identity mismatch at {args.busid}: host={device.vid}:{device.pid}, expected={vid}:{pid}"
            )

        success, message = _run_reconnect_cycle(api, device, record_completion=False)
        report["cycle_success"] = success
        report["cycle_message"] = message
        report["after"] = _query_payload()
        if not success:
            raise RuntimeError(message)

        exact_local = [
            item
            for item in report["after"]["devices"]
            if item["busid"] == args.busid
            and item.get("vid", "").lower() == vid
            and item.get("pid", "").lower() == pid
        ]
        if len(exact_local) != 1:
            raise RuntimeError(
                f"Expected one verified local session after recovery; found {len(exact_local)}"
            )
        return 0
    except Exception as exc:
        report["error"] = str(exc)
        logger.exception("Hardware pilot failed")
        return 1
    finally:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
