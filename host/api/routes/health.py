import time

from fastapi import APIRouter, Request

from host.core import usbipd_wrapper
from shared.constants import APP_VERSION
from shared.models import HealthStatus

router = APIRouter()

_start_time = time.time()


@router.get("/health", response_model=HealthStatus)
def get_health(request: Request):
    usbipd_avail = usbipd_wrapper.is_available()
    usbipd_listening = False
    if usbipd_avail:
        usbipd_listening = usbipd_wrapper.check_port_listening(3240)

    version_str = ""
    if usbipd_avail:
        major, minor = usbipd_wrapper.get_version()
        version_str = f"{major}.{minor}.0"

    devices = []
    if usbipd_avail:
        try:
            devices = usbipd_wrapper.list_devices()
        except Exception:
            pass
    shared_count = sum(1 for d in devices if d.state == "Shared")

    uptime = time.time() - _start_time

    status = "ok"
    if not usbipd_avail or not usbipd_listening:
        status = "degraded"

    return HealthStatus(
        status=status,
        usbipd_available=usbipd_avail,
        usbipd_listening=usbipd_listening,
        usbipd_version=version_str,
        shared_count=shared_count,
        uptime_seconds=round(uptime, 1),
    )
