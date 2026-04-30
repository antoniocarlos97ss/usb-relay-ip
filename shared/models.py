from typing import Optional
from pydantic import BaseModel, Field


class UsbDevice(BaseModel):
    busid: str
    vid: str
    pid: str
    description: str
    state: str = "Not shared"
    is_permanent: bool = False


class CommandResult(BaseModel):
    success: bool
    message: str
    stdout: str = ""
    stderr: str = ""


class HealthStatus(BaseModel):
    status: str = "ok"
    usbipd_available: bool = False
    usbipd_version: str = ""
    shared_count: int = 0
    uptime_seconds: float = 0.0


class AttachedDevice(BaseModel):
    port: int
    busid: str
    vid: str
    pid: str


class PermanentDevice(BaseModel):
    vid: str
    pid: str
    description: str = ""
    busid_hint: str = ""
    auto_bind: bool = True


class ClientPermanentDevice(BaseModel):
    vid: str
    pid: str
    description: str = ""
    auto_attach: bool = True


class HostConfig(BaseModel):
    api_port: int = 5757
    api_key: str = ""
    poll_interval_seconds: int = 5
    autostart_as_service: bool = False
    permanent_devices: list[PermanentDevice] = Field(default_factory=list)


class ClientConfig(BaseModel):
    host_ip: str = ""
    host_port: int = 5757
    api_key: str = ""
    poll_interval_seconds: int = 10
    autostart_with_windows: bool = False
    permanent_devices: list[ClientPermanentDevice] = Field(default_factory=list)
