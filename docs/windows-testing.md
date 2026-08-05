# Validação Windows e piloto USB/IP

Este projeto usa duas camadas de validação Windows. Uma execução verde no runner hospedado valida o software e o instalador, mas **não substitui** o piloto com o dispositivo USB físico, o driver VHCI e o Host USB/IP reais.

## 1. CI Windows hospedada

A workflow [Windows Validation](../.github/workflows/windows-validation.yml) é executada em pushes para `master`, `fix/**` e `feature/**`, em pull requests para `master` e manualmente.

Ela executa:

- a suíte completa em `windows-2022` e `windows-2025`;
- `compileall` em `client`, `host`, `shared`, `tests` e `ci`;
- os locks interprocessos reais do Windows por `msvcrt`;
- o script real [`set_shared_acl.ps1`](../build/set_shared_acl.ps1) em um diretório temporário;
- inspeção da DACL por SID:
  - SYSTEM (`S-1-5-18`): Full Control;
  - Administradores (`S-1-5-32-544`): Full Control;
  - usuário do runner: Modify;
  - Users (`S-1-5-32-545`): sem regra explícita de Modify;
- renderização do XML real de autostart;
- criação, execução e remoção de uma tarefa temporária como SYSTEM;
- escrita de um marcador pelo processo SYSTEM em estado protegido por ACL;
- consulta PnP/CIM pelo código de produção.

O script usa um nome de tarefa exclusivo e executa cleanup em `finally`; ele não altera `USBRelayClientBoot`.

A workflow [Build Installers](../.github/workflows/build-installers.yml) instala o NSIS, executa PyInstaller, compila os instaladores Host e Client e publica os `.exe` como artifact com retenção de 14 dias.

## 2. Limites do runner hospedado

O runner hospedado não contém o cenário físico necessário para provar:

- enumeração de um USB remoto real pelo VHCI;
- `usbip-win2 v0.9.7.7` com o dispositivo de produção;
- geração real de `USB\VID_0000&PID_0002` e PnP Code 43;
- associação física do sentinela à sessão original;
- reuso real de porta VHCI durante attach/detach;
- unbind/bind do Host ligado ao dispositivo físico;
- recuperação após reboot que o driver eventualmente exija.

Esses itens pertencem ao piloto self-hosted.

## 3. Preparar o runner self-hosted

Use uma máquina Windows piloto dedicada, na rede do Host, e não uma estação administrativa de uso diário.

Pré-requisitos:

1. Instalar o GitHub Actions Runner como serviço Windows.
2. Garantir que o serviço tenha privilégios locais de Administrador.
3. Registrar estes labels no runner:
   - `self-hosted`;
   - `Windows`;
   - `X64`;
   - `usbip-pilot`.
4. Instalar e validar `usbip-win2 v0.9.7.7` e deixar `usbip.exe` disponível no `PATH`.
5. Confirmar conectividade com a API do Host e com a porta USB/IP.
6. Reservar um dispositivo físico de teste com `busid`, VID e PID conhecidos.
7. No GitHub, criar o Environment `usbip-pilot` e configurar aprovadores obrigatórios.
8. Se a API usa autenticação, criar no Environment o secret `USB_RELAY_PILOT_API_KEY`. Nunca passar a chave como input ou argumento de linha de comando.

> Não habilite a workflow self-hosted para `pull_request`, `pull_request_target` ou pushes automáticos. Código de workflow executado em um runner administrativo tem acesso à máquina e à rede.

## 4. Executar o piloto manual

Abra **Actions → Windows Hardware Pilot → Run workflow** e informe:

- `confirmation`: exatamente `USBIP-PILOT`;
- `host_ip`: IP do Host acessível pelo runner;
- `host_port`: normalmente `5757`;
- `busid`: identidade física exata, por exemplo `1-11`;
- `expected_vid`: VID real com quatro dígitos;
- `expected_pid`: PID real com quatro dígitos.

A workflow rejeita `VID=0000`, `PID=0000`, identidade ausente ou ambígua. Depois da aprovação do Environment, ela:

1. confirma Windows, privilégio administrativo e presença de `usbip.exe`;
2. consulta saúde e inventário do Host;
3. exige exatamente um dispositivo com `busid + VID/PID` esperados;
4. captura as sessões locais antes da operação;
5. executa o ciclo verificado de detach/unbind/bind/attach;
6. revalida estado Host, identidade local e saúde PnP fresca;
7. publica `usbip-pilot-report.json` como artifact, sem armazenar a chave de API.

A execução é destrutiva para a sessão escolhida: o dispositivo será temporariamente desconectado e reconectado.

## 5. Cenários manuais adicionais

Mesmo com o ciclo self-hosted verde, execute separadamente:

- provocar ou capturar um Code 43 real;
- confirmar correlação do sentinela com a sessão persistida;
- testar troca/reuso de porta e garantir que nenhuma sessão alheia seja destacada;
- solicitar shutdown da GUI no meio da recuperação;
- reiniciar Host e Client;
- validar os logs de GUI e SYSTEM;
- instalar e desinstalar os artifacts e confirmar ausência de tarefa/startup residual.

Somente após esses testes físicos uma versão pode ser considerada pronta para produção.
