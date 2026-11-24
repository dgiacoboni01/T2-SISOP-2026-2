# SimES — Simulador de E/S e de sistema de arquivos

Trabalho prático 2 da disciplina de Sistemas Operacionais (PUCRS, 2026/II), Prof. Filipo Novo Mór.

**Integrantes:** Diogo Giacoboni Gallo, Pedro Augusto Wagner, Elisa Ely de Oliveira Rigotti, Maria Júlia Escobar Correia de Melo

Este repositório contém a nossa implementação do SimES, um simulador do caminho de uma requisição de E/S: do sistema de arquivos, passando pelo subsistema de E/S do kernel, até o disco. A partir do código-base fornecido pelo professor, implementamos o núcleo, os seis módulos, a integração ponta a ponta e o bônus de armazenamento flash, e executamos os experimentos X1 a X13. A análise completa está no [RELATORIO.md](RELATORIO.md).

## O que foi implementado

| Parte | Conteúdo | Arquivos principais |
|---|---|---|
| Núcleo · E/S | Escalonadores SSTF e SCAN; custo de CPU por polling e por interrupção | `simes/es/escalonadores.py`, `simes/es/cpu.py` |
| Núcleo · Arquivos | Resolução de caminhos, `mkdir`, `create`, `open`, `read`, `write`, `seek`, `close`, `unlink`, tabelas de arquivos abertos | `simes/arquivos/sistema.py` |
| M1 · Cache de blocos | LRU e FIFO, write-through e write-back, perda numa queda de energia | `simes/es/cache.py` |
| M2 · Escalonamento avançado | C-SCAN, LOOK, C-LOOK e DEADLINE | `simes/es/escalonadores.py` |
| M3 · DMA e buffering | Custo do DMA; buffers simples, duplo e circular | `simes/es/cpu.py`, `simes/es/buffering.py` |
| M4 · Links | Hard links, links simbólicos, ponteiros pendentes e detecção de ciclos | `simes/arquivos/sistema.py` |
| M5 · Proteção e travas | Bits rwx, ACL, travas consultivas e mandatórias | `simes/arquivos/sistema.py` |
| M6 · Alocação | Encadeada, FAT e indexada (índice de 2 níveis) | `simes/arquivos/alocacao.py` |
| Integração | Histórico do sistema de arquivos convertido em requisições que passam pela cache e pelo escalonador | `simes/integracao.py` |
| Bônus · Flash | `DiscoFlash` com FTL, coleta de lixo e amplificação de escrita | `simes/disco.py` |

Os parâmetros do modelo (`simes/parametros.py`), as cargas (`cargas/`) e os experimentos oficiais (`experimentos.py`) não foram alterados.

## Como executar

É necessário Python 3.8 ou superior, usando apenas a biblioteca padrão. O `matplotlib` é opcional e só é usado para gerar os gráficos. No Windows, substitua `python3` por `py`.

```bash
python3 executar.py testes                 # 50 testes (26 fornecidos + 24 nossos)
python3 executar.py experimentos           # roda X1 a X13 e grava resultados/
python3 graficos.py                        # gera graficos/ a partir de resultados/
python3 ferramentas/validar_entrega.py     # confere a entrega
```

Também é possível rodar uma carga isolada, por exemplo:

```bash
python3 executar.py es --carga cargas/es_aleatoria.txt --escalonador SSTF
python3 executar.py es --carga cargas/es_reuso.txt --cache LRU --capacidade 512 --escrita write-back --queda 12000
python3 executar.py arquivos --carga cargas/fs_alocacao.txt --alocador indexada --blocos 20000
python3 executar.py buffering --buffers 2 --producao 2 --consumo 8
```

## Experimentos

| Experimento | Parte | O que compara |
|---|---|---|
| X1 | núcleo | FCFS, SSTF e SCAN em cargas sequencial, aleatória, concentrada e em rajadas |
| X2 | núcleo | Polling × interrupção num disco rígido e num dispositivo rápido |
| X3 | núcleo | Acesso sequencial (compilador) × direto (banco de dados) |
| X4 | núcleo | Custo do `open` conforme a profundidade do caminho |
| X5 | núcleo | Tabelas de arquivos abertos com vários processos |
| X6 | M1 | Cache LRU × FIFO, capacidade, write-through × write-back, queda de energia |
| X7 | M2 | Escalonadores avançados sob carga crescente: inanição e justiça |
| X8 | M3 | Interrupção × DMA conforme o tamanho da requisição; buffering |
| X9 | M4 | Hard links, links simbólicos, ponteiros pendentes e ciclos |
| X10 | M5 | rwx × ACL; travas consultivas × mandatórias |
| X11 | M6 | Alocação contígua × encadeada × FAT × indexada |
| X12 | integração | Cargas de arquivos passando pela cache e pelo escalonador |
| X13 | bônus | Armazenamento flash × disco rígido |

Os resultados oficiais estão em `resultados/*.csv` e os gráficos em `graficos/`.

Além dos experimentos oficiais, fizemos três experimentos extras, em configuração separada, que gravam em `resultados/extras/` e `graficos/extras/`:

- **E1** (`extras/experimentos_extras.py`): flash em estado estacionário, com o dispositivo cheio e diferentes áreas de reserva. Complementa o X13.
- **E2** (`extras/experimentos_extras.py`): picos de latência causados pela sincronização periódica do write-back. Complementa o X6.
- **E3** (`extras/e3_open_real.py`): tempo real do `open` conforme a profundidade, medido num SSD e num HDD de uma máquina com Windows. Complementa o X4.

## Estrutura do repositório

| Caminho | Conteúdo |
|---|---|
| `simes/` | Código do simulador (dispositivo, subsistema de E/S, sistema de arquivos, integração) |
| `cargas/` | Cargas de trabalho padronizadas |
| `experimentos.py`, `executar.py`, `graficos.py` | Experimentos oficiais, linha de comando e gráficos |
| `testes/` | Testes automáticos fornecidos e testes do grupo (`test_grupo.py`) |
| `extras/` | Experimentos extras E1, E2 e E3 |
| `resultados/`, `graficos/` | Resultados e gráficos usados como evidência no relatório |
| `RELATORIO.md`, `grupo.json` | Relatório final e dados do grupo |
| `ferramentas/validar_entrega.py` | Validador da entrega fornecido pelo professor |
