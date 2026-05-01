import logging
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

    @property
    def host_ip(self) -> str:
        return self._host_ip

    @host_ip.setter
    def host_ip(self, value: str):
        self._host_ip = value

    @property
    def host_port(self) -> int:
        return self._host_port

    @host_port.setter
    def host_port(self, value: int):
        self._host_port = value

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str):
        self._api_key = value

    @property
    def base_url(self) -> str:
        return f"http://{self._host_ip}:{self._host_port}"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key and self._api_key.strip():
            headers["X-API-Key"] = self._api_key
        return headers

    def _request(self, method: str, path: str) -> Optional[dict]:
        url = f"{self.base_url}/api/v1{path}"

        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                response = client.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                )
                if response.status_code == 200:
                    self._connected = True
                    return response.json()
                elif response.status_code == 401:
                    logger.error("API key rejected by host")
                    self._connected = False
                    return None
                else:
                    logger.warning(f"Host returned {response.status_code}")
                    return None
        except (httpx.ConnectError, httpx.TimeoutException):
            self._connected = False
            return None
        except Exception as exc:
            logger.debug(f"Request error: {exc}")
            self._connected = False
            return None

    def get_devices(self) -> list[UsbDevice]:
        data = self._request("GET", "/devices")
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
            return None
        try:
            return HealthStatus(**data)
        except Exception as exc:
            logger.warning(f"Failed to parse health data: {exc}")
            return None

    def is_connected(self) -> bool:
        return self._connected
