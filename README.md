# USB Relay IP

Passagem USB do host Hyper-V para VMs convidadas — simplificada.

O USB Relay IP é uma aplicação desktop Windows de dois componentes que encapsula o `usbipd-win` (host) e o `usbip-win2` (client) com uma GUI amigável e uma camada de comunicação REST, eliminando a necessidade de interação manual por linha de comando.

## Componentes

| Componente | Onde Instalar | Finalidade |
|---|---|---|
| **USBRelay Host** | Máquina física host Hyper-V | Detecta dispositivos USB, compartilha via `usbipd-win` e expõe API REST |
| **USBRelay Client** | VM Windows convidada | Conecta dispositivos USB compartilhados via `usbip-win2` (driver VHCI) |

## Pré-requisitos

### No Host Hyper-V

1. Windows 10/11 com Hyper-V habilitado
2. Habilite o switch virtual externo do Hyper-V (para comunicação VM ↔ host)
3. O instalador do Host instala o `usbipd-win` automaticamente
4. Execute o USBRelay Host **como Administrador** (manifest UAC incluso)

### Na VM Convidada

1. Windows 10 x64 (versão 1903+) ou Windows 11 ARM64
2. Garanta que a VM alcance o host pelo IP da rede local (LAN)
3. O instalador do Client instala o `usbip-win2` (driver VHCI + CLI) automaticamente
4. **Porta 3240** (protocolo USB/IP) deve estar liberada no firewall do Host

```powershell
# No HOST, como Administrador:
netsh advfirewall firewall add rule name="USBIP Server" dir=in action=allow protocol=tcp localport=3240
```

## Instalação

### Instaladores NSIS (Recomendado)

Os instaladores estão na pasta `dist/`:

| Instalador | Tamanho | Conteúdo |
|---|---|---|
| `USBRelayHost_Setup.exe` | ~38 MB | Host + usbipd-win (MSI) |
| `USBRelayClient_Setup.exe` | ~65 MB | Client + usbip-win2 (driver VHCI, sem GUI) |

- O instalador do **Host** solicita privilégios de Administrador (UAC) e instala o `usbipd-win` silenciosamente
- O instalador do **Client** instala o driver VHCI do `usbip-win2` sem a GUI nativa (apenas drivers + CLI)
- Se o driver VHCI já estiver instalado, a instalação do USBip é pulada automaticamente

### A Partir do Código-Fonte

```bash
cd usbrelay

# Dependências compartilhadas
pip install pydantic

# Dependências do Host
pip install fastapi uvicorn PyQt6 pyinstaller

# Dependências do Client
pip install httpx PyQt6 pyinstaller

# Executar o Host
python host/main.py

# Executar o Client
python client/main.py
```

### Gerar Instaladores

```bash
# Compilar Host (onedir + UAC admin)
python -m PyInstaller --noconsole --onedir --uac-admin --name USBRelayHost \
  --add-data "host/assets/icon.ico;assets" \
  --add-data "usbipd-install/usbipd-win_5.3.0_x64.msi;usbipd-install" \
  --hidden-import fastapi --hidden-import uvicorn --hidden-import pydantic --hidden-import PyQt6 \
  host/main.py

# Compilar Client (onedir)
python -m PyInstaller --noconsole --onedir --name USBRelayClient \
  --add-data "client/assets/icon.ico;assets" \
  --add-data "usbipd-install/USBip;usbipd-install/USBip" \
  --hidden-import httpx --hidden-import pydantic --hidden-import PyQt6 \
  client/main.py

# Gerar instaladores NSIS
makensis build/installer_host.nsi
makensis build/installer_client.nsi
```

## Guia Rápido

### 1. Instalar e Configurar o Host

- Execute `USBRelayHost_Setup.exe` como Administrador
- O Host detecta todos os dispositivos USB e inicia a API REST na porta `5757`
- Anote o endereço IP da máquina host (ex.: `192.168.1.10`)

### 2. Compartilhar um Dispositivo USB

- Na GUI do Host, localize seu dispositivo USB na tabela
- Clique com o botão direito e selecione **Share**, ou clique em **Share Selected**
- O status do dispositivo muda para "Shared"

### 3. Instalar e Configurar o Client (dentro da VM)

- Execute `USBRelayClient_Setup.exe` na VM convidada
- Vá até a aba **Settings**
- Informe o IP e a porta do Host (padrão: `5757`)
- Clique em **Apply**

### 4. Conectar o Dispositivo

- O Client exibe os dispositivos disponíveis obtidos do Host
- Selecione o dispositivo compartilhado e clique em **Attach Selected**
- O dispositivo USB agora aparece na VM convidada

## Modo de Compartilhamento Permanente

A principal funcionalidade do USB Relay IP é a reconexão automática entre reinicializações — sem etapas manuais após a configuração inicial.

### No Host

1. Clique com botão direito no dispositivo e selecione **"Mark as Always Share"**
2. O dispositivo é salvo na lista de permanentes (identificado por VID/PID)
3. Na próxima inicialização, o Host faz o bind automático do dispositivo

### No Client

1. Clique com botão direito no dispositivo e selecione **"Mark as Always Attach"**
2. O Client aguarda o dispositivo aparecer no estado "Shared"
3. Conecta automaticamente assim que disponível (tenta a cada 3 segundos, até 30s)

### Configuração de Início Automático

- **Host**: Em Settings, habilite "Start with Windows as a Service" (requer NSSM)
- **Client**: Em Settings, habilite "Start with Windows at Logon" (usa o Agendador de Tarefas)

Com ambos ativados, os dispositivos USB reconectam automaticamente após a reinicialização do host e da VM.

## Bandeja do Sistema

- Fechar a janela minimiza para a bandeja (não encerra o programa)
- Clique com botão direito no ícone da bandeja para **Open Window** ou **Quit**
- Cor do ícone: cinza (ocioso) / verde (dispositivo compartilhado/conectado)
- Notificações em balão para eventos dos dispositivos

## Arquitetura

```
Hyper-V Host                              Hyper-V Guest VM
┌──────────────────────────┐               ┌──────────────────────────┐
│ Dispositivo USB          │               │ USBRelay Client          │
│        │                 │               │        │                 │
│        ▼                 │  USB/IP       │        ▼                 │
│   usbipd-win             │◄─────────────►│   usbip-win2 (VHCI)     │
│   (bind/share)           │  Porta 3240   │   (attach/detach)        │
│        │                 │               │        │                 │
│        ▼                 │  HTTP REST    │        ▼                 │
│ USBRelay Host            │◄─────────────►│ USBRelay Client          │
│  ├─ API FastAPI :5757    │               │  ├─ API httpx            │
│  ├─ Device Monitor       │               │  ├─ Device Poller        │
│  ├─ Config Manager       │               │  ├─ Auto-Attach Worker   │
│  └─ GUI PyQt6            │               │  └─ GUI PyQt6            │
└──────────────────────────┘               └──────────────────────────┘
```

## Endpoints da API

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/v1/health` | Status de saúde |
| GET | `/api/v1/devices` | Listar dispositivos USB |
| POST | `/api/v1/devices/{busid}/bind` | Compartilhar um dispositivo |
| POST | `/api/v1/devices/{busid}/unbind` | Descompartilhar um dispositivo |
| POST | `/api/v1/devices/{busid}/permanent` | Marcar como permanente |
| DELETE | `/api/v1/devices/{busid}/permanent` | Remover marcação permanente |
| GET | `/api/v1/config` | Configuração do Host |

Autenticação: Chave de API opcional via cabeçalho `X-API-Key`.

## Solução de Problemas

| Problema | Solução |
|---|---|
| "usbipd-win not found" | Execute o instalador do Host (instala o usbipd-win automaticamente) |
| "Access denied" ao fazer bind | Execute o USBRelay Host como Administrador |
| Client mostra "Offline" | Verifique IP do Host, porta `5757` e conectividade de rede |
| Dispositivo não aparece na VM | Certifique-se de que o dispositivo está no estado "Shared" no Host |
| "VHCI device not found" | Execute o instalador do Client (instala o driver VHCI do usbip-win2) |
| "Connection refused" porta 3240 | Libere a porta 3240 no firewall do Host (veja Pré-requisitos) |
| Auto-attach não funciona | Verifique a lista de dispositivos permanentes em Settings; confirme que o host está acessível |
| Porta já em uso | O USBRelay tenta incrementos (+1): 5758, 5759... |
| Configuração corrompida | Backup salvo como `.bak`; padrões restaurados automaticamente |

## Logs

- Log do Host: `%APPDATA%\USBRelay\usbrelay_host.log`
- Log do Client: `%APPDATA%\USBRelay\usbrelay_client.log`
- Crash log do Client: `%APPDATA%\USBRelay\usbrelay_client_crash.log`
- Máximo de 5 MB por arquivo, 3 backups rotativos (15 MB total)

## Tecnologias

- **Python 3.14** + **PyQt6** (GUI) + **FastAPI** (API REST) + **httpx** (HTTP client)
- **usbipd-win** (host) — compartilhamento USB via protocolo USB/IP
- **usbip-win2** (client) — driver VHCI certificado WHLK para Windows
- **NSIS** — instaladores Windows
- **PyInstaller** — empacotamento em executável (modo onedir)

## Licença

Proprietária — OhMyTech Soluções Digitais
