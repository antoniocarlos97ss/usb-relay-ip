import json
import os
import sys

_current_lang = "en"


def _config_dir() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    path = os.path.join(appdata, "USBRelay")
    os.makedirs(path, exist_ok=True)
    return path


def _lang_config_path() -> str:
    return os.path.join(_config_dir(), "language.json")


def detect_language() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lang_id & 0xFF
            if primary == 0x16:
                return "pt"
        except Exception:
            pass
    return "en"


def load_language() -> str:
    path = _lang_config_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("language", detect_language())
    except Exception:
        pass
    return detect_language()


def save_language(lang: str):
    path = _lang_config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"language": lang}, f)
    except Exception:
        pass


STRINGS = {
    "en": {
        "app.name": "USBRelay",
        "host.title": "USBRelay Host",
        "client.title": "USBRelay Client",

        "btn.refresh": "Refresh",
        "btn.share_selected": "Share Selected",
        "btn.unshare_selected": "Unshare Selected",
        "btn.always": "★ Always",
        "btn.attach_selected": "Attach Selected",
        "btn.detach_selected": "Detach Selected",
        "btn.apply": "Apply",
        "btn.cancel": "Cancel",
        "btn.clear": "Clear",
        "btn.export_log": "Export Log",
        "btn.show": "Show",
        "btn.hide": "Hide",

        "tab.devices": "Devices",
        "tab.log": "Log",
        "tab.settings": "Settings",

        "table.bus_id": "Bus ID",
        "table.vid": "VID",
        "table.pid": "PID",
        "table.vid_pid": "VID / PID",
        "table.description": "Description",
        "table.status": "Status",
        "table.permanent": "Permanent",

        "state.not_shared": "Not shared",
        "state.shared": "Shared",
        "state.attached": "Attached",
        "state.available": "Available",
        "state.offline": "Offline",

        "ctx.share": "Share",
        "ctx.unshare": "Unshare",
        "ctx.attach": "Attach",
        "ctx.detach": "Detach",
        "ctx.always_share": "Mark as Always Share",
        "ctx.remove_always_share": "Remove from Always Share",
        "ctx.always_attach": "Mark as Always Attach",
        "ctx.remove_always_attach": "Remove from Always Attach",
        "ctx.copy_busid": "Copy Bus ID",

        "status.api_running": "● API Running on port {port}",
        "status.api_stopped": "✗ API Stopped",
        "status.api_starting": "● API Starting...",
        "status.connected": "● Connected to {host}:{port}",
        "status.connecting": "● Connecting...",
        "status.offline_retry": "✗ Offline — retrying...",
        "status.offline": "✗ Offline",
        "status.checking": "● Checking...",

        "settings.host_title": "USBRelay Host — Settings",
        "settings.client_title": "USBRelay Client — Settings",
        "settings.api_server": "API Server",
        "settings.host_connection": "Host Connection",
        "settings.device_monitor": "Device Monitor",
        "settings.polling": "Polling",
        "settings.startup": "Startup",
        "settings.api_port": "API Port:",
        "settings.api_key": "API Key:",
        "settings.api_key_placeholder": "Optional shared secret",
        "settings.host_ip": "Host IP:",
        "settings.host_ip_placeholder": "e.g. 192.168.1.10",
        "settings.host_port": "Host Port:",
        "settings.poll_interval": "Poll Interval:",
        "settings.poll_suffix": " seconds",
        "settings.autostart_service": "Start with Windows as a Service",
        "settings.autostart_boot": "Start automatically at system boot (before logon)",
        "settings.autostart_result_title": "Autostart Configuration",
        "settings.autostart_logon_ok": "Autostart at logon configured successfully.",
        "settings.autostart_logon_fail": "Failed to configure logon autostart.",
        "settings.autostart_boot_ok": "Autostart at system boot configured successfully.",
        "settings.autostart_boot_needs_admin": "Note: boot startup (before login) requires Administrator. Run the application as Administrator and Apply again.",
        "settings.autostart_disabled": "Autostart disabled.",
        "settings.autostart_failed": "Failed to configure autostart.",
        "settings.autostart_logon": "Start with Windows at Logon",
        "settings.autostart": "Autostart",

        "log.level": "Level:",
        "log.export_title": "Export Log",
        "log.export_filter": "Log Files (*.log);;All Files (*)",

        "tray.host_title": "USBRelay Host v1.0.0",
        "tray.client_title": "USBRelay Client v1.0.0",
        "tray.api_running": "● API Running",
        "tray.api_stopped": "✗ API Stopped",
        "tray.connected": "● Connected to {host}",
        "tray.connected_simple": "● Connected",
        "tray.disconnected": "✗ Disconnected",
        "tray.checking": "● Checking...",
        "tray.open": "Open Window",
        "tray.quit": "Quit USBRelay",

        "notify.shared": "Device {busid} shared successfully.",
        "notify.share_failed": "Failed to share {busid}: {msg}",
        "notify.already_shared": "Device {busid} is already {state}.",
        "notify.unshared": "Device {busid} unshared.",
        "notify.unshare_failed": "Failed to unshare {busid}: {msg}",
        "notify.attached": "Device {busid} attached successfully.",
        "notify.attach_failed": "Failed to attach {busid}: {msg}",
        "notify.detached": "Device {busid} detached.",
        "notify.detach_failed": "Failed to detach {busid}: {msg}",
        "notify.no_port": "Cannot find port for device {busid}.",
        "notify.marked_perm": "Device {busid} marked as Always Share.",
        "notify.unmarked_perm": "Device {busid} removed from Always Share.",
        "notify.marked_perm_client": "Device {busid} marked as Always Attach.",
        "notify.unmarked_perm_client": "Device {busid} removed from Always Attach.",
        "notify.auto_bound": "Device {busid} ({desc}) auto-shared on startup.",
        "notify.auto_attaching": "Attempting auto-attach for {vid}:{pid}...",
        "notify.auto_attached": "Auto-attached {busid} ({desc}).",
        "notify.auto_attach_failed": "Auto-attach failed for {vid}:{pid} — device not available.",
        "notify.tray_host": "USBRelay Host is running in the system tray.",
        "notify.tray_client": "USBRelay Client is running in the system tray.",

        "setup.usbipd_title": "USBRelay Host — Setup Required",
        "setup.usbipd_text": "usbipd-win not found or version too low.",
        "setup.usbipd_info": "USBRelay Host requires usbipd-win 4.0 or later.\n\nDownload: https://github.com/dorssel/usbipd-win/releases\n\nInstall usbipd-win, then restart USBRelay Host.",
        "setup.client_title": "USBRelay Client — Setup Required",
        "setup.client_text": "usbipd-win not found in the guest VM.",
        "setup.client_info": "USBRelay Client requires usbipd-win 4.0 or later installed in the guest VM.\n\nDownload: https://github.com/dorssel/usbipd-win/releases\n\nInstall usbipd-win, then restart USBRelay Client.",
        "setup.no_ip_title": "USBRelay Client — Setup",
        "setup.no_ip_text": "No Host IP configured.",
        "setup.no_ip_info": "Please enter the IP address of the USBRelay Host machine.\nOpen Settings from the main window to configure.",

        "install.title": "usbipd-win Required",
        "install.text": "usbipd-win was not found on this machine.\n\nWould you like to install it now?",
        "install.client_text": "usbip-win2 (USB/IP client) was not found.\n\nIt is required to attach USB devices remotely.\nWould you like to install it now?",
        "install.installing": "Installing usbipd-win...",
        "install.error_title": "Installation Failed",
        "install.error_text": "Failed to install usbipd-win.",
        "install.success_title": "Installation Complete",
        "install.success_text": "usbipd-win has been installed.\nUSBRelay will now start.",
    },
    "pt": {
        "app.name": "USBRelay",
        "host.title": "USBRelay Host",
        "client.title": "USBRelay Client",

        "btn.refresh": "Atualizar",
        "btn.share_selected": "Compartilhar Selecionado",
        "btn.unshare_selected": "Descompartilhar Selecionado",
        "btn.always": "★ Sempre",
        "btn.attach_selected": "Conectar Selecionado",
        "btn.detach_selected": "Desconectar Selecionado",
        "btn.apply": "Aplicar",
        "btn.cancel": "Cancelar",
        "btn.clear": "Limpar",
        "btn.export_log": "Exportar Log",
        "btn.show": "Mostrar",
        "btn.hide": "Ocultar",

        "tab.devices": "Dispositivos",
        "tab.log": "Log",
        "tab.settings": "Configurações",

        "table.bus_id": "Bus ID",
        "table.vid": "VID",
        "table.pid": "PID",
        "table.vid_pid": "VID / PID",
        "table.description": "Descrição",
        "table.status": "Status",
        "table.permanent": "Permanente",

        "state.not_shared": "Não compartilhado",
        "state.shared": "Compartilhado",
        "state.attached": "Conectado",
        "state.available": "Disponível",
        "state.offline": "Offline",

        "ctx.share": "Compartilhar",
        "ctx.unshare": "Descompartilhar",
        "ctx.attach": "Conectar",
        "ctx.detach": "Desconectar",
        "ctx.always_share": "Marcar como Sempre Compartilhar",
        "ctx.remove_always_share": "Remover de Sempre Compartilhar",
        "ctx.always_attach": "Marcar como Sempre Conectar",
        "ctx.remove_always_attach": "Remover de Sempre Conectar",
        "ctx.copy_busid": "Copiar Bus ID",

        "status.api_running": "● API Rodando na porta {port}",
        "status.api_stopped": "✗ API Parada",
        "status.api_starting": "● API Iniciando...",
        "status.connected": "● Conectado a {host}:{port}",
        "status.connecting": "● Conectando...",
        "status.offline_retry": "✗ Offline — tentando novamente...",
        "status.offline": "✗ Offline",
        "status.checking": "● Verificando...",

        "settings.host_title": "USBRelay Host — Configurações",
        "settings.client_title": "USBRelay Client — Configurações",
        "settings.api_server": "Servidor API",
        "settings.host_connection": "Conexão com Host",
        "settings.device_monitor": "Monitor de Dispositivos",
        "settings.polling": "Atualização",
        "settings.startup": "Inicialização",
        "settings.api_port": "Porta API:",
        "settings.api_key": "Chave API:",
        "settings.api_key_placeholder": "Segredo compartilhado opcional",
        "settings.host_ip": "IP do Host:",
        "settings.host_ip_placeholder": "ex: 192.168.1.10",
        "settings.host_port": "Porta do Host:",
        "settings.poll_interval": "Intervalo de Consulta:",
        "settings.poll_suffix": " segundos",
        "settings.autostart_service": "Iniciar com Windows como Serviço",
        "settings.autostart_boot": "Iniciar automaticamente na inicialização (antes do login)",
        "settings.autostart_result_title": "Configuração de Início Automático",
        "settings.autostart_logon_ok": "Início automático ao fazer logon configurado com sucesso.",
        "settings.autostart_logon_fail": "Falha ao configurar início automático no logon.",
        "settings.autostart_boot_ok": "Início automático na inicialização do sistema configurado com sucesso.",
        "settings.autostart_boot_needs_admin": "Nota: o início antes do login (boot) requer Administrador. Execute o aplicativo como Administrador e aplique novamente.",
        "settings.autostart_disabled": "Início automático desativado.",
        "settings.autostart_failed": "Falha ao configurar início automático.",
        "settings.autostart_logon": "Iniciar com Windows ao Fazer Logon",
        "settings.autostart": "Início Automático",

        "log.level": "Nível:",
        "log.export_title": "Exportar Log",
        "log.export_filter": "Arquivos de Log (*.log);;Todos os Arquivos (*)",

        "tray.host_title": "USBRelay Host v1.0.0",
        "tray.client_title": "USBRelay Client v1.0.0",
        "tray.api_running": "● API Rodando",
        "tray.api_stopped": "✗ API Parada",
        "tray.connected": "● Conectado a {host}",
        "tray.connected_simple": "● Conectado",
        "tray.disconnected": "✗ Desconectado",
        "tray.checking": "● Verificando...",
        "tray.open": "Abrir Janela",
        "tray.quit": "Sair do USBRelay",

        "notify.shared": "Dispositivo {busid} compartilhado com sucesso.",
        "notify.share_failed": "Falha ao compartilhar {busid}: {msg}",
        "notify.already_shared": "Dispositivo {busid} já está {state}.",
        "notify.unshared": "Dispositivo {busid} descompartilhado.",
        "notify.unshare_failed": "Falha ao descompartilhar {busid}: {msg}",
        "notify.attached": "Dispositivo {busid} conectado com sucesso.",
        "notify.attach_failed": "Falha ao conectar {busid}: {msg}",
        "notify.detached": "Dispositivo {busid} desconectado.",
        "notify.detach_failed": "Falha ao desconectar {busid}: {msg}",
        "notify.no_port": "Não foi possível encontrar a porta para o dispositivo {busid}.",
        "notify.marked_perm": "Dispositivo {busid} marcado como Sempre Compartilhar.",
        "notify.unmarked_perm": "Dispositivo {busid} removido de Sempre Compartilhar.",
        "notify.marked_perm_client": "Dispositivo {busid} marcado como Sempre Conectar.",
        "notify.unmarked_perm_client": "Dispositivo {busid} removido de Sempre Conectar.",
        "notify.auto_bound": "Dispositivo {busid} ({desc}) auto-compartilhado na inicialização.",
        "notify.auto_attaching": "Tentando auto-conectar {vid}:{pid}...",
        "notify.auto_attached": "Auto-conectado {busid} ({desc}).",
        "notify.auto_attach_failed": "Auto-conexão falhou para {vid}:{pid} — dispositivo não disponível.",
        "notify.tray_host": "USBRelay Host está rodando na bandeja do sistema.",
        "notify.tray_client": "USBRelay Client está rodando na bandeja do sistema.",

        "setup.usbipd_title": "USBRelay Host — Configuração Necessária",
        "setup.usbipd_text": "usbipd-win não encontrado ou versão muito antiga.",
        "setup.usbipd_info": "USBRelay Host requer usbipd-win 4.0 ou superior.\n\nDownload: https://github.com/dorssel/usbipd-win/releases\n\nInstale o usbipd-win e reinicie o USBRelay Host.",
        "setup.client_title": "USBRelay Client — Configuração Necessária",
        "setup.client_text": "usbipd-win não encontrado na VM convidada.",
        "setup.client_info": "USBRelay Client requer usbipd-win 4.0 ou superior instalado na VM.\n\nDownload: https://github.com/dorssel/usbipd-win/releases\n\nInstale o usbipd-win e reinicie o USBRelay Client.",
        "setup.no_ip_title": "USBRelay Client — Configuração",
        "setup.no_ip_text": "Nenhum IP do Host configurado.",
        "setup.no_ip_info": "Informe o endereço IP da máquina USBRelay Host.\nAbra as Configurações na janela principal para configurar.",

        "install.title": "usbipd-win Necessário",
        "install.text": "usbipd-win não foi encontrado.\n\nDeseja instalá-lo agora?",
        "install.client_text": "usbip-win2 (cliente USB/IP) não foi encontrado.\n\nEle é necessário para conectar dispositivos USB remotamente.\n\nDeseja instalá-lo agora?",
        "install.installing": "Instalando usbipd-win...",
        "install.error_title": "Falha na Instalação",
        "install.error_text": "Falha ao instalar o usbipd-win.",
        "install.success_title": "Instalação Concluída",
        "install.success_text": "usbipd-win foi instalado.\nO USB Relay IP será iniciado agora.",
    },
}


def set_language(lang: str):
    global _current_lang
    if lang in STRINGS:
        _current_lang = lang
        save_language(lang)


def get_language() -> str:
    return _current_lang


def t(key: str, **kwargs) -> str:
    text = STRINGS.get(_current_lang, STRINGS["en"]).get(key)
    if text is None:
        text = STRINGS["en"].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text


_current_lang = load_language()
