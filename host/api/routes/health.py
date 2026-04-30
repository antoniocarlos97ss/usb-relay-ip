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
    version_str = ""
    if usbipd_avail:
        major, minor = usbipd_wrapper.get_version()
        version_str = f"{major}.{minor}.0"

    devices = usbipd_wrapper.list_devices()
    shared_count = sum(1 for d in devices if d.state == "Shared")

    uptime = time.time() - _start_time

    return HealthStatus(
        status="ok" if usbipd_avail else "degraded",
        usbipd_available=usbipd_avail,
        usbipd_version=version_str,
        shared_count=shared_count,
        uptime_seconds=round(uptime, 1),
    )
