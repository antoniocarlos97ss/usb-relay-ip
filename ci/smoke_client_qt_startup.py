"""Smoke-test the real PyQt QThread MRO used by the packaged Client."""

from PyQt6.QtCore import QCoreApplication

from client.api.host_client import HostApiClient
from client.core.pnp_recovery import PnpRecoveryMonitor


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication([])
    api_client = HostApiClient(
        host_ip="127.0.0.1",
        host_port=5757,
        api_key="",
    )
    monitor = PnpRecoveryMonitor(api_client)

    assert monitor._api_client.config_snapshot() == ("127.0.0.1", 5757, "")
    monitor.deleteLater()
    app.processEvents()
    print("REAL_PYQT_PNP_MONITOR_INIT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
