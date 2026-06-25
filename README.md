# USB Relay IP

Compartilhamento e conexão remota de USB no Windows — simplificado.

O USB Relay IP é uma aplicação desktop Windows de dois componentes para compartilhar e reconectar dispositivos USB em cenários locais e remotos. Ele nasceu para o uso com Hyper-V, mas também pode atender outros contextos em que seja útil centralizar o acesso a dispositivos USB com uma GUI amigável e uma camada de comunicação REST.

## Componentes

| Componente | Onde Instalar | Finalidade |
|---|---|---|
| **USBRelay Host** | Máquina Windows com o dispositivo conectado | Detecta dispositivos USB, compartilha e expõe API REST |
| **USBRelay Client** | Máquina Windows que vai consumir o dispositivo | Conecta dispositivos USB compartilhados remotamente |

## Pré-requisitos

### No Host Windows

1. Windows 10/11/Windows Server 2019/2022/2025
2. Se estiver usando Hyper-V, habilite o switch virtual externo para comunicação entre máquinas
3. Execute o USBRelay Host **como Administrador** (manifest UAC incluso)

### Na Máquina Cliente

1. Windows 10 x64 (versão 1903+) ou Windows 11 ARM64 ou Windows Server 2019/2022/2025
2. Garanta conectividade de rede com a máquina Host
3. **Porta 3240** deve estar liberada no firewall do Host

```powershell
# No HOST, como Administrador:
netsh advfirewall firewall add rule name="USBIP Server" dir=in action=allow protocol=tcp localport=3240
```

## Instalação

### Instaladores NSIS (Recomendado)

Os instaladores estão na pasta `dist/`:

| Instalador | Tamanho | Conteúdo |
|---|---|---|
| `USBRelayHost_Setup.exe` | ~38 MB | Host |
| `USBRelayClient_Setup.exe` | ~65 MB | Client |

- O instalador do **Host** solicita privilégios de Administrador (UAC)
- O instalador do **Client** prepara o ambiente necessário para conexão remota
- Se algum pré-requisito já estiver presente, a instalação pula a etapa correspondente automaticamente

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

### 3. Instalar e Configurar o Client

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

## Casos de Uso

Além do cenário Hyper-V, o USB Relay IP pode ser útil em situações como:

- Compartilhamento de periféricos entre máquinas Windows em laboratório ou produção controlada
- Acesso remoto a dispositivos USB que precisam permanecer conectados a um host específico
- Ambientes de teste e automação que dependem de reconexão previsível
- Cenários com máquinas físicas e clientes Windows na mesma rede

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


## Últimas correções no branch dev

As alterações mais recentes atacaram um problema real de indisponibilidade do serviço `usbipd` no Host, especialmente visível em Windows Server 2022/2025.

### O que foi corrigido

- O Host agora valida o estado real do serviço `usbipd` antes de anunciar que tudo está OK.
- O monitoramento passou a verificar continuamente a porta 3240 e tenta auto-recuperação quando o serviço cai.
- A barra de status e a bandeja do Host mostram quando a API está online, mas o `usbipd` está parado.
- O Client passou a receber o estado de saúde do serviço e bloqueia attach quando o Host está indisponível.
- O health endpoint agora expõe o estado do serviço `usbipd` com mais detalhes.
- Foram adicionados testes novos para o wrapper do serviço e para o monitoramento contínuo.

### Resultado prático

O Host deixa de dar uma falsa sensação de disponibilidade quando o serviço subjacente está parado. Isso reduz os casos de `Connection refused` no Client e facilita o diagnóstico do problema.

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

## Creditos

Este projeto foi viabilizado por ferramentas de terceiros que tornam o compartilhamento USB no Windows possivel:

- [usbipd-win](https://github.com/dorssel/usbipd-win)
- [usbip-win2](https://github.com/cezanne/usbip-win2)

## Solução de Problemas

| Problema | Solução |
|---|---|
| "componente necessario nao encontrado" | Execute o instalador do Host novamente |
| "Access denied" ao fazer bind | Execute o USBRelay Host como Administrador |
| Client mostra "Offline" | Verifique IP do Host, porta `5757` e conectividade de rede |
| Dispositivo não aparece na VM | Certifique-se de que o dispositivo está no estado "Shared" no Host |
| "componente necessario nao encontrado" | Execute o instalador do Client novamente |
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
- **NSIS** — instaladores Windows
- **PyInstaller** — empacotamento em executável (modo onedir)

## Licença

Este projeto está licenciado sob os termos da licença MIT. Veja o arquivo [LICENSE.txt](LICENSE.txt) para os detalhes completos.
