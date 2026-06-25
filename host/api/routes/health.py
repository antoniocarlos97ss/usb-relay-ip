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
    usbipd_service_state = ""

    if usbipd_avail:
        usbipd_listening = usbipd_wrapper.check_port_listening(3240)
        usbipd_service_state = usbipd_wrapper.get_service_state()

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

    # Determine status:
    # - "ok":       usbipd installed AND listening on port 3240
    # - "degraded": usbipd installed but NOT listening (service stopped/crashed)
    # - "error":    usbipd not installed at all
    if not usbipd_avail:
        status = "error"
    elif not usbipd_listening:
        status = "degraded"
    else:
        status = "ok"

    return HealthStatus(
        status=status,
        usbipd_available=usbipd_avail,
        usbipd_listening=usbipd_listening,
        usbipd_version=version_str,
        usbipd_service_state=usbipd_service_state,
        shared_count=shared_count,
        uptime_seconds=round(uptime, 1),
    )
