# PS5 Auto Switch

Projeto em Python para consultar o estado de um PS5 e desenvolver, em etapas,
o controle de monitores no Windows 11. A versão atual corresponde à **Etapa 3.2**:
há um simulador, um detector DDP, controle manual de monitores e uma integração
opcional com confirmação de estados. **A integração só inicia por comando
explícito e usa `dry_run=True` por padrão.**

O exemplo de configuração inicia em simulação, sem consultar a rede. O controle
manual ou integrado de monitores usa `dry_run=True` por padrão: registra a intenção sem
habilitar ou desabilitar dispositivos. Não há inicialização automática do
Windows, instalador ou executável do projeto.

## Funcionalidades e validação

| Funcionalidade | Implementação | Validação atual |
| --- | --- | --- |
| Simulador `ON` / `REST` / `OFF` | Sequência configurável, repetição ou retenção do último estado | Testes automatizados |
| Detector de rede DDP por UDP | Consulta dirigida ao IPv4 do PS5; `200 → ON`, `620 → REST` | Respostas de rede simuladas; PS5 físico pendente |
| Falhas de detecção | Timeout, erro e resposta inválida produzem `UNKNOWN` | Testes automatizados; não infere `OFF` por silêncio |
| Configuração e monitoramento | JSON validado, consulta periódica e logs de transições | Testes automatizados; formato da Etapa 2.1 preservado |
| Histórico de observações | Última leitura válida e duração de sequências de `UNKNOWN` | Testes com relógio simulado |
| Recuperação após ausência prolongada | Somente contrato para política futura e histórico passivo | Nenhuma política ou ação de recuperação ativa |
| Inventário de monitores | IDs completos, nomes, seriais, estado e layout via adaptador MultiMonitorTool | CSV e processos simulados; exportação real pendente |
| Habilitar/desabilitar monitor manualmente | Seleção por ID completo, `dry_run`, proteção do principal e do último ativo, revalidação e verificação | Monitores simulados; operações físicas pendentes |
| Integração PS5 → monitor selecionado | ON solicita desabilitação e REST habilitação após pelo menos duas leituras; UNKNOWN não executa ações; duplicatas suprimidas | Testes simulados de ponta a ponta; hardware real pendente |
| Recuperação por OFF | Requer fonte confiável por leitura ou política OFF explícita; desativada por padrão | Testes simulados; não se aplica a timeout/UNKNOWN |

O detector real não confirma `OFF`: a ausência de resposta também pode ser
causada por rede, firewall ou opções de repouso. Ele não autentica, pareia,
acorda ou controla o console e não usa ping ou latência para distinguir estados.
Uma resposta `ON` não comprova que alguém está jogando ou que a entrada HDMI
está selecionada.

## Tecnologias

- Python **3.10 ou superior**, com validação executada em Windows/Python 3.13.1.
- Biblioteca padrão: `socket`, `json`, `logging`, `dataclasses`, `typing`,
  `subprocess` e `unittest`; não há pacotes Python externos a instalar.
- Protocolo DDP/UDP para a consulta local do PS5.
- [MultiMonitorTool](https://www.nirsoft.net/utils/multi_monitor_tool.html) como
  dependência externa opcional para inventário ao vivo e ações manuais no Windows.
  O binário não é distribuído, baixado ou localizado automaticamente no PATH.

## Estrutura e arquitetura

```text
PS5-Auto-Switch/
├── config.example.json       # Exemplo público, sem IP do console
├── README.md
├── docs/
│   ├── ETAPA2.2.md           # Detector DDP, decisões e validação no PC
│   ├── ETAPA3.1.md           # Controle manual, proteções e limitações
│   └── ETAPA3.2.md           # Integração, confirmação e autorização física
├── examples/
│   └── automation-simulation.json # Demonstração segura da integração
├── switch_hdmi/              # Nome do pacote Python preservado
│   ├── __main__.py           # CLI de estados do PS5
│   ├── config.py             # Validação e compatibilidade de configurações
│   ├── detector.py           # StateDetector.read_state(), enum e simulador
│   ├── factory.py            # Seleção entre simulação e rede
│   ├── network_detector.py   # Consulta DDP, timeout e retentativas
│   ├── monitor.py            # Monitor periódico dos estados do PS5
│   ├── observations.py       # Histórico passivo imutável
│   ├── recovery.py           # Contrato de política futura, sem execução
│   ├── displays.py           # Modelo e controlador manual de monitores
│   ├── multimonitor_tool.py  # Adaptador e parser de inventário CSV
│   ├── display_control.py    # CLI manual de monitores, separada do PS5
│   ├── automation.py         # Decisões e laço de automação
│   └── automation_control.py # Entrada explícita para a integração
└── tests/
    ├── fixtures/monitors.csv # Inventário sintético, sem IDs do PC
    └── test_*.py
```

Os detectores compartilham `StateDetector.read_state() -> PS5State`. O monitor
de estados depende dessa interface. O controle de telas usa outra interface,
`DisplayBackend`, com validações em `DisplayController`. A integração é composta
separadamente por `AutomationController` e `AutomationRunner`; o comando antigo
de consulta e o controle manual mantêm seu funcionamento independente.

## Instalação

Clone ou extraia o código e abra o PowerShell na pasta raiz. Prepare o ambiente
e a configuração local **antes de executar o aplicativo ou os testes**:

```powershell
py -3 -m venv .venv
if (-not (Test-Path -LiteralPath .\config.json)) {
    Copy-Item -LiteralPath .\config.example.json -Destination .\config.json
}
```

Não é necessário ativar o ambiente virtual nem executar `pip install`.
O `config.json` é local e fica fora do Git; ele é preservado se já existir.
O arquivo publicado é `config.example.json`, com `simulation_mode=true`,
`ps5_ip=null` e caminhos de log relativos. `0.0.0.0` indica escolha da interface
local pelo sistema e não identifica um PC específico.

## Executar o simulador

```powershell
# Sequência OFF → ON → REST → ON → OFF, encerrando após cerca de 8 segundos.
.\.venv\Scripts\python.exe -m switch_hdmi --max-polls 5

# Uma consulta.
.\.venv\Scripts\python.exe -m switch_hdmi --once

# Monitoramento contínuo; Ctrl+C encerra.
.\.venv\Scripts\python.exe -m switch_hdmi
```

Os logs ficam em `logs/ps5_detector.log`. Consultas sem mudança de estado
não acrescentam transições duplicadas. A configuração é carregada uma vez;
reinicie o processo depois de editar o JSON.

## Configuração e consulta real do PS5

| Campo | Uso |
| --- | --- |
| `simulation_mode` | `true` usa o simulador; `false` usa DDP; se omitido no formato novo, assume `true` |
| `ps5_ip` | IPv4 literal do console; obrigatório no modo real e `null` permitido em simulação |
| `check_interval_seconds` | Espera positiva entre o término de uma consulta e o início da próxima |
| `network` | Porta DDP, timeout, retentativas e endereço/porta locais, com valores validados |
| `simulator.states` / `repeat` | Sequência de estados e comportamento após o último item |
| `logging.file` | Caminho do log, relativo à pasta do JSON quando não absoluto |

Todos os campos mostrados no exemplo, exceto `simulation_mode`, são obrigatórios.
Campos desconhecidos são rejeitados. O formato legado completo com
`mode: "simulator"` e `poll_interval_seconds` continua aceito; não misture os
formatos.

Para validar o DDP com hardware, edite somente sua configuração local e siga
[o roteiro da Etapa 2.2](docs/ETAPA2.2.md). Um arquivo alternativo local pode
ser usado com `--config .\config.local.json`. Esses arquivos também são
ignorados pelo Git. Os IPs privados presentes nos testes e nos exemplos da
documentação são dados sintéticos, não endereços descobertos de dispositivos.

## Controle manual de monitores

Para listar os três monitores **sintéticos** fornecidos, sem ferramenta externa
e sem alterar dispositivos:

```powershell
.\.venv\Scripts\python.exe -m switch_hdmi.display_control --inventory-csv .\tests\fixtures\monitors.csv list
```

O módulo seleciona somente pelo Monitor ID completo, nunca por número, apelido,
ID curto ou curinga. Bloqueia o monitor principal, o último ativo e inventários
inseguros. A API `enable`/`disable` e a CLI usam `dry_run` por padrão; o inventário
offline nunca permite alterações físicas.

Há um caminho de execução manual explícita por `--execute` ou `dry_run=False`.
Ele pode alterar dispositivos físicos e não foi validado com hardware nesta
entrega. O backend exige um caminho absoluto de MultiMonitorTool fornecido
pelo usuário. Consulte [a Etapa 3.1](docs/ETAPA3.1.md) para inventário ao vivo,
prévia, requisitos de CSV, proteções e riscos de mudanças de layout/hotplug.

## Integração PS5 e monitor (Etapa 3.2)

Para executar a integração apenas com estados e monitores sintéticos:

```powershell
$monitorId = 'MONITOR\SIM0002\{4d36e96e-e325-11ce-bfc1-08002be10318}\0002'
.\.venv\Scripts\python.exe -m switch_hdmi.automation_control --config .\examples\automation-simulation.json --inventory-csv .\tests\fixtures\monitors.csv --monitor-id $monitorId --max-polls 8
```

O exemplo contém leituras ON e REST repetidas para validar a confirmação. As
duas intenções são registradas em `logs/automation_simulation.log`; nenhuma
alteração física ou do CSV é executada. O exemplo padrão antigo continua
válido; nele ON/REST isolados não confirmam ações.

`UNKNOWN` interrompe confirmações e mantém a tela sem ações. OFF também é
ignorado por padrão; `--allow-off-recovery` é uma política explícita somente
para OFF confirmado e nunca transforma timeout em OFF. `--confirmations` pode
aumentar o limite, mas não reduzi-lo abaixo de dois.

O controle físico exige `--execute`, detector real e `--tool`; é bloqueado com
simulador ou CSV offline. As proteções de principal, último ativo e IDs
ambíguos permanecem. Uma falha de controle suspende a execução sem repetir
ou compensar comandos. Ctrl+C encerra e fecha o log, sem reativação automática
na saída. A atuação com hardware real ainda não foi validada.

Veja regras, opções, API de parada e limitações em [ETAPA3.2.md](docs/ETAPA3.2.md).

## Executar os testes

Depois de preparar o `config.json` local:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado da Etapa 3.2: **188 testes aprovados**.
Os testes usam arquivos temporários, sockets e processos simulados. Não exigem
PS5, MultiMonitorTool instalado ou acesso às configurações de vídeo, e não
habilitam/desabilitam monitores físicos. Os 86 testes das etapas do detector
e os 51 testes de monitores permanecem preservados, com 51 testes novos
de automação e integração, incluindo DDP simulado até telas em memória.

## Documentação e publicação

- [Etapa 2.2](docs/ETAPA2.2.md): protocolo, histórico passivo e validação do PS5.
- [Etapa 3.1](docs/ETAPA3.1.md): backend de monitores, proteções e limitações.
- [Etapa 3.2](docs/ETAPA3.2.md): integração, confirmações, falhas e parada segura.

O `.gitignore` exclui ambientes, caches, build/dist, logs, binários temporários,
configurações locais, inventários locais e arquivos comuns de segredos. Publique
o exemplo de configuração e os dados sintéticos; mantenha IPs reais e números
de série do seu PC nos arquivos locais ignorados. A documentação histórica das
etapas foi preservada: onde ela menciona `config.json`, use a cópia local criada
na instalação acima.

O projeto permanece na Etapa 3.2, aguardando revisão. A Etapa 3.3, a inicialização
com Windows e a geração de executável não foram iniciadas. Commit e push não
são realizados automaticamente.
