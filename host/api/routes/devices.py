from fastapi import APIRouter, Request

from host.core import config_manager, usbipd_wrapper

router = APIRouter()


@router.get("/devices")
def get_devices(request: Request):
    devices = usbipd_wrapper.list_devices()

    result = []
    for device in devices:
        device.is_permanent = config_manager.is_permanent(device.vid, device.pid)
        result.append(device.model_dump())

    return {"devices": result}
