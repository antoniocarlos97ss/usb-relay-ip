import logging
import threading
from typing import Optional

import httpx

from shared.constants import DEFAULT_API_PORT
from shared.models import HealthStatus, UsbDevice

logger = logging.getLogger(__name__)

TIMEOUT = 3.0


class HostApiClient:
    def __init__(self, host_ip: str = "", host_port: int = DEFAULT_API_PORT, api_key: str = ""):
        self._host_ip = host_ip
        self._host_port = host_port
        self._api_key = api_key
        self._connected = False
        self._usbipd_listening = False  # Unknown until first health check
        self._config_lock = threading.RLock()

    @property
    def host_ip(self) -> str:
        with self._config_lock:
            return self._host_ip

    @host_ip.setter
    def host_ip(self, value: str):
        with self._config_lock:
            self._host_ip = value

    @property
    def host_port(self) -> int:
        with self._config_lock:
            return self._host_port

    @host_port.setter
    def host_port(self, value: int):
        with self._config_lock:
            self._host_port = value

    @property
    def api_key(self) -> str:
        with self._config_lock:
            return self._api_key

    @api_key.setter
    def api_key(self, value: str):
        with self._config_lock:
            self._api_key = value

    def update_config(self, host_ip: str, host_port: int, api_key: str) -> None:
        with self._config_lock:
            self._host_ip = host_ip
            self._host_port = host_port
            self._api_key = api_key

    def config_snapshot(self) -> tuple[str, int, str]:
        with self._config_lock:
            return self._host_ip, self._host_port, self._api_key

    def _set_connection_state(self, connected: bool, usbipd_listening: bool | None = None) -> None:
        with self._config_lock:
            self._connected = connected
            if usbipd_listening is not None:
                self._usbipd_listening = usbipd_listening

    @property
    def base_url(self) -> str:
        host_ip, host_port, _ = self.config_snapshot()
        return f"http://{host_ip}:{host_port}"

    def _headers(self) -> dict[str, str]:
        _, _, api_key = self.config_snapshot()
        headers: dict[str, str] = {}
        if api_key and api_key.strip():
            headers["X-API-Key"] = api_key
        return headers

    def _request(self, method: str, path: str, timeout: float = TIMEOUT) -> Optional[dict]:
        host_ip, host_port, api_key = self.config_snapshot()
        url = f"http://{host_ip}:{host_port}/api/v1{path}"
        headers = {"X-API-Key": api_key} if api_key.strip() else {}

        try:
            with httpx.Client(timeout=max(0.1, timeout)) as client:
                response = client.request(
                    method=method,
                    url=url,
                    headers=headers,
                )
                if response.status_code == 200:
                    self._set_connection_state(True)
                    return response.json()
                elif response.status_code == 401:
                    logger.error("API key rejected by host")
                    self._set_connection_state(False)
                    return None
                else:
                    logger.warning(f"Host returned {response.status_code}")
                    self._set_connection_state(False)
                    return None
        except (httpx.ConnectError, httpx.TimeoutException):
            self._set_connection_state(False, False)
            return None
        except Exception as exc:
            logger.debug(f"Request error: {exc}")
            self._set_connection_state(False)
            return None

    def get_devices(self, timeout: float = TIMEOUT) -> list[UsbDevice]:
        data = self._request("GET", "/devices", timeout=timeout)
        if data is None:
            return []

        raw_devices = data.get("devices", [])
        devices: list[UsbDevice] = []
        for raw in raw_devices:
            try:
                devices.append(UsbDevice(**raw))
            except Exception as exc:
                logger.warning(f"Failed to parse device data: {exc}")
                continue
        return devices

    def get_health(self) -> Optional[HealthStatus]:
        data = self._request("GET", "/health")
        if data is None:
            self._set_connection_state(self.is_connected(), False)
            return None
        try:
            health = HealthStatus(**data)
            self._set_connection_state(self.is_connected(), health.usbipd_listening)
            return health
        except Exception as exc:
            logger.warning(f"Failed to parse health data: {exc}")
            return None

    def is_connected(self) -> bool:
        with self._config_lock:
            return self._connected

    def bind_device(self, busid: str) -> bool:
        data = self._request("POST", f"/devices/{busid}/bind")
        return bool(data and data.get("success"))

    def unbind_device(self, busid: str) -> bool:
        data = self._request("POST", f"/devices/{busid}/unbind")
        return bool(data and data.get("success"))
