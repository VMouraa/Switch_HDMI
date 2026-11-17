# Etapa 3.1 — controle manual de monitores no Windows 11

## Resultado e escopo

Foi criado um módulo independente para inventariar monitores, selecionar um
Monitor ID completo e preparar sua habilitação ou desabilitação. `dry_run=True`
é o padrão da API e da linha de comando. A automação permanece desativada:
o detector DDP, o simulador, a política futura de recuperação e a CLI do PS5
não importam nem chamam o módulo de telas.

Antes das alterações, foram inspecionados arquitetura, configuração, testes e
documentação existentes. Os 86 testes anteriores foram preservados integralmente.
Toda a suíte atual passou: **137 testes, sem falhas**, em Windows com Python
3.13.1. Os testes de controle de telas usam inventários sintéticos e processos
simulados; nenhuma operação real de habilitar/desabilitar foi executada.
Não foi baixado, executado ou gerado um binário de controle de monitores.

## Escolha do backend

Foram avaliadas duas opções:

| Opção | Avaliação e decisão |
| --- | --- |
| MultiMonitorTool | O autor documenta suporte ao Windows 11, comandos por Monitor ID e exportação de inventário. Foi escolhido um adaptador pequeno, substituível e sem descoberta automática do executável. |
| APIs nativas | `EnumDisplayDevicesW` permite enumeração e obtenção de nomes de interface de monitor. `QueryDisplayConfig`/`SetDisplayConfig` permitem trabalhar com caminhos e modos; exigiriam gerenciar a topologia completa, incluindo layouts e drivers. Um backend nativo poderá implementar a mesma interface depois de validação específica. |

Referências primárias: [MultiMonitorTool](https://www.nirsoft.net/utils/multi_monitor_tool.html),
[EnumDisplayDevicesW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaydevicesw),
[QueryDisplayConfig](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-querydisplayconfig)
e [SetDisplayConfig](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setdisplayconfig).

A decisão mantém o código de aplicação independente da ferramenta. O pacote
Python usa somente a biblioteca padrão. O MultiMonitorTool é uma dependência
externa opcional para inventário ao vivo e execução manual: não é incluído,
instalado, procurado no PATH ou baixado automaticamente. O caminho absoluto
de um `MultiMonitorTool.exe` existente precisa ser fornecido explicitamente.
Verifique a origem do binário na página oficial e seus termos de distribuição.

O autor limita a utilidade da ferramenta a áreas de trabalho estendidas e
alerta para possível reposicionamento ao reabilitar monitores em certas
configurações com três ou mais telas. Isso é um risco conhecido do backend,
não uma garantia de que posições serão preservadas pela ferramenta.

## Arquitetura

| Arquivo | Responsabilidade |
| --- | --- |
| `switch_hdmi/displays.py` | Modelo imutável `DisplayInfo`, interface `DisplayBackend`, validação e controlador seguro `DisplayController` |
| `switch_hdmi/multimonitor_tool.py` | Parser de CSV, backend de inventário offline e adaptador de processo do MultiMonitorTool |
| `switch_hdmi/display_control.py` | CLI manual independente, com listagem, habilitação e desabilitação em prévia por padrão |

`switch_hdmi/monitor.py` continua sendo o monitor dos **estados do PS5**;
ele não controla monitores de vídeo e não foi modificado nesta etapa.
`config.json`, a interface `read_state()` e os formatos de configuração das
Etapas 2.1 e 2.2 permanecem inalterados. Não há configuração nova obrigatória.

### Inventário e identificadores

Cada entrada contém:

- `monitor_id`: Monitor ID completo, quando disponível;
- `device_name`: nome GDI, como `\\.\DISPLAY2`, somente para diagnóstico e
  detecção de origem compartilhada;
- `friendly_name` e `serial_number`, se fornecidos pelo inventário;
- `active`, `primary` e `connected` como booleanos;
- resolução, posição inicial/final, orientação, frequência e profundidade de cor
  exportadas, para comparar o estado dos outros monitores.

O módulo lista inclusive monitores conectados e inativos; entradas desconectadas
presentes no inventário também aparecem com `connected=false`. Não se presume
que todo registro do Windows representa um monitor fisicamente conectado.
Um ID ausente é representado por `null` e não pode ser usado para controlar tela.

A seleção aceita **somente correspondência integral do Monitor ID completo**,
sem distinção de maiúsculas. Não aceita número, `DISPLAY2`, `Primary`, `All`,
ID curto de modelo, serial isolado, listas ou curingas. Dois monitores do mesmo
modelo podem compartilhar ID curto e até serial; esses campos não são chaves
de seleção neste módulo. A numeração GDI pode mudar sem impedir a seleção
pelo Monitor ID; uma mudança durante a operação, porém, faz a revalidação falhar.

O Monitor ID é uma identidade da instalação do Windows, relativamente estável
nessa configuração. Não é uma garantia de identidade física universal: troca de
porta, dock, driver ou reinstalação pode mudar os IDs. Liste novamente após
essas mudanças. O CSV sintético usa IDs `SIM...` e não identifica dispositivos
reais do computador.

### Formato de inventário

O adaptador solicita uma exportação nova para um arquivo temporário exclusivo,
com `/HideInactiveMonitors 0 /scomma <arquivo>`. Não reutiliza relatório antigo
em caso de falha. O diretório temporário é removido ao terminar.

O parser exige cabeçalhos em inglês, booleanos `Yes`/`No` e estas colunas:
`Name`, `Monitor ID`, `Monitor Name`, `Monitor Serial Number`, `Active`,
`Primary`, `Disconnected`, `Resolution`, `Left-Top`, `Right-Bottom`,
`Orientation`, `Frequency`, `Colors`. Colunas adicionais são aceitas; cabeçalhos
duplicados, colunas obrigatórias ausentes e linhas malformadas são rejeitados.
Codificações aceitas: UTF-8, UTF-8 com BOM e UTF-16 com BOM.

Versões traduzidas, configuração que oculte colunas ou outro formato de saída
podem ser incompatíveis. O aplicativo apresenta erro em vez de adivinhar
estados. O esquema foi testado com dados sintéticos e ainda precisa ser
conferido com uma exportação real da versão da ferramenta instalada.

## Proteções e comportamento das operações

Todas as chamadas públicas de controle devem passar por `DisplayController`.
O adaptador é uma camada interna de execução, sem substituir as validações
do controlador. Exemplo da API, que apenas registra uma prévia:

```python
controller.disable(monitor_id)  # dry_run=True
controller.enable(monitor_id)   # dry_run=True
```

As verificações também se aplicam às prévias:

1. Validar o formato do ID e exigir uma correspondência única no inventário.
2. Recusar IDs duplicados, monitores conectados sem identificação suficiente,
   flags inconsistentes e falta de dados de layout em monitores ativos.
3. Recusar origens GDI compartilhadas entre registros conectados; duplicação
   de tela não é um cenário suportado.
4. Recusar monitor selecionado desconectado.
5. Impedir a desativação do último monitor ativo.
6. Bloquear ambas as operações sobre o monitor principal e exigir exatamente
   um principal ativo no inventário, sem opção de ignorar essa proteção.
7. Registrar `NOOP` se o estado desejado já estiver presente; não executar comando.
8. Em `dry_run`, registrar `DRY_RUN` e retornar sem chamar o executor.

Somente uma chamada manual com `dry_run=False`, ou a opção CLI `--execute`,
habilita a execução real de **uma** ação. Isso não ativa automação ou vínculo
com o PS5. Antes de executar, o controlador consulta o inventário novamente,
repete as proteções e exige que a topologia completa esteja igual à anterior.
A ordem das linhas pode mudar; mudanças de estado, identidade ou layout abortam.

O único comando de alteração preparado pelo adaptador é `/enable <ID>` ou
`/disable <ID>`, com exatamente um ID completo como argumento separado. Não
usa shell, comandos de principal, operações globais, perfis de layout, DDC/CI,
energia do monitor ou alternância cega de estado. Habilitar/desabilitar aqui
significa anexar/desanexar a tela da área de trabalho do Windows; não trocar
a entrada HDMI ou cortar a alimentação elétrica do aparelho.

Depois do comando, há nova consulta: o alvo deve atingir o estado solicitado,
e todos os demais registros devem permanecer iguais, incluindo principal,
resolução, posição, frequência e orientação. Retorno zero do processo sozinho
não é considerado sucesso. O resultado só contém `changed=true` e
`verified=true` após essas verificações.

O processo tem timeout de 10 segundos por padrão, configurável para um valor
maior que zero e até 60 segundos exclusivamente pela API. Janelas do
processo são ocultadas. Se houver falha, timeout, interrupção durante execução
ou divergência na verificação, o resultado é tratado como **incerto**: a ação
pode ter sido aplicada. O erro exige revisão manual; não há repetição de comando
nem rollback automático que possa sobrescrever uma alteração externa.

O backend de CSV offline nunca executa ações, mesmo se chamado diretamente.
A CLI também rejeita `--execute` junto de `--inventory-csv` antes de iniciar
a operação.

## Instruções de validação sem dispositivos físicos

Na pasta do projeto, use o Python do ambiente existente. Não são necessários
PS5, MultiMonitorTool instalado ou acesso às configurações de vídeo:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado desta entrega: `Ran 137 tests ... OK` — **86 anteriores + 51 novos**.

Os novos testes cobrem seleção e renumeração, IDs ausentes/ambíguos, principal,
último ativo, monitor desconectado, fontes compartilhadas, dados incompletos,
prévia, operações idempotentes, revalidação após hotplug, verificação dos outros
monitores, falhas e interrupções, parser CSV e codificações, argumentos exatos
do processo, timeout, limpeza de temporários e CLI. Todos os caminhos de
execução real nos testes usam processos simulados; não há chamada à ferramenta.

Para testar somente o módulo novo:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_displays tests.test_multimonitor_tool tests.test_display_cli -v
```

Para visualizar o inventário **sintético**, com um principal, um secundário
ativo e uma tela de reserva inativa:

```powershell
.\.venv\Scripts\python.exe -m switch_hdmi.display_control --inventory-csv .\tests\fixtures\monitors.csv list
```

Para registrar uma prévia de desabilitação e outra de habilitação, sem executar:

```powershell
.\.venv\Scripts\python.exe -m switch_hdmi.display_control --inventory-csv .\tests\fixtures\monitors.csv disable --monitor-id 'MONITOR\SIM0002\{4d36e96e-e325-11ce-bfc1-08002be10318}\0002'
.\.venv\Scripts\python.exe -m switch_hdmi.display_control --inventory-csv .\tests\fixtures\monitors.csv enable --monitor-id 'MONITOR\SIM0003\{4d36e96e-e325-11ce-bfc1-08002be10318}\0003'
```

O JSON de resposta informa `dry_run=true`, `changed=false` e `verified=false`:
o estado não foi alterado nem foi feita verificação de aplicação de comando.
Solicitar o ID `SIM0001` para desabilitação deve retornar erro por ser principal.
Os logs ficam em `logs/monitor_control.log`, em UTF-8, acrescentados a cada
execução. `--log-file <caminho>` permite escolher outro arquivo.

## Validação posterior do inventário no Windows 11

No PC de destino, obtenha manualmente o MultiMonitorTool na página oficial,
use a interface em inglês e deixe disponíveis as colunas exigidas. Use área
de trabalho estendida e confira se todas as telas esperadas estão presentes
no inventário. Para listar **sem habilitar ou desabilitar**:

```powershell
.\.venv\Scripts\python.exe -m switch_hdmi.display_control --tool 'C:\Ferramentas\MultiMonitorTool\MultiMonitorTool.exe' list
```

O resultado é JSON: as barras invertidas aparecem escapadas. Para exibir os
IDs sem os escapes do JSON e facilitar a cópia, também é possível usar:

```powershell
$monitores = .\.venv\Scripts\python.exe -m switch_hdmi.display_control --tool 'C:\Ferramentas\MultiMonitorTool\MultiMonitorTool.exe' list | ConvertFrom-Json
$monitores | Select-Object monitor_id, friendly_name, serial_number, active, primary, connected | Format-List
```

Copie o `monitor_id` completo da tela pretendida. Compare nome, serial,
estado e principal com o hardware antes de planejar ações. Para uma prévia:

```powershell
.\.venv\Scripts\python.exe -m switch_hdmi.display_control --tool 'C:\Ferramentas\MultiMonitorTool\MultiMonitorTool.exe' --dry-run disable --monitor-id '<Monitor ID completo copiado>'
```

Substitua o texto entre `<...>` pelo ID real. Todas as opções globais, como
`--tool`, `--dry-run` e `--log-file`, ficam antes de `list`, `enable` ou `disable`.
Na prévia, a ferramenta só é chamada para exportar o inventário; os comandos
`/enable` e `/disable` não são chamados. O arquivo de configuração do detector
não participa dessas operações.

O código oferece execução manual explícita via `--execute` ou
`DisplayController.enable/disable(..., dry_run=False)`, mas **essa execução
física não foi validada nesta entrega**. Os procedimentos acima permanecem em
listagem e prévia. A automação continua desativada independentemente da presença
do executável no computador.

Códigos de saída: `0` para listagem, prévia, NOOP ou operação verificada;
`2` para recusa, falha ou resultado incerto; `130` para interrupção de
consulta/prévia antes de executar uma ação. O log distingue esses casos.

## Limitações e possíveis riscos

- A integração com o binário e o CSV reais ainda precisa de validação em
  Windows 11, com os drivers e a versão específica da ferramenta do PC.
- O inventário deve estar completo. Colunas ausentes são detectadas, mas não
  é possível comprovar apenas pelo CSV que a ferramenta não omitiu um registro.
  Duplicação de telas, sessões remotas, docks e drivers virtuais exigem revisão.
- O módulo solicita apenas o ID selecionado. Windows, drivers ou a ferramenta
  podem ainda reorganizar janelas/layouts como consequência da mudança. A
  verificação detecta divergências observáveis; ela não desfaz efeitos já ocorridos.
- A revalidação reduz o risco de hotplug, mas não cria uma transação atômica com
  o Windows. Uma desconexão externa após a última consulta ainda pode ocorrer.
  As proteções de último ativo e principal usam o inventário verificado mais recente.
- A verificação é imediata; uma mudança assíncrona pode ainda não estar
  estabilizada. Um resultado incerto não deve ser reinterpretado como falha
  sem efeito ou usado para repetir o comando cegamente.
- O Monitor ID pode mudar; não há seleção por similaridade ou fallback por número.
- Há crescimento contínuo de log, sem rotação, e o inventário contém identificadores
  e números de série locais. O limite de timeout se aplica a cada processo.

## Relatório de arquivos

| Situação | Arquivos |
| --- | --- |
| Criados | `switch_hdmi/displays.py`, `switch_hdmi/multimonitor_tool.py`, `switch_hdmi/display_control.py` |
| Criados | `tests/test_displays.py`, `tests/test_multimonitor_tool.py`, `tests/test_display_cli.py`, `tests/fixtures/monitors.csv` |
| Criado | `docs/ETAPA3.1.md` |
| Atualizado | `README.md` |

Nenhum arquivo anterior de código ou teste das Etapas 2.1/2.2 foi alterado.
Não foi configurada inicialização automática nem gerado executável. Esta
entrega termina na Etapa 3.1, sem integração PS5/telas e sem avanço para a 3.2.
