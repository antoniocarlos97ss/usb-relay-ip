# USBRelay

Passagem USB do host Hyper-V para VMs convidadas — simplificada.

O USBRelay é uma aplicação desktop Windows de dois componentes que encapsula o `usbipd-win` com uma GUI amigável e uma camada de comunicação REST, eliminando a necessidade de interação manual por linha de comando.

## Componentes

| Componente | Onde Instalar | Finalidade |
|---|---|---|
| **USBRelay Host** | Máquina física host Hyper-V | Detecta dispositivos USB e os compartilha via API REST |
| **USBRelay Client** | VM Windows convidada | Conecta dispositivos USB compartilhados dentro da VM |

## Pré-requisitos

### No Host Hyper-V

1. Instale o [usbipd-win](https://github.com/dorssel/usbipd-win/releases) >= 4.0
2. Habilite o switch virtual externo do Hyper-V (para comunicação VM ↔ host)
3. Execute o USBRelay Host **como Administrador**

### Na VM Convidada

1. Instale o [usbipd-win](https://github.com/dorssel/usbipd-win/releases) >= 4.0
2. Garanta que a VM alcance o host pelo IP da rede local (LAN)

## Instalação

### A Partir do Código-Fonte

```bash
# Clone ou baixe o projeto
cd usbrelay

# Instale as dependências do Host
pip install -r requirements-host.txt

# Instale as dependências do Client
pip install -r requirements-client.txt

# Execute o Host
python -m host.main

# Execute o Client
python -m client.main
```

### Gerar .exe Autônomo

```bash
# Instale o PyInstaller
pip install pyinstaller

# Compilar o Host
pyinstaller build/build_host.spec

# Compilar o Client
pyinstaller build/build_client.spec

# Arquivos de saída
# dist/USBRelayHost.exe  (~30-50 MB autônomo)
# dist/USBRelayClient.exe (~30-50 MB autônomo)
```

## Guia Rápido

### 1. Configurar o Host

- Execute `USBRelayHost.exe` como Administrador
- O Host detecta todos os dispositivos USB e inicia a API REST na porta `5757`
- Anote o endereço IP da máquina host (ex.: `192.168.1.10`)

### 2. Compartilhar um Dispositivo USB

- Na GUI do Host, localize seu dispositivo USB na tabela
- Clique com o botão direito e selecione **Share**, ou clique em **Share Selected**
- O status do dispositivo muda para verde "Shared"

### 3. Configurar o Client (dentro da VM)

- Execute `USBRelayClient.exe` na VM convidada
- Vá até a aba **Settings**
- Informe o IP e a porta do Host (padrão: `5757`)
- Clique em **Apply**

### 4. Conectar o Dispositivo

- O Client exibe os dispositivos disponíveis obtidos do Host
- Selecione o dispositivo compartilhado e clique em **Attach Selected**
- O dispositivo USB agora aparece na VM convidada

## Modo de Compartilhamento Permanente

A principal funcionalidade do USBRelay é a reconexão automática entre reinicializações — sem etapas manuais após a configuração inicial.

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
Hyper-V Host                         Hyper-V Guest VM
┌────────────────────┐               ┌────────────────────┐
│ Dispositivo USB    │               │ USBRelay Client    │
│        │           │               │        │           │
│        ▼           │  HTTP REST    │        ▼           │
│   usbipd-win       │◄─────────────►│   usbipd (client)  │
│        │           │  Porta 5757   │                    │
│        ▼           │               │                    │
│ USBRelay Host      │               │                    │
│  ├─ API FastAPI    │               │                    │
│  ├─ Config Manager │               │                    │
│  └─ GUI PyQt6      │               │                    │
└────────────────────┘               └────────────────────┘
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
| "usbipd-win not found" | Instale o usbipd-win >= 4.0 da página de releases do GitHub |
| "Access denied" ao fazer bind | Execute o USBRelay Host como Administrador |
| Client mostra "Offline" | Verifique IP do Host, porta e conectividade de rede |
| Dispositivo não aparece na VM | Certifique-se de que o dispositivo está no estado "Shared" no Host |
| Auto-attach não funciona | Verifique a lista de dispositivos permanentes em Settings; confirme que o host está acessível |
| Porta já em uso | O USBRelay tenta incrementos (+1): 5758, 5759... |
| Configuração corrompida | Backup salvo como `.bak`; padrões restaurados automaticamente |

## Logs

- Log do Host: `%APPDATA%\USBRelay\usbrelay_host.log`
- Log do Client: `%APPDATA%\USBRelay\usbrelay_client.log`
- Máximo de 5 MB por arquivo, 3 backups rotativos (15 MB total)

## Segurança

- Autenticação por chave de API (opcional, texto plano)
- Projetado para ambientes LAN confiáveis
- Sem TLS na v1.0 (melhoria futura)
- Host requer privilégios de Administrador

## Licença

Proprietária — OhMyTech Soluções Digitais
