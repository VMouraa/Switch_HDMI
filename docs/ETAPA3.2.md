# Etapa 3.2 — integração entre estados do PS5 e monitor selecionado

## Escopo e arquitetura

Esta etapa acrescenta um controlador de automação separado dos detectores e
do controle de monitores. A integração só começa pela nova entrada explícita
`python -m switch_hdmi.automation_control` ou pela API Python. O comando antigo
`python -m switch_hdmi` continua consultando estados, sem controlar telas.

Antes de modificar arquivos, foram lidos código, configuração, documentação
da Etapa 3.1 e testes relevantes. A suíte anterior foi executada: **137 testes
aprovados**. Os módulos anteriores, os testes existentes, os documentos das
Etapas 2.2/3.1 e o exemplo de configuração original foram preservados.

| Componente | Responsabilidade |
| --- | --- |
| `StateDetector.read_state()` | Interface existente, usada pelo simulador e pelo detector DDP |
| `StateMonitor` | Leitura e registro dos estados, conforme implementação anterior |
| `AutomationController` | Confirmação, deduplicação, tratamento de OFF e decisões sobre um ID |
| `AutomationRunner` | Laço periódico, conversão de falhas de comunicação para UNKNOWN e parada cooperativa |
| `DisplayController` | Proteções e execução/prévia de controle de tela da Etapa 3.1 |
| `automation_control.py` | Composição explícita dos componentes, validação de opções e logs |

O arquivo `switch_hdmi/automation.py` não contém comandos de Windows nem
instancia backends de hardware. Todas as solicitações passam pela interface
existente `DisplayController.set_enabled()`. O formato do JSON e a fábrica
de detectores não precisaram ser modificados.

## Regras de decisão

| Leitura | Condição | Decisão |
| --- | --- | --- |
| `ON` | Pelo menos duas leituras consecutivas | Solicitar desabilitação do monitor selecionado |
| `REST` | Pelo menos duas leituras consecutivas | Solicitar habilitação do monitor selecionado |
| `UNKNOWN` | Qualquer duração/quantidade | Manter monitor sem ações e descartar confirmação pendente |
| `OFF` sem autoridade | Qualquer duração/quantidade | Manter monitor sem ações e descartar confirmação pendente |
| `OFF` com evidência confiável ou política explícita | Pelo menos duas leituras consecutivas elegíveis | Solicitar habilitação do monitor selecionado |

Uma primeira leitura de `ON` ou `REST` nunca dispara comando. `ON, REST, ON`
não confirma nenhum dos estados. `REST, UNKNOWN, REST` exige mais uma leitura
`REST` para confirmar. O limite é configurável por `--confirmations`, mas
nunca pode ser menor que dois e não aceita booleanos como números.

A última intenção solicitada com sucesso é mantida em memória. Leituras
repetidas do mesmo estado não repetem pedidos, inclusive após uma sequência de
`UNKNOWN` e retorno ao mesmo estado conhecido. Uma transição oposta incompleta
também não força repetição do comando anterior. `ON, ON, REST, REST` solicita
uma desabilitação e uma habilitação. `REST, REST, ON, ON` faz o inverso.

Isso não é uma sincronização contínua contra alterações manuais de layout:
uma intenção repetida continua suprimida. Para reiniciar decisões após revisão
manual, crie uma nova instância ou inicie novamente o processo.

## OFF e falhas de comunicação

O detector DDP continua retornando `ON`, `REST` ou `UNKNOWN`; silêncio não
confirma `OFF`. Não foi adicionado ping, classificação por latência ou
recuperação por tempo de ausência. Timeout, falha de socket e saída inválida
do detector interrompem a confirmação e são tratados como `UNKNOWN` pelo
runner, sem solicitar habilitação ou desabilitação.

Há dois mecanismos explícitos para uma leitura `OFF`:

- API: `observe(PS5State.OFF, off_is_trusted=True)` atesta a leitura recebida de
  uma fonte externa confiável. São necessárias duas leituras elegíveis; uma
  leitura OFF não confiável entre elas descarta a confirmação. O contrato
  `read_state()` sozinho não informa proveniência, portanto o runner não
  atribui essa confiança automaticamente ao simulador ou ao DDP.
- Política: `allow_off_recovery=True`, ou `--allow-off-recovery`, permite
  reativação após OFF confirmado. A opção é desativada por padrão e não se
  aplica a `UNKNOWN`, timeout ou ausência prolongada de resposta.

A marcação de confiança é uma responsabilidade do chamador da API, não uma
prova criptográfica ou um detector físico novo. A política futura de recuperação
por ausência, reservada na Etapa 2.2, continua sem implementação/ativação.

## Proteções e autorização física

`dry_run=True` é o padrão. Na API, selecionar `dry_run=False` sem passar
também `physical_control_authorized=True` é recusado antes de qualquer ação.
Na CLI, `--execute` é a autorização explícita para aquela execução. Essa
opção só é aceita com detector real (`simulation_mode=false`) e `--tool`;
simulador e inventário CSV offline só permitem prévia.

Monitor ID e quantidade de confirmações são fixados na construção do
controlador e expostos apenas para leitura. A integração nunca seleciona por
número, ID curto ou curinga. As verificações da Etapa 3.1 permanecem intactas:
principal, último ativo, ID ambíguo, fonte compartilhada, inventário incompleto,
mudança de topologia e alterações observáveis nos outros monitores.

Um erro de `DisplayController`, inclusive recusa de proteção ou resultado
incerto, suspende a instância e interrompe o laço. Não há repetição automática
ou comando de compensação ao receber `REST` depois da falha. A execução deve
ser revista manualmente antes de um novo início. Uma interrupção durante um
pedido de controle também suspende a instância antes de propagar o sinal.

Nenhum controle físico foi autorizado ou executado para esta entrega.
Os caminhos de autorização nos testes substituem processos e monitores por
mocks ou por um backend que altera apenas objetos Python em memória.

## Configuração e execução de prévia

Instalação e dependências seguem o README: Python 3.10 ou superior, biblioteca
padrão e MultiMonitorTool opcional para inventário ao vivo. O JSON existente
continua aceito integralmente, inclusive o formato legado da Etapa 2.1.
As novas opções de automação pertencem à CLI/API; não acrescente campos
desconhecidos ao JSON antigo.

Foi fornecido `examples/automation-simulation.json` com a sequência:

```text
OFF, ON, ON, ON, REST, REST, REST, OFF
```

Ela usa intervalo de 0,25 segundo e logging relativo ao arquivo de configuração.
Para executar a integração apenas com dados sintéticos:

```powershell
$monitorId = 'MONITOR\SIM0002\{4d36e96e-e325-11ce-bfc1-08002be10318}\0002'
.\.venv\Scripts\python.exe -m switch_hdmi.automation_control --config .\examples\automation-simulation.json --inventory-csv .\tests\fixtures\monitors.csv --monitor-id $monitorId --max-polls 8
```

São esperados dois pedidos confirmados, um para ON e outro para REST. Os
repetidos não geram novos pedidos. Os OFF são ignorados. Como a prévia não
modifica o inventário, a habilitação pode aparecer como `NOOP` no controlador
de telas se o CSV já descreve o monitor como ativo. Isso não significa que a
desabilitação anterior foi executada. Não existe mutação física ou do CSV.

O log do exemplo fica em `logs/automation_simulation.log`. Por padrão, a
CLI usa `logging.file` do JSON, resolvido pelo carregador existente. A opção
`--log-file` substitui esse destino. A primeira consulta é imediata; o intervalo
começa após terminar a leitura/decisão. Uma configuração real pode gastar mais
tempo na consulta DDP por causa de seus timeouts e retentativas.

| Opção | Uso |
| --- | --- |
| `--config` | JSON existente; padrão `config.json` |
| `--monitor-id` | ID completo obrigatório, validado antes de iniciar |
| `--inventory-csv` / `--tool` | Origem de inventário, mutuamente exclusivas e obrigatória uma delas |
| `--dry-run` | Prévia explícita; já é o padrão |
| `--execute` | Autoriza atuação física somente com detector real e ferramenta ao vivo |
| `--confirmations` | Inteiro maior ou igual a dois; padrão dois |
| `--max-polls` | Limite opcional de leituras processadas; deve ser positivo |
| `--allow-off-recovery` | Política explícita somente para OFF confirmado; padrão desativado |
| `--log-file` | Destino de log opcional, relativo ao diretório de execução se não absoluto |

Com PS5 real, a configuração local precisa ter `simulation_mode=false` e
`ps5_ip` preenchido. Primeiro valide inventário e detecção separadamente,
conforme as etapas anteriores. A nova CLI pode fazer prévia com o detector
real e inventário offline, ou com `--tool`, sempre sem `--execute`.
O binário/CSV reais e as mudanças físicas ainda não foram validados nesta entrega.

## Parada e falhas

`AutomationRunner.stop()` ou um `threading.Event` fornecido ao runner solicitam
parada cooperativa. O evento é verificado antes/depois da leitura e antes
de entregar um pedido de controle. A espera entre leituras é interrompível.
Confirmações pendentes são descartadas na parada. `Ctrl+C` é propagado pelo
runner, fecha os handlers de log na CLI e encerra com código `130`.

Uma operação de sistema já em andamento não pode ser desfeita atomicamente
pelo evento de parada. Ela continua sujeita aos timeouts do backend. Se o
controle for interrompido depois de ter começado, seu resultado pode ser
incerto, conforme a Etapa 3.1, e exige inspeção manual.

Ao parar, encerrar por limite ou falhar, não se solicita reativação automática
de saída. O monitor pode permanecer no último estado efetivamente aplicado
numa execução física autorizada. Isso evita um comando adicional sem evidência
ou compensações após erro; uma eventual restauração deve ser manual.

Falhas recuperáveis de comunicação geram warning e `UNKNOWN`, com continuidade
do monitoramento. Erros inesperados do detector encerram a execução com erro.
Falhas de controle encerram com código `2`; término normal/limite retorna `0`.
Os logs INFO registram transições observadas, confirmações pendentes, pedidos,
modo, política OFF e encerramento; falhas incluem contexto. Supressão de
duplicatas tem diagnóstico DEBUG disponível ao configurar o logger pela API.

## Testes e validação

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado final desta entrega: **188 testes aprovados**, sendo 137 anteriores
preservados e 51 novos. Os testes novos cobrem:

- ON → REST, REST → ON, ON → UNKNOWN e UNKNOWN → REST;
- duas ou mais confirmações, oscilações, repetição e retorno ao mesmo estado;
- OFF não confiável, evidência explícita por leitura e política OFF opt-in;
- timeout, falha de comunicação, saída inválida e erro inesperado do detector;
- falhas/resultado incerto do controle, suspensão e ausência de compensação;
- parada antes/depois da leitura, antes do pedido, durante espera e por Ctrl+C;
- proteções originais usando o `DisplayController` real com backend em memória;
- autorização física na API/CLI com dependências simuladas e bloqueio de execução
  com simulador/CSV offline;
- respostas DDP sintéticas passando pelo detector de rede real e pela integração,
  incluindo timeout entre as transições, até o backend de telas em memória.

Nenhum teste exige PS5 ou executável de controle. Nos testes de CLI e integração,
a criação de processos físicos é bloqueada ou substituída. O caso DDP usa
socket simulado, envia somente SRCH e confirma dois comandos apenas em memória.
Para executar só os testes novos:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_automation tests.test_automation_cli -v
```

## Limitações e ponto de parada

A compatibilidade DDP com um PS5 físico, a exportação da ferramenta e as ações
em Windows 11 continuam dependentes de validação com o hardware de destino.
As limitações de identidade, CSV incompleto, drivers, rearranjo de layout e
hotplug da Etapa 3.1 permanecem. Confirmação por duas leituras é uma filtragem
lógica, não uma garantia de ausência de oscilação ou de frescor/autenticidade
criptográfica de uma resposta UDP.

O estado de deduplicação é local à instância e não persiste entre processos.
Uma nova execução exige novas confirmações, mas pode solicitar novamente a
mesma intenção; o `DisplayController` ainda verifica NOOP e todas as proteções.
Não há recuperação temporal, reconciliação contínua com alterações externas,
rollback ou restauração de tela ao sair.

## Arquivos desta entrega

| Situação | Arquivos |
| --- | --- |
| Criados | `switch_hdmi/automation.py`, `switch_hdmi/automation_control.py` |
| Criados | `tests/test_automation.py`, `tests/test_automation_cli.py` |
| Criado | `examples/automation-simulation.json` |
| Criado | `docs/ETAPA3.2.md` |
| Atualizado | `README.md` |

Nenhum módulo de detecção, configuração, monitoramento ou controle de monitores
das etapas anteriores foi modificado. Não houve commit ou push, geração de
executável ou configuração de inicialização automática. A entrega termina na
Etapa 3.2, aguardando revisão, sem avanço para a Etapa 3.3.
