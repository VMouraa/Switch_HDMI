# Etapa 2.2 — consulta local de estado do PS5

## Entrega e resultado

Implementação do detector DDP separado do simulador, com a simulação como
padrão. O aplicativo faz consultas e registra estados; não autentica, não
pareia, não envia comandos de energia ou controle, não altera monitores,
não configura inicialização automática e não gera executável.

Antes de modificar arquivos, foram lidos o código, a configuração, o README e
os testes existentes, e executada a suíte da Etapa 2.1: **34 testes aprovados**.
Não havia detector de rede nem módulo de controle de telas no projeto.
A sequência, repetição, retenção do último estado e testes comportamentais do
simulador foram preservados. O formato antigo do JSON continua aceito.

Após esta continuação: **86 testes aprovados**, em Windows com Python 3.13.1,
incluindo testes de regressão, parser DDP, transporte UDP simulado, configuração,
seleção de detector, logs e linha de comando. Não foi utilizado um PS5 físico
nem enviado tráfego de descoberta para a LAN durante esses testes.

## Pesquisa e decisão técnica

Foi avaliada a parte de descoberta do protocolo Remote Play em implementações
públicas, em vez de usar ping ou inferências por latência:

- O [cabeçalho de descoberta do Chiaki-ng](https://github.com/chiaki-ng/chiaki-ng/blob/main/lib/include/chiaki/discovery.h)
  define a porta UDP `9302` e a versão DDP `00030010` para PS5.
- O [código de descoberta do Chiaki-ng](https://github.com/chiaki-ng/chiaki-ng/blob/main/lib/src/discovery.c)
  separa busca de comandos de energia e interpreta `200` como pronto e `620`
  como repouso. Os demais códigos ficam desconhecidos.
- O [módulo DDP do pyremoteplay](https://github.com/ktnrg45/pyremoteplay/blob/master/pyremoteplay/ddp.py)
  também separa consulta de autenticação/controle, com esses códigos de estado.

A implementação deste projeto usa apenas a biblioteca padrão. Foi escolhida
uma consulta dirigida ao IP configurado, evitando adicionar uma biblioteca
com funcionalidades de autenticação, streaming e controle que não são
necessárias nesta etapa. O pacote enviado é somente:

```text
SRCH * HTTP/1.1
device-discovery-protocol-version:00030010
```

O texto termina com uma quebra de linha LF. Não são enviados credenciais,
`WAKEUP`, `LAUNCH`, HTTP por TCP ou ICMP. Não há broadcast nem varredura de rede.
Trata-se de compatibilidade baseada em implementações públicas de terceiros,
não de uma API pública de estado garantida pela Sony. A consulta precisa ser
confirmada com o console e a rede de destino.

## Semântica dos estados

| Observação | Resultado do detector real |
| --- | --- |
| Resposta válida de PS5, código DDP `200` | `ON` |
| Resposta válida de PS5, código DDP `620` | `REST` |
| Timeout ou nenhuma resposta | `UNKNOWN` |
| Erro de socket, firewall, porta local ocupada ou rede indisponível | `UNKNOWN` |
| Resposta malformada, versão incompatível, console de outro tipo ou código desconhecido | `UNKNOWN` se não chegar resposta válida no prazo |
| Pacote vindo de outro IP ou porta | Ignorado; `UNKNOWN` se não houver resposta válida no prazo |
| Console fisicamente desligado, sem resposta | `UNKNOWN` |

**Este detector real não emite `OFF`.** Nos códigos DDP avaliados não há
confirmação confiável de desligamento. Esse é um limite desta fonte de dados:
o silêncio também pode indicar isolamento de rede ou serviço indisponível.
Mesmo vários timeouts consecutivos continuam sendo `UNKNOWN`. Uma resposta
com texto “Off” ou um código não reconhecido também não comprova `OFF`.
`OFF` continua disponível normalmente no simulador da Etapa 2.1.

Uma falha após `ON` ou `REST` atualiza o monitor para `UNKNOWN`; o estado
anterior não é apresentado como se ainda fosse conhecido. Uma resposta válida
posterior permite recuperar `ON` ou `REST`.

## Arquitetura e transporte

- `StateDetector.read_state() -> PS5State` é a interface comum já existente.
- `SimulatedPS5Detector` mantém a sequência e as regras da Etapa 2.1.
- `NetworkPS5Detector` envia SRCH e consulta o PS5 por IPv4/UDP.
- `create_detector()` seleciona a implementação por `simulation_mode`.
- `StateMonitor` mantém a leitura periódica e o registro de mudanças; identifica
  no log se a origem é simulação ou rede/DDP.

Cada tentativa abre e fecha seu próprio socket, inclusive em erros ou Ctrl+C.
A resposta precisa vir do IP e da porta configurados e anunciar a versão DDP
de PS5. Se houver `host-type`, ele deve ser `PS5`. O parser aceita LF, CRLF,
nomes de cabeçalho sem distinção de maiúsculas, campos adicionais e NUL final.
Rejeita conteúdo inválido, cabeçalhos duplicados, caracteres de controle
incompatíveis com o formato aceito e mensagens acima de 8192 bytes. Tabulações
e quebras de linha válidas continuam aceitas, assim como NUL apenas ao final.

O timeout usa um prazo monotônico por tentativa; pacotes inválidos ou alheios
não reiniciam esse prazo. `retries` conta tentativas adicionais. Com os padrões,
uma consulta sem resposta gasta aproximadamente até `2 × 1,5 = 3` segundos de
espera UDP, além do custo do sistema e logs. A primeira consulta é imediata;
o intervalo configurado começa depois do fim da consulta. Ctrl+C encerra o
laço e fecha o log; a reação depende também do retorno da operação de socket,
que tem timeout limitado.

O log inclui início/fim, estado inicial, transições, respostas DDP válidas e
avisos de falha. Os diagnósticos de rede podem aparecer a cada consulta;
transições só são registradas quando o estado muda. Não são gravados nomes
de usuário, credenciais ou o conteúdo bruto das respostas.

## Preparação para recuperação futura — sem ativação

Nesta continuação foi adicionado um histórico **passivo**, separado do detector:
`StateHistory`, em `switch_hdmi/observations.py`. Após cada leitura válida da
interface, o monitor expõe `monitor.observation`, um `StateObservation` imutável
com:

- `state`: o resultado atual, inclusive `UNKNOWN`;
- `observed_at`: instante monotônico da conclusão da leitura;
- `last_known_state` e `last_known_at`: última leitura diferente de `UNKNOWN`
  e seu instante, usados apenas como histórico;
- `unknown_since`: instante da primeira leitura da sequência atual de `UNKNOWN`;
- `consecutive_unknown`: quantidade de consultas consecutivas sem estado conhecido;
- `unknown_duration_seconds`: tempo entre o primeiro `UNKNOWN` da sequência e
  a última observação, sem um temporizador autônomo.

Uma nova leitura conhecida zera a sequência de `UNKNOWN`; uma perda posterior
inicia outra sequência. Uma leitura repetida de `ON` ou `REST` também atualiza
o instante da última evidência válida, mesmo sem gerar uma transição no log.
O histórico usa memória constante e não persiste entre execuções. Seu relógio
monotônico pode ser injetado para testar longos períodos sem aguardar tempo real.

A contagem mede observações sem estado conhecido, não prova ausência física,
desligamento ou causa da falha. Se a primeira leitura já for `UNKNOWN`, não há
estado anterior conhecido. O tempo começa no primeiro `UNKNOWN` observado, não
no instante físico em que a rede ou o console deixou de responder. Os dados
temporais ficam disponíveis pela API Python e não acrescentam linhas periódicas
ao log de transições nem modificam o contrato `read_state()`.

`switch_hdmi/recovery.py` reserva somente o contrato puro
`RecoveryPolicy.should_recover(observation) -> bool`. **Não há implementação,
instância, avaliação, executor de ações ou ligação desse contrato à CLI ou ao
monitor.** Nenhuma política de recuperação está ativa. O JSON permanece igual:
não foram adicionados limiares, atalhos de ativação ou novos campos obrigatórios.
O formato da Etapa 2.1 e a simulação padrão continuam funcionando.

Em uma etapa futura autorizada, uma implementação poderá receber parâmetros
validados de configuração e avaliar essas observações. Essa decisão deverá
continuar separada da detecção e de qualquer executor, sem transformar
`UNKNOWN` em `OFF`. A escolha de limiares e de qual recuperação executar fica
para essa etapa futura. Nesta entrega, mesmo uma hora ou mais de `UNKNOWN`
observado apenas acumula evidência e mantém as consultas normais.

## Configuração

O `config.json` entregue contém `simulation_mode: true` e `ps5_ip: null`.
Iniciar com esse arquivo não acessa a rede.

| Campo | Validação / efeito |
| --- | --- |
| `simulation_mode` | Booleano; se omitido, assume `true` |
| `ps5_ip` | IPv4 literal unicast; obrigatório em modo real; pode ser `null` em simulação; se informado, sempre validado |
| `check_interval_seconds` | Número finito maior que zero, até `threading.TIMEOUT_MAX`; booleanos rejeitados |
| `network.discovery_port` | Inteiro de 1 a 65535; padrão no exemplo: `9302` |
| `network.timeout_seconds` | Número maior que zero e até 30 segundos |
| `network.retries` | Inteiro de 0 a 3, adicional à primeira tentativa |
| `network.local_ip` | IPv4 da interface do PC; `0.0.0.0` permite escolha pelo sistema |
| `network.local_port` | Inteiro de 0 a 65535; exemplo usa `9303`; `0` pede porta efêmera ao sistema |
| `simulator.states` | Lista não vazia contendo apenas `ON`, `REST` e `OFF` |
| `simulator.repeat` | Booleano; ao terminar, repete ou mantém o último estado |
| `logging.file` | Caminho não vazio; relativo à pasta do JSON; log em UTF-8, acrescentado a cada execução |

Os demais campos são obrigatórios, inclusive a seção `network` em simulação,
para validar o arquivo inteiro antes da execução. Campos desconhecidos e
mistura entre o formato antigo e o novo são rejeitados. Não são aceitos nomes
DNS ou IPv6. Configuração inválida encerra com código `2`, antes de criar o log
ou consultar a rede. Ausência de evidência de estado em uma consulta real é
`UNKNOWN`, não erro fatal da aplicação: o monitor continua e retorna código `0`
quando termina normalmente.

Compatibilidade: arquivos completos da Etapa 2.1 com `mode: "simulator"`,
`poll_interval_seconds`, `simulator` e `logging` continuam funcionando somente
em simulação. O atributo Python `poll_interval_seconds` também foi mantido;
`check_interval_seconds` expõe o mesmo intervalo. Reinicie o aplicativo após
editar o JSON; não há recarga automática.

## Testes automatizados

Na pasta do projeto:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado desta entrega: `Ran 86 tests ... OK`. Antes desta continuação,
a suíte existente de 74 testes também foi executada e aprovada.

Os novos testes verificam códigos ON/REST, mensagens inválidas e ambíguas,
PS4/versão incorreta, timeout, criação/bind/envio/recebimento com erro, limite
de retentativas, recuperação, filtragem de origem, fechamento de socket,
prazo total, propagação de Ctrl+C, recuperação de `UNKNOWN` e seleção pela
configuração. A CLI também é exercitada com rede simulada e arquivos temporários.
Um teste bloqueia a abertura de sockets no modo simulado. Os datagramas de
`tests/samples.py` são sintéticos, baseados no formato estudado, e não capturas
de um PS5. Não há necessidade de console, autenticação ou rede nos testes.

Os 12 testes adicionados nesta continuação cobrem caracteres de controle
inválidos, imutabilidade do histórico, instante inicial zero, preservação da
última leitura válida, atualização em estados estáveis, início e reinício de
sequências de `UNKNOWN`, integração com o monitor e perda prolongada seguida
de resposta válida. O teste de rede prolongada avança o relógio simulado por
uma hora: o resultado permanece `UNKNOWN`, e todos os cinco datagramas enviados
continuam sendo apenas `SRCH`. A resposta posterior `620` recupera `REST`
naturalmente pela detecção, sem executar qualquer política de recuperação.

Para repetir somente os testes de rede e observações:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_network_detector tests.test_observations -v
```

## Roteiro para o PC do cunhado

1. Leve os diretórios `switch_hdmi`, `tests` e `docs`, mais `config.json` e
   `README.md`. Com Python 3.10 ou superior instalado, abra o PowerShell nessa
   pasta e prepare um ambiente local:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m unittest discover -s tests -v
   .\.venv\Scripts\python.exe -m switch_hdmi --max-polls 5
   ```

   A demonstração padrão deve produzir `OFF -> ON -> REST -> ON -> OFF`,
   com dois segundos de espera entre consultas. Ela não consulta o console.

2. Coloque PC e PS5 na mesma LAN e confira o IPv4 atual do console em suas
   informações de conexão. Confira também se Uso remoto está habilitado e
   se a conexão de rede é mantida no repouso. A Sony documenta essas opções
   em [configurações de Uso remoto](https://www.playstation.com/pt-br/support/games/remote-play-settings/)
   e no [guia de Remote Play no PC](https://www.playstation.com/pt-br/support/games/playstation-remote-play-on-pc-and-mac/).
   Essas condições devem ser verificadas manualmente; o aplicativo não altera
   configurações do console, não exige login PSN e não faz pareamento.

3. Crie uma configuração separada para o teste, preservando o padrão:

   ```powershell
   Copy-Item .\config.json .\config.cunhado.json
   ```

   Edite `config.cunhado.json`: troque `simulation_mode` para `false`, preencha
   `ps5_ip` com o IP real e use `check_interval_seconds: 5`. Mantenha a seção
   `simulator`. Exemplo completo; **substitua `192.168.1.100` pelo IP real**:

   ```json
   {
     "simulation_mode": false,
     "ps5_ip": "192.168.1.100",
     "check_interval_seconds": 5,
     "network": {
       "discovery_port": 9302,
       "timeout_seconds": 1.5,
       "retries": 1,
       "local_ip": "0.0.0.0",
       "local_port": 9303
     },
     "simulator": {
       "states": ["OFF", "ON", "REST", "ON", "OFF"],
       "repeat": false
     },
     "logging": {
       "file": "logs/ps5_detector_cunhado.log"
     }
   }
   ```

4. Com o console ligado manualmente, faça uma consulta:

   ```powershell
   .\.venv\Scripts\python.exe -m switch_hdmi --config .\config.cunhado.json --once
   ```

   Procure `modo network/DDP`, resposta válida e `estado=ON`. Depois execute
   um acompanhamento limitado ou contínuo:

   ```powershell
   .\.venv\Scripts\python.exe -m switch_hdmi --config .\config.cunhado.json --max-polls 12
   .\.venv\Scripts\python.exe -m switch_hdmi --config .\config.cunhado.json
   ```

   No segundo comando, Ctrl+C encerra. Durante a observação, coloque o PS5
   em repouso e ligue-o novamente pelos controles normais do console. Uma
   resposta `620` deve aparecer como `REST`; `200`, como `ON`. Durante a
   transição, `UNKNOWN` temporário é possível. Desligue-o manualmente e
   observe: sem resposta, o resultado esperado é `UNKNOWN`, mesmo sabendo
   fisicamente que está desligado. O programa não deve ligar ou controlar
   o console em nenhuma dessas observações.

5. Se só houver `UNKNOWN`, confira IP, rede de convidados/isolamento Wi-Fi,
   VPN, interface de saída e permissões UDP do Python no firewall da rede
   privada. Se o log indicar porta local ocupada, feche outro cliente que a
   use ou teste `network.local_port: 0`. Para múltiplas interfaces, preencha
   `network.local_ip` com o IPv4 local do PC. O aplicativo não muda regras
   de firewall, roteador ou adaptadores. Não há fallback por ping.

6. Guarde `logs/ps5_detector_cunhado.log` e anote o estado físico observado,
   horários, versão do sistema do PS5, tipo de conexão e opções de repouso.
   Compare vários ciclos de ligado/repouso. A validação com o PS5 só fica
   concluída quando essas respostas reais forem observadas e revisadas.
   Para voltar à simulação, execute sem `--config` ou use `simulation_mode: true`.

## Limitações e ponto de parada

Não foi validado firmware ou console físico. Alterações de firmware, opções de
repouso, firewall e topologia podem impedir respostas. Não há descoberta ampla,
IPv6, DNS, autenticação, identidade criptográfica ou garantia de que o IP ainda
pertença ao mesmo dispositivo; a validação é por origem UDP, versão e tipo.
DDP não fornece nonce de correlação nessa consulta, portanto não há garantia
criptográfica de autenticidade ou frescor. A consulta não prova que o usuário
está jogando nem que a entrada HDMI está selecionada.

Não há rotação de logs; o arquivo cresce durante o monitoramento. Consultas
são síncronas, com espera limitada por tentativa. O uso de porta fixa pode
conflitar com outros clientes, e redes isoladas podem exigir ajuste manual.
`OFF` real permanece indisponível até existir uma fonte confiável específica.

Esta entrega termina na Etapa 2.2. Nenhum módulo de monitores foi criado ou
alterado, nenhuma inicialização automática foi configurada e nenhum `.exe`
foi produzido. Não há avanço automático para controle de telas ou outra etapa.

## Arquivos desta entrega

| Situação | Arquivos |
| --- | --- |
| Modificados | `config.json`, `README.md`, `switch_hdmi/__init__.py`, `switch_hdmi/detector.py`, `switch_hdmi/config.py`, `switch_hdmi/monitor.py`, `switch_hdmi/__main__.py`, `tests/test_config.py`, `tests/test_cli.py` |
| Criados | `switch_hdmi/network_detector.py`, `switch_hdmi/factory.py`, `tests/samples.py`, `tests/test_network_config.py`, `tests/test_network_detector.py`, `tests/test_factory.py`, `docs/ETAPA2.2.md` |

Nesta continuação foram criados `switch_hdmi/observations.py`,
`switch_hdmi/recovery.py` e `tests/test_observations.py`; foram atualizados
`switch_hdmi/monitor.py`, `switch_hdmi/network_detector.py`,
`tests/test_network_detector.py`, `README.md` e este documento.
`config.json`, o simulador, a interface `read_state()` e a leitura da configuração
legada não foram modificados nesta continuação.

Em `tests/test_config.py`, a expectativa sobre o conjunto completo do enum foi
ajustada para verificar especificamente os três estados do simulador; os novos
testes verificam `UNKNOWN` no detector real. Os testes do simulador e do monitor
da Etapa 2.1 foram mantidos.
