## USB Relay IP v1.0.0

### Release por: Antonio Carlos — OhMyTech Soluções Digitais

---

### Correções recentes no branch dev

- O Host agora valida explicitamente se o serviço `usbipd` está realmente em execução antes de anunciar sucesso.
- Foi adicionado monitoramento contínuo da porta 3240 com tentativa de auto-recuperação.
- O health endpoint passou a expor o estado do serviço `usbipd`.
- O Client passou a reagir ao estado do serviço e evita attach quando o Host está indisponível.
- Foram criados testes para o wrapper do serviço e para o monitoramento contínuo.

---

O USB Relay IP é uma aplicação desktop Windows para compartilhar e reconectar dispositivos USB em cenários locais e remotos. O projeto nasceu para ajudar em ambientes Hyper-V, mas sua proposta é mais ampla: simplificar o acesso a hardware USB sempre que houver uma máquina host e um cliente Windows na rede.

### Destaques

- Compartilhamento e conexão remota de dispositivos USB com interface gráfica
- Suporte a reconexão automática e dispositivos permanentes
- Operação em modo GUI e em segundo plano
- API REST para integração entre host e client
- Suporte a Português (BR) e Inglês

### Uso

1. Instale o componente do host na máquina onde o dispositivo USB está conectado
2. Instale o componente do client na máquina que fará o acesso remoto
3. Configure o endereço da máquina host no client
4. Marque dispositivos como permanentes quando quiser reconexão automática

### Requisitos

- Windows 10/11 (64-bit)
- Conectividade de rede entre as máquinas
- Porta 3240 liberada no firewall do host

### Créditos

- [usbipd-win](https://github.com/dorssel/usbipd-win)
- [usbip-win2](https://github.com/cezanne/usbip-win2)
