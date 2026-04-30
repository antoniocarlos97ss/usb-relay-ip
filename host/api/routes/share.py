from fastapi import APIRouter, Request

from host.core import config_manager, usbipd_wrapper
from shared.models import UsbDevice

router = APIRouter()


@router.post("/devices/{busid}/bind")
def bind_device(busid: str, request: Request):
    devices = usbipd_wrapper.list_devices()
    device = next((d for d in devices if d.busid == busid), None)

    if device is None:
        return {"success": False, "message": f"Device {busid} not found."}

    if device.state == "Shared" or device.state == "Attached":
        return {"success": False, "message": f"Device {busid} is already {device.state.lower()}."}

    result = usbipd_wrapper.bind_device(busid)
    return result.model_dump()


@router.post("/devices/{busid}/unbind")
def unbind_device(busid: str, request: Request):
    result = usbipd_wrapper.unbind_device(busid)
    return result.model_dump()


@router.post("/devices/{busid}/permanent")
def set_permanent(busid: str, request: Request):
    devices = usbipd_wrapper.list_devices()
    device = next((d for d in devices if d.busid == busid), None)

    if device is None:
        return {"success": False, "message": f"Device {busid} not found."}

    config_manager.add_permanent_device(
        vid=device.vid,
        pid=device.pid,
        description=device.description,
        busid_hint=device.busid,
    )

    return {"success": True, "message": f"Device {busid} marked as permanent."}


@router.delete("/devices/{busid}/permanent")
def remove_permanent(busid: str, request: Request):
    devices = usbipd_wrapper.list_devices()
    device = next((d for d in devices if d.busid == busid), None)

    if device is None:
        return {"success": False, "message": f"Device {busid} not found."}

    config_manager.remove_permanent_device(device.vid, device.pid)

    return {"success": True, "message": f"Device {busid} removed from permanent list."}


@router.get("/config")
def get_config(request: Request):
    config = config_manager.load_config()
    return {
        "api_port": config.api_port,
        "poll_interval_seconds": config.poll_interval_seconds,
        "autostart_as_service": config.autostart_as_service,
        "permanent_devices_count": len(config.permanent_devices),
    }
