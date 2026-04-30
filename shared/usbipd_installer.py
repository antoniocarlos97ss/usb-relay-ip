import os
import subprocess
import sys
import tempfile

USBIPD_RELEASES_URL = "https://github.com/dorssel/usbipd-win/releases/latest"
USBIPD_MSI_ASSET = "usbipd-win_x64.msi"


def _download_with_powershell(url: str, dest: str, progress_callback=None) -> bool:
    try:
        script = (
            "$ProgressPreference='SilentlyContinue';"
            f"try{{Invoke-WebRequest -Uri '{url}' -OutFile '{dest}' -UseBasicParsing -TimeoutSec 180}}"
            "catch{exit 1}"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=200,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        if proc.returncode != 0:
            print(f"PowerShell error: {proc.stderr.strip()}", file=sys.stderr)
            return False

        return os.path.exists(dest) and os.path.getsize(dest) > 50000
    except subprocess.TimeoutExpired:
        print("PowerShell download timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Download error: {e}", file=sys.stderr)
        return False


def download_and_install(progress_callback=None) -> tuple[bool, str]:
    tmp_dir = tempfile.mkdtemp()
    msi_path = os.path.join(tmp_dir, "usbipd-win_x64.msi")

    try:
        download_url = f"{USBIPD_RELEASES_URL}/download/{USBIPD_MSI_ASSET}"

        if progress_callback:
            progress_callback(0, None)

        if not _download_with_powershell(download_url, msi_path, progress_callback):
            return (
                False,
                "Falha ao baixar o instalador do usbipd-win.\n\n"
                "Possíveis causas:\n"
                "- Sem conexão com a internet\n"
                "- Firewall bloqueando o acesso\n"
                "- GitHub inacessível\n\n"
                "Você pode instalar manualmente:\n"
                "https://github.com/dorssel/usbipd-win/releases",
            )

        file_size = os.path.getsize(msi_path)
        if file_size < 50000:
            return False, "Instalador baixado está corrompido (tamanho insuficiente)."

        if progress_callback:
            progress_callback(-1, "Instalando usbipd-win (privilégios de Administrador necessários)...")

        proc = subprocess.run(
            ["msiexec", "/i", msi_path, "/quiet", "/norestart"],
            capture_output=True,
            text=True,
            timeout=180,
        )

        if proc.returncode not in (0, 3010):
            stderr = proc.stderr.strip()
            return False, f"Erro na instalação (código {proc.returncode})\n{stderr}"

        return True, "usbipd-win instalado com sucesso."
    except subprocess.TimeoutExpired:
        return False, "Instalação excedeu o tempo limite."
    except Exception as e:
        return False, f"Erro: {e}"
    finally:
        try:
            os.unlink(msi_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass
