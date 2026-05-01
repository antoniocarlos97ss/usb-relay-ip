import os
import subprocess
import sys
from pathlib import Path


def _list_bundled_installers() -> list[str]:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent.parent

    install_dir = base / "usbipd-install"
    if not install_dir.exists():
        return []

    files = []
    for p in install_dir.iterdir():
        if p.suffix.lower() in (".msi", ".exe"):
            files.append(str(p))
    return sorted(files)


def _find_installer(prefix: str) -> str | None:
    for path in _list_bundled_installers():
        name = os.path.basename(path).lower()
        if name.startswith(prefix.lower()):
            return path
    return None


def install_bundled(progress_callback=None) -> tuple[bool, str]:
    installer = _find_installer("usbipd-win") or _find_installer("usbip-")
    if not installer:
        files = _list_bundled_installers()
        names = ", ".join(os.path.basename(f) for f in files) if files else "nenhum"
        return (
            False,
            f"Instalador não encontrado. Disponíveis: {names}",
        )

    name = os.path.basename(installer).lower()
    is_msi = name.endswith(".msi")

    try:
        if progress_callback:
            label = f"Instalando {os.path.basename(installer)}..."
            progress_callback(-1, label)

        if is_msi:
            proc = subprocess.run(
                ["msiexec", "/i", installer, "/quiet", "/norestart"],
                capture_output=True, text=True, timeout=180,
            )
            ok_codes = (0, 3010)
        else:
            proc = subprocess.run(
                [installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                capture_output=True, text=True, timeout=180,
            )
            ok_codes = (0,)

        if proc.returncode not in ok_codes:
            return False, f"Erro na instalação (código {proc.returncode})"

        return True, f"{os.path.basename(installer)} instalado com sucesso."
    except subprocess.TimeoutExpired:
        return False, "Instalação excedeu o tempo limite."
    except Exception as e:
        return False, f"Erro: {e}"


def install_for_client(progress_callback=None) -> tuple[bool, str]:
    installer = _find_installer("usbip-")
    if not installer:
        installer = _find_installer("usbipd-win")

    if not installer:
        return (
            False,
            "Instalador do usbip-win2 não encontrado nos arquivos do programa.\n\n"
            "Instale manualmente: https://github.com/vadimgrn/usbip-win2/releases",
        )

    name = os.path.basename(installer).lower()

    try:
        if progress_callback:
            progress_callback(-1, f"Instalando {os.path.basename(installer)}...")

        if name.endswith(".msi"):
            proc = subprocess.run(
                ["msiexec", "/i", installer, "/quiet", "/norestart"],
                capture_output=True, text=True, timeout=180,
            )
        else:
            proc = subprocess.run(
                [installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                capture_output=True, text=True, timeout=180,
            )

        if proc.returncode not in (0, 3010, None):
            return False, f"Erro na instalação (código {proc.returncode})"

        return True, f"{os.path.basename(installer)} instalado com sucesso."
    except subprocess.TimeoutExpired:
        return False, "Instalação excedeu o tempo limite."
    except Exception as e:
        return False, f"Erro: {e}"
