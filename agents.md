# Agents.md — USB Relay IP

## Propósito do repositório

O **USB Relay IP** é uma aplicação desktop Windows de dois componentes para compartilhar e reconectar dispositivos USB via rede.

- **Host**: roda na máquina onde o USB está fisicamente conectado. Detecta, compartilha, descompartilha e expõe uma API REST.
- **Client**: roda na máquina que vai consumir o USB remoto. Lista os dispositivos do Host e faz attach/detach.
- **shared**: modelos, constantes e internacionalização compartilhados entre Host e Client.

Este arquivo serve como guia operacional para agentes e pessoas que forem trabalhar no projeto.

---

## Estrutura do projeto

```text
usb-relay-ip/
├── host/
│   ├── api/          # FastAPI, rotas e server
│   ├── core/         # usbipd wrapper, monitoramento, config
│   ├── gui/          # interface Qt do Host
│   └── main.py       # entrypoint do Host
├── client/
│   ├── api/          # client HTTP para falar com o Host
│   ├── core/         # polling, attach/detach workers, config
│   ├── gui/          # interface Qt do Client
│   └── main.py      # entrypoint do Client
├── shared/
│   ├── constants.py
│   ├── i18n.py
│   └── models.py
├── tests/
├── README.md
└── _release_notes.md
```

---

## Visão geral da arquitetura

### Host

1. Inicializa a GUI e a API REST.
2. Garante que `usbipd` esteja disponível e ouvindo na porta 3240.
3. Monitora dispositivos USB locais.
4. Expõe endpoints para saúde, listagem, bind/unbind e configurações.
5. Mostra o estado atual na GUI e na bandeja.

### Client

1. Lê a configuração do Host (IP, porta, API key).
2. Faz polling da API do Host.
3. Mantém a lista de dispositivos compartilhados atualizada.
4. Faz attach/detach usando workers em background.
5. Reage ao estado do serviço `usbipd` do Host para evitar falsos positivos.

### Shared

- `shared/models.py`: contratos de dados entre Host e Client.
- `shared/constants.py`: porta padrão, versão e valores comuns.
- `shared/i18n.py`: strings em PT-BR e EN.

---

## Fluxo principal de funcionamento

### Fluxo do Host

- `host/main.py` sobe a interface e a API.
- `host/core/usbipd_wrapper.py` encapsula chamadas para `usbipd` e `sc`.
- `host/core/service_monitor.py` monitora continuamente se a porta 3240 está viva.
- `host/api/routes/health.py` retorna o estado do sistema com mais granularidade.
- `host/gui/main_window.py` reflete o estado do serviço na barra de status e na bandeja.

### Fluxo do Client

- `client/core/device_poller.py` faz polling dos dispositivos e do health do Host.
- `client/api/host_client.py` centraliza as requisições HTTP.
- `client/gui/main_window.py` atualiza a UI e bloqueia attach quando o serviço do Host está indisponível.

---

## Ponto importante do bug corrigido recentemente

O problema principal era: o Host podia exibir a API como “online”, mas o serviço `usbipd` estar parado. Isso gerava `Connection refused` no Client.

### Correções aplicadas

- `usbipd_wrapper.ensure_service_running()` ficou mais robusto.
- O Host agora valida o estado real do serviço antes de anunciar sucesso.
- O Host monitora continuamente a porta 3240.
- O health endpoint passou a expor o estado do serviço `usbipd`.
- O Client passa a reagir ao estado do serviço e evita attach quando o Host está indisponível.
- A GUI mostra claramente quando a API está de pé, mas o `usbipd` não.
- Foram criados testes para o wrapper e para o monitor do serviço.

---

## Regras e convenções para mudanças futuras

### Antes de alterar código

- Ler o arquivo alvo completo antes de editar quando a mudança for estrutural.
- Preferir ajustes pequenos e testáveis.
- Se a alteração tocar Host + Client + shared, revisar os três lados do contrato.

### Ao mexer em saúde/monitoramento

- Não assumir que “API online” significa “usbipd ativo”.
- Sempre diferenciar:
  - API REST rodando
  - serviço `usbipd` disponível
  - porta 3240 escutando
  - dispositivos compartilhados de fato

### Ao mexer em i18n

- Atualizar PT-BR e EN ao mesmo tempo.
- Manter as chaves consistentes com placeholders idênticos.

### Ao mexer em testes

- Cobrir o caminho feliz e o caminho de falha.
- Se a lógica usar debounce/retry/monitoramento, testar também transições de estado.

---

## Execução local

### Host

```bash
python host/main.py
```

### Client

```bash
python client/main.py
```

### Testes

```bash
python -m pytest tests -q
```

---

## Arquivos mais sensíveis

- `host/core/usbipd_wrapper.py`
- `host/core/service_monitor.py`
- `host/api/routes/health.py`
- `host/gui/main_window.py`
- `client/api/host_client.py`
- `client/core/device_poller.py`
- `client/gui/main_window.py`
- `shared/models.py`
- `shared/i18n.py`
- `tests/test_usbipd_wrapper.py`
- `tests/test_service_monitor.py`

---

## Últimas observações de operação

- Mudanças que afetem o estado do serviço devem ser validadas com testes ou revisão manual.
- Se o serviço cair no meio da sessão, a UI precisa informar de forma explícita.
- Se houver nova mensagem de erro de rede ou serviço, ela deve ser refletida no Host e no Client.

---

## Estado atual do branch

Este branch contém a correção do problema de visibilidade do `usbipd` no Host, melhorias de feedback no Client, monitoramento contínuo e documentação operacional para manter o projeto sustentável.