# Relatório — SimES: simulador de E/S e de sistema de arquivos

**Integrantes:** Diogo Giacoboni Gallo, Pedro Augusto Wagner, Elisa Ely de Oliveira Rigotti, Maria Júlia Escobar Correia de Melo

**Repositório:** https://github.com/dgiacoboni01/T2-SISOP-2026-2

## 0. Resumo

Este trabalho implementa o SimES completo. No núcleo, desenvolvemos os escalonadores SSTF e SCAN, o custo de CPU por polling e por interrupção e o sistema de arquivos, com tabelas de arquivos abertos e `unlink` adiado. Também implementamos os seis módulos (M1 cache LRU/FIFO, M2 C-SCAN/LOOK/C-LOOK/DEADLINE, M3 DMA e buffering, M4 links, M5 proteção e travas, M6 alocação encadeada/FAT/indexada), a integração ponta a ponta e o bônus de armazenamento flash, com uma FTL em log e coleta de lixo gulosa. Os 50 testes passam (26 fornecidos e 24 escritos pelo grupo) e os 13 experimentos foram executados.

Três resultados se destacaram. O primeiro é que, no disco rígido, a latência rotacional pesa mais que o deslocamento do braço. Nas rajadas, SSTF e SCAN reduziram o deslocamento em até 16 vezes, mas a resposta média caiu apenas 45%. O maior ganho ocorre quando a ordem de atendimento preserva a sequência física dos blocos, o que elimina a rotação: no X1 sequencial, a resposta média foi de 10,2 s com FCFS e de 17 ms com SSTF.

O segundo resultado diz respeito ao write-back. A sincronização periódica envia de uma vez todos os blocos sujos ao disco e forma rajadas de escrita, e as leituras que chegam logo depois esperam 14 vezes mais que as demais (X6 e extra E2). Além disso, uma queda de energia chegou a perder 55 blocos.

O terceiro é o peso dos metadados. Como o bloco de índice é relido a cada acesso, a alocação indexada ficou 2,7 vezes mais lenta que a contígua na integração. Com uma cache LRU, os blocos de índice permanecem na memória e essa diferença praticamente desaparece (X11 e X12).

## 1. Escopo realizado

| Parte | Situação | Observação |
|---|---|---|
| N1 · Dispositivo (fornecido) | usado | — |
| N2 · Subsistema de E/S (SSTF, SCAN, polling, interrupção) | completo | conferido com os exemplos do livro (fila 98, 183, …, cabeça em 53) |
| N3 · Sistema de arquivos | completo | todas as regras de custo seguidas; `unlink` com arquivo aberto libera no último `close` |
| N4 · Experimentos X1–X5 e relatório | completo | — |
| M1 · Cache de blocos | completo | LRU e FIFO, write-through e write-back, perda na queda (X6) |
| M2 · Escalonamento avançado | completo | C-SCAN, LOOK, C-LOOK e DEADLINE (X7) |
| M3 · DMA e buffering | completo | DMA e buffers simples, duplo e circular (X8) |
| M4 · Links e compartilhamento | completo | hard links, links simbólicos (também no meio do caminho), ELOOP, pendentes, ciclos (X9) |
| M5 · Proteção e travas | completo | rwx, ACL, travas consultivas e mandatórias (X10) |
| M6 · Alocação em disco | completo | encadeada, FAT e indexada com índice de 2 níveis (X11) |
| Integração ponta a ponta | completo | histórico do sistema de arquivos → cache → escalonador (X12) |
| Bônus flash | completo | `DiscoFlash` com FTL, coleta de lixo e amplificação de escrita (X13 + extra E1) |

Resultado dos testes automáticos (`python3 executar.py testes`): `Ran 50 tests in 0.085s — OK`. Nenhum teste foi pulado, e 24 dos 50 testes foram escritos pelo grupo, em `testes/test_grupo.py`.

Também realizamos experimentos extras, em configuração separada. O script `extras/experimentos_extras.py` contém E1 e E2, que são simulados, e `extras/e3_open_real.py` contém E3, uma medição do `open` numa máquina real que complementa o X4. Esses scripts gravam em `resultados/extras/` e `graficos/extras/` e não alteram `parametros.py`, as cargas nem `experimentos.py`. Os resultados oficiais continuam sendo os de `resultados/*.csv`.

## 2. Implementação

### N2 — Subsistema de E/S

**Onde:** `simes/es/escalonadores.py` (`SSTF`, `SCAN` e as funções auxiliares `_mais_proxima`, `_a_frente`, `_direcao_do_movimento`); `simes/es/cpu.py` (`custo_cpu`).

**Decisões:** Todos os escalonadores usam a mesma função auxiliar, `_mais_proxima(fila, cabeca, filtro)`. Ela percorre a fila na ordem de chegada e só troca de candidata quando encontra uma distância estritamente menor, de modo que a regra de desempate (vence a requisição mais antiga) é respeitada sem código adicional. O filtro `_a_frente` implementa a regra de que uma requisição no mesmo cilindro da cabeça está à frente nas duas direções.

O SSTF escolhe a requisição mais próxima sem aplicar filtro e devolve como direção o sentido do movimento (+1 ou −1), ou mantém a direção atual quando o alvo está no mesmo cilindro. O SCAN procura a mais próxima à frente. Quando não há nenhuma, ele devolve `passar_por=[borda]` (0 ou `max_cilindro`), inverte a direção e escolhe a mais próxima a partir da borda, que coincide com a mais próxima da cabeça entre as que ficaram para trás.

Quanto ao custo de CPU, o polling custa `duracao_ms`, já que a CPU fica em espera ocupada durante todo o atendimento, e a interrupção custa `n_blocos × (CUSTO_INTERRUPCAO_MS + CUSTO_COPIA_MS_POR_BLOCO)`.

**Dificuldades:** No SCAN, tivemos dúvida sobre o ponto de referência para medir a requisição mais próxima depois da inversão. Medir a partir da borda ou a partir da cabeça leva à mesma escolha, porque após a inversão todas as requisições ficam do mesmo lado. Conferimos o resultado com o exemplo do livro, que dá 236 cilindros descendo e 331 subindo, com borda em 199.

### N3 — Sistema de arquivos

**Onde:** `simes/arquivos/sistema.py`: `_resolver_partes`, `_pai_e_nome`, `_criar_entrada`, `resolver`, `mkdir`, `create`, `open`, `read`, `write`, `seek`, `close`, `unlink`.

**Decisões:** Na resolução de caminhos (`_resolver_partes`), cada componente exige a leitura do diretório que o contém. Assim, `/a/b/c` custa 3 leituras e `/` não custa nenhuma. Um componente ausente gera ENOENT, e tentar atravessar um arquivo gera ENOTDIR. As operações que criam ou removem entradas usam `_pai_e_nome`, que resolve o pai da mesma forma que `resolver` e em seguida lê o próprio pai. Isso corresponde ao "até o pai, inclusive" das regras; por exemplo, `create /a/b/f.txt` custa 3 leituras.

As operações `mkdir` e `create` alocam pelo alocador (1 bloco para diretório e `ceil(tamanho/4096)` para arquivo), aplicam os extras do alocador e gravam os blocos de diretório exigidos. Se o alocador lançar ENOSPC, o FCB recém-criado é descartado para não deixar um FCB órfão.

Para as tabelas de arquivos abertos, `tabela_global[fcb_id]` guarda uma `EntradaGlobal` com o contador de aberturas, e `tabelas_processo[proc][handle]` guarda uma `EntradaProcesso` com modo e posição. Quando dois processos abrem o mesmo arquivo, eles compartilham a entrada global (contador 2) e mantêm posições independentes. Abrir um handle que o processo já usa gera EINVAL, e a tabela de um processo é removida quando fica vazia.

As operações `read` e `write` calculam os blocos lógicos do intervalo `[posição, posição+n)`, sendo que no `read` o intervalo é limitado ao tamanho do arquivo. O `write` primeiro estende o arquivo com `alocador.estender`, se faltarem blocos, e depois grava. No modo `'a'`, a posição é levada ao fim antes de cada escrita, como em `O_APPEND`. Toda escrita marca o FCB como sujo, e o `close` então grava o bloco do diretório pai (1 escrita).

No `unlink` de um arquivo aberto, a entrada é removida e o pai é gravado. O contador `links` é decrementado e, se chegar a zero, os blocos só são liberados quando não houver entrada na tabela global; caso contrário, a liberação fica para o `close` que zerar o contador. Se o arquivo removido estiver sujo, esse `close` não grava o pai, porque o arquivo já não possui entrada de diretório. O `unlink` de um diretório gera EISDIR, como no Linux.

**Dificuldades:** A parte mais trabalhosa foi acertar o custo exato de cada operação. O `open`, por exemplo, não escreve nada, e o `close` só escreve se houve escrita no arquivo. Os testes do núcleo conferem cada um desses custos.

### M1 — Cache de blocos

**Onde:** `simes/es/cache.py` (`_CacheOrdenada`, `CacheLRU`, `CacheFIFO`).

**Decisões:** As duas caches compartilham uma classe base que guarda um `OrderedDict` de bloco para estado sujo. A ordem do dicionário é a ordem de expulsão, e a vítima é sempre o primeiro item. A LRU chama `move_to_end` a cada acerto e a cada escrita, enquanto a FIFO nunca reordena. No write-through, `escrever` devolve `[bloco]` e o bloco nunca fica sujo. No write-back, o bloco é marcado como sujo e só vai ao disco quando é expulso ou quando ocorre o `sincronizar`. A expulsão devolve apenas as vítimas sujas.

**Dificuldades:** Regravar um bloco que já está na cache não pode provocar expulsão. Escrevemos um teste específico para esse caso.

### M2 — Escalonamento avançado

**Onde:** `simes/es/escalonadores.py` (`CSCAN`, `LOOK`, `CLOOK`, `Deadline`).

**Decisões:** Quando não há requisições à frente, o C-SCAN devolve `passar_por=[max_cilindro, 0]`, de modo que o retorno ao início é contado como deslocamento. O LOOK inverte a direção no ponto onde está. O C-LOOK salta para a requisição de menor cilindro, com desempate pela mais antiga. O DEADLINE procura, entre as requisições vencidas (`agora >= prazo`), a de prazo mais antigo e mantém a direção; quando nenhuma está vencida, a decisão é delegada ao SSTF. Os valores do livro foram reproduzidos: C-SCAN 382, LOOK 299 e C-LOOK 322.

**Dificuldades:** Não houve dificuldades relevantes.

### M3 — DMA e buffering

**Onde:** `simes/es/cpu.py` (modo `dma`) e `simes/es/buffering.py`.

**Decisões:** O DMA custa `CUSTO_PROGRAMAR_DMA_MS + CUSTO_INTERRUPCAO_MS`, independentemente do tamanho da requisição. O buffering foi implementado como uma recorrência que segue diretamente as regras: a produção do bloco *k* começa em `max(fim_produção[k−1], fim_gravação[k−n])`, a espera é somada a `bloqueio_produtor_ms` e a gravação começa em `max(fim_produção[k], fim_gravação[k−1])`.

**Dificuldades:** Não houve dificuldades relevantes. Os testes com 1 e 2 buffers conferem com as fórmulas fechadas.

### M4 — Links e compartilhamento

**Onde:** `simes/arquivos/sistema.py` (`_resolver_partes`, `link`, `symlink`, `percorrer`).

**Decisões:** Durante a resolução, quando um componente é um link simbólico (e não é o último componente, ou `seguir_simbolico=True`), os componentes do alvo substituem o link no início da lista de pendentes e a busca recomeça pela raiz, pagando novamente a leitura dos diretórios do alvo. Mais de 8 links seguidos geram ELOOP.

Para distinguir um link pendente de um ENOENT comum, contamos quantos dos próximos componentes vieram de um alvo. Se o nome ausente veio de um alvo, o erro é marcado com `pendente=True` e o `open` incrementa `links_pendentes`.

O `link` não segue um link simbólico no último componente, como o `link()` do Linux, recusa diretórios com EPERM e incrementa `links`. O `percorrer` é uma busca em profundidade com um conjunto `visitados` por travessia. Revisitar um diretório incrementa `ciclos_detectados` e interrompe a descida. Os links simbólicos são seguidos como no `find -L`: um alvo inexistente conta em `links_pendentes` e um alvo em laço conta em `links_em_laco`, indicador que acrescentamos.

**Dificuldades:** Pela regra do enunciado, qualquer revisita conta como ciclo, inclusive chegar a `/atalhos` pela segunda vez sem que haja laço. Esse ponto é discutido no X9.

### M5 — Proteção e travas

**Onde:** `simes/arquivos/sistema.py` (`_verificar_permissao`, `_verificar_trava`, `usuario`, `chmod`, `setacl`, `lock`, `unlock`). Os dois ganchos são redefinidos na seção do M5 da própria classe e, por aparecerem depois, substituem as versões vazias.

**Decisões:** Na verificação de permissão, o root sempre passa e conta uma verificação. Se a ACL tem uma entrada para o usuário, vale a primeira, e a posição dela é somada a `verificacoes`. Caso contrário, valem os bits do dono ou de "outros", e soma-se `len(acl)+1`.

O `chmod` aceita `'640'` como texto e `640` como número (o simulador converte argumentos numéricos para `int`), e sempre interpreta o valor em octal.

Com bloqueio consultivo, ler ou escrever um arquivo travado por outro processo apenas registra uma violação. Com bloqueio mandatório, a operação falha com EBUSY. O `close` do dono libera a trava, como no `fcntl` do POSIX, que solta as travas do processo em qualquer `close`. Um `unlock` feito por quem não é o dono não tem efeito.

**Dificuldades:** O simulador converte automaticamente `'600'` para o inteiro 600, que sem tratamento seria interpretado como 0o1130.

### M6 — Alocação em disco

**Onde:** `simes/arquivos/alocacao.py` (`AlocadorEncadeado`, `AlocadorFAT`, `AlocadorIndexado`).

**Decisões:** A alocação encadeada usa `primeiros_livres`. O `estender` devolve 1 escrita no antigo último bloco, e `bloco_fisico` segue literalmente a regra do cursor: se *i* ≤ cursor, a busca recomeça do bloco 0. A FAT herda a alocação da encadeada, mas não tem extras. A indexada precisa de 1 bloco de índice, ou de `1 + ceil(n/1024)` quando *n* > 1024. Ao passar de 1 para 2 níveis, o índice antigo se torna o de 1º nível e os novos são de 2º nível. Os extras correspondem a uma escrita por bloco de índice, e acrescentamos o indicador `blocos_indice`.

**Dificuldades:** A regra de recomeçar quando i ≤ cursor penaliza escritas desalinhadas em modo acréscimo, porque cada `write` de 4 KiB volta ao bloco do cursor e relê a corrente inteira. Mantivemos a regra, para que todos os grupos obtenham os mesmos números, e analisamos o efeito no X11.

### Integração ponta a ponta

**Onde:** `simes/integracao.py`.

**Decisões:** O histórico é ordenado pelo instante com `sorted`, que é estável. Com isso, os acessos de uma mesma operação (mesmo instante) mantêm a ordem em que o sistema de arquivos os emitiu. Os identificadores são 0, 1, 2 e assim por diante.

**Dificuldades:** Não houve dificuldades relevantes.

### Bônus — DiscoFlash

**Onde:** `simes/disco.py` (`DiscoFlash`).

**Decisões:** A FTL funciona como um log. Ela mantém um mapa lógico → físico (um dicionário esparso) e o mapa reverso por página física, e cada bloco apagável tem um contador de páginas válidas e um contador de desgaste. Toda gravação vai para a próxima página livre do bloco ativo e invalida a página antiga.

Quando restam até 2 blocos apagados (a reserva), a coleta gulosa escolhe o bloco cheio com menos páginas válidas, com desempate pelo menos desgastado, copia as páginas válidas (leitura e programação) e apaga o bloco. O tempo da coleta é cobrado da gravação que a disparou. A leitura custa `n × 0,025 ms`. A área física tem `ceil(320.000 × 1,07)` páginas, arredondada para blocos de 64 páginas, o que dá 5.350 blocos.

O método `preencher()` envelhece o dispositivo e é usado apenas no experimento extra E1. Também expomos o atributo `cilindros`, porque o simulador de E/S o consulta.

**Dificuldades:** Foi preciso garantir que a coleta nunca ficasse sem página livre no meio de uma cópia, e a reserva de 2 blocos resolve isso. Um dos testes confere que nenhum dado se perde depois de milhares de coletas.

## 3. Experimentos

Todos os tempos estão em ms. Chamamos de resposta o intervalo entre a chegada e o fim do atendimento, e de espera o intervalo entre a chegada e o início. A ocupação do disco foi estimada pela porcentagem de CPU do polling no X2, já que no polling a CPU fica ocupada exatamente enquanto o disco trabalha.

### X1 — Escalonadores sob quatro padrões de carga

**Hipótese:** SSTF e SCAN terão deslocamento e resposta média bem menores que o FCFS nas cargas com fila (sequencial e rajadas) e ganho pequeno na aleatória e na concentrada, onde a fila é curta. O SSTF terá a menor resposta média e a maior espera máxima (inanição). O SCAN terá espera máxima menor que o SSTF.

**Fundamentação:** Silberschatz §10.4: o FCFS é justo, mas não otimiza o movimento do braço; o SSTF é guloso e pode causar inanição; o SCAN limita a espera a cerca de duas varreduras. Pelo modelo (`parametros.py`), cada acesso fora de sequência paga 1 ms de busca mínima, mais 0,003 ms por cilindro e 4,17 ms de rotação. O ganho de reordenar depende de haver fila para reordenar.

**Configuração:** 4 cargas padronizadas (2.000 requisições de 8 blocos, 70% de leituras, chegadas exponenciais com média de 12 ms; nas rajadas, 50 requisições a cada 600 ms), sempre no disco rígido, em modo interrupção, com FCFS, SSTF e SCAN.

**Resultados:**

| Carga | Escalonador | Deslocamento (cil.) | Resposta média | Resposta p95 | Espera máx. | Vazão (req/s) |
|---|---|---:|---:|---:|---:|---:|
| sequencial | FCFS | 7.184.093 | 10.241,0 | 19.565,4 | 20.598,1 | 61,5 |
| sequencial | SSTF | 2.336.365 | 17,4 | 32,8 | 37,4 | 167,5 |
| sequencial | SCAN | 2.469.069 | 23,2 | 43,7 | 48,6 | 167,4 |
| aleatória | FCFS | 3.316.472 | 50,1 | 145,1 | 193,5 | 82,5 |
| aleatória | SSTF | 2.631.714 | 21,0 | 48,6 | 116,7 | 82,5 |
| aleatória | SCAN | 3.372.680 | 31,5 | 65,1 | 116,7 | 82,4 |
| concentrada | FCFS | 1.864.164 | 19,8 | 48,6 | 96,0 | 83,8 |
| concentrada | SSTF | 1.613.030 | 15,0 | 37,2 | 130,5 | 83,8 |
| concentrada | SCAN | 3.338.708 | 33,2 | 68,3 | 118,8 | 83,7 |
| rajadas | FCFS | 3.341.490 | 267,0 | 505,3 | 597,3 | 83,5 |
| rajadas | SSTF | 209.811 | 147,5 | 277,7 | 297,0 | 84,4 |
| rajadas | SCAN | 199.955 | 148,6 | 278,7 | 285,4 | 84,4 |

**Evidências:** resultados/es.csv (experimento=X1); graficos/X1_resposta_media_ms.png; graficos/X1_espera_max_ms.png; graficos/X1_deslocamento_total.png; resultados/es.csv (experimento=X2, ocupação do disco).

**Análise:**

Na carga sequencial, o FCFS apresentou o pior desempenho de todo o experimento. Os processos P1 (por volta do cilindro 312) e P2 (por volta do cilindro 3.906) se alternam na fila, de modo que cada requisição atravessa cerca de 3.600 cilindros e custa aproximadamente 1 + 10,8 + 4,17 + 0,32 ≈ 16,3 ms. Como chega uma requisição a cada 6 ms em média, o disco satura: o X2 indica ocupação de 99,998%, e embora as chegadas terminem em 11,9 s, o disco só conclui o atendimento em 32,5 s. Com SSTF ou SCAN, o braço permanece em um dos fluxos e cada requisição começa no bloco seguinte ao último transferido. Pela regra do modelo, isso elimina a latência rotacional e deixa apenas os 0,32 ms de transferência. A resposta cai 590 vezes, enquanto o deslocamento cai só 3 vezes, o que mostra que o ganho vem da sequência física e não da distância percorrida.

Nas rajadas, o deslocamento caiu 16 vezes, mas a resposta apenas 45%. Com 50 requisições ordenadas pela superfície, a distância média entre elas fica em torno de 100 cilindros (0,3 ms de busca), mas cada uma ainda paga 1 ms de busca mínima, 4,17 ms de rotação e 0,32 ms de transferência, o que dá cerca de 5,8 ms, contra cerca de 10,5 ms no FCFS. A razão 5,8/10,5 = 0,55 coincide com a razão medida, 147,5/267,0 = 0,55. Concluímos que, num disco rígido, a rotação e o custo fixo da busca limitam o ganho que qualquer escalonador pode obter.

Nas cargas aleatória e concentrada, a fila é curta (ocupação de cerca de 86%). O SSTF melhora a resposta média, mas na carga concentrada a espera máxima piora em relação ao FCFS (130,5 contra 96,0 ms). Trata-se da inanição prevista na hipótese: as requisições fora da zona quente (os 20% iniciais do disco, que recebem 80% dos acessos) acabam sendo preteridas.

O SCAN percorreu mais cilindros que o FCFS nas cargas concentrada e aleatória. Com a fila quase vazia, sempre que não há requisições à frente, a regra do SCAN obriga o braço a ir até a borda (cilindro 4.999 ou 0), mesmo com 80% das requisições perto do cilindro 0. Esse é justamente o problema que o LOOK resolve, como se vê no X7. Por fim, a vazão foi praticamente igual entre os escalonadores sempre que o disco não saturou, já que nesses casos ela é determinada pela taxa de chegada.

**Veredito:** PARCIALMENTE CONFIRMADA

### X2 — Polling × interrupção conforme o dispositivo

**Hipótese:** No disco rígido, o polling gastará uma fração de CPU próxima da ocupação do disco (dezenas de %), e a interrupção, cerca de 1%. No dispositivo rápido, o polling ficará competitivo e poderá até gastar menos CPU que a interrupção.

**Fundamentação:** Silberschatz §13.2.1–13.2.2: o polling desperdiça ciclos em espera ocupada, mas evita o custo da interrupção (troca de contexto e tratador), e compensa quando o dispositivo responde mais rápido que esse custo. No modelo, para uma requisição de 8 blocos, o polling custa a duração do atendimento e a interrupção custa 8 × (0,015 + 0,002) = 0,136 ms. No dispositivo rápido, a duração é 8 × 0,008 = 0,064 ms.

**Configuração:** FCFS; cargas aleatória e sequencial; disco rígido e dispositivo rápido (sem busca nem rotação); modos polling e interrupção. Mede-se a porcentagem do tempo total de CPU gasta com E/S.

**Resultados:**

| Dispositivo | Carga | Polling (% CPU) | Interrupção (% CPU) | Resposta média |
|---|---|---:|---:|---:|
| hdd | aleatória | 86,28 | 1,12 | 50,1 |
| hdd | sequencial | 99,998 | 0,84 | 10.241,0 |
| rápido | aleatória | 0,53 | 1,12 | 0,064 |
| rápido | sequencial | 1,07 | 2,28 | 0,064 |

**Evidências:** resultados/es.csv (experimento=X2); graficos/X2_cpu.png.

**Análise:**

No disco rígido, o polling ocupa a CPU praticamente durante todo o tempo em que o disco trabalha (86% na carga aleatória e quase 100% na sequencial), enquanto a interrupção custa cerca de 1%. Na prática, a porcentagem de CPU do polling acaba medindo a ocupação do disco.

No dispositivo rápido, a relação se inverte. Cada requisição dura 0,064 ms, menos que os 0,136 ms de 8 interrupções e 8 cópias, e por isso o polling gasta metade da CPU gasta pela interrupção. Pelo modelo, o polling compensa sempre que a duração da requisição é menor que `n × 0,017 ms`, ou seja, apenas em dispositivos com latência da ordem de microssegundos.

Na carga sequencial do dispositivo rápido, a porcentagem da interrupção dobra (2,28%). Isso não ocorre porque cada requisição custe mais, e sim porque a carga termina em 11,9 s em vez de 32,5 s (o disco rígido estava saturado), de modo que o mesmo custo é dividido por um tempo total menor. Observamos também que o modo de CPU não altera a resposta, já que no modelo a CPU não faz parte do caminho crítico do disco.

**Veredito:** CONFIRMADA

### X3 — Acesso sequencial × direto

**Hipótese:** Por operação de leitura, os dois padrões farão o mesmo número de acessos (1 bloco de 4 KiB), mas a leitura direta (banco de dados) custará várias vezes mais tempo, pois quase toda leitura paga busca e rotação, enquanto a sequencial (compilador) aproveita a sequência física.

**Fundamentação:** Silberschatz §11.2 (métodos de acesso sequencial e direto) e §12.4.1 (a alocação contígua favorece os dois, com endereço direto). No modelo, uma leitura em sequência custa 0,04 ms, e uma fora de sequência custa pelo menos 5,2 ms.

**Configuração:** Cargas `fs_compilador` (3 compilações de 40 fontes de 2–64 KiB, cada uma lendo 3 cabeçalhos e gravando um `.o`) e `fs_banco_dados` (tabela de 64 MiB e 3.000 transações: 80% leituras e 20% escritas de 4 KiB em posições aleatórias, cada escrita com 512 B de log em modo acréscimo). Alocação contígua.

**Resultados:**

| Carga | Operação | n | Acessos/op | Tempo médio (ms) |
|---|---|---:|---:|---:|
| compilador | read | 1.719 | 1,00 | 1,48 |
| compilador | write | 726 | 1,00 | 0,91 |
| compilador | open | 600 | 3,00 | 8,20 |
| compilador | create / unlink | 175 / 80 | 4,00 / 4,00 | 11,95 / 13,69 |
| banco de dados | read | 2.405 | 1,00 | 5,49 |
| banco de dados | write | 1.190 | 1,00 | 5,54 |
| banco de dados | seek | 3.000 | 0,00 | 0,00 |

No total, foram 1,38 acesso/op e 3,07 ms/op no compilador, contra 0,55 acesso/op e 3,01 ms/op no banco, cuja média é diluída pelos `seek`.

**Evidências:** resultados/arquivos_por_operacao.csv (experimento=X3); resultados/arquivos.csv (experimento=X3); graficos/X3_tempo_por_operacao.png.

**Análise:**

No banco de dados, a leitura direta custou 3,7 vezes mais que a sequencial, com o mesmo número de acessos. A tabela ocupa 16.384 blocos (256 cilindros), então uma posição aleatória fica em média a cerca de 85 cilindros da anterior, o que dá 1 + 0,26 + 4,17 + 0,04 ≈ 5,47 ms, valor muito próximo dos 5,49 ms medidos.

No compilador, a primeira leitura de cada arquivo paga busca e rotação, porque o `open` anterior leu diretórios em outra região do disco, e as leituras seguintes custam 0,04 ms. A média de 1,48 ms reflete arquivos de cerca de 8 blocos. Os custos fixos do sistema de arquivos, como o `open` (3 leituras de diretório, 8,2 ms) e o `create` (4 acessos), dominam o tempo do compilador, o que antecipa o resultado do X4. Por fim, como previsto, o `seek` não acessa o disco e o log em modo acréscimo grava 1 bloco por transação.

**Veredito:** CONFIRMADA

### X4 — Custo de resolver caminhos conforme a profundidade

**Hipótese:** O `open` fará `d + 1` leituras para um arquivo a `d` diretórios de profundidade (a raiz mais os `d` diretórios), e o tempo crescerá aproximadamente na mesma proporção, cerca de 5 ms por nível, já que cada diretório é um bloco em outro lugar do disco.

**Fundamentação:** Silberschatz §11.3 (diretórios em árvore) e as regras de custo de `sistema.py`, segundo as quais resolver um caminho lê um bloco por diretório. Sem cache de diretórios, cada nível é um acesso ao disco.

**Configuração:** Uma cadeia de `d` diretórios (`/n1/n2/…/nd`) com `f.txt` no fim, `d` = 1…10, e 20 ciclos de `open` + `read` + `close` de 4 KiB por P2. Alocação contígua. No extra E3 (fora dos resultados oficiais), a mesma cadeia de 1 a 10 diretórios foi criada de fato num SSD e num HDD de uma máquina real, medindo 5.000 `os.open` + `os.close` por profundidade (`extras/e3_open_real.py`).

**Resultados:**

| Profundidade | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Acessos por open | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
| Tempo por open (ms) | 4,25 | 4,29 | 4,33 | 4,37 | 4,41 | 4,45 | 4,49 | 4,53 | 4,57 | 4,61 |

Extra E3, na máquina real (Windows 11, NTFS, Intel Core i5-11400H): mediana de 5.000 `open` + `close` por profundidade, com os metadados na cache. Os tempos desta tabela estão em µs (1 ms = 1.000 µs).

| Profundidade | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SSD (C:), µs | 27,1 | 27,2 | 27,3 | 27,1 | 27,2 | 27,2 | 27,2 | 27,4 | 27,4 | 27,3 |
| HDD (D:), µs | 40,9 | 41,2 | 41,0 | 42,2 | 42,1 | 41,8 | 41,6 | 41,5 | 42,4 | 41,6 |

**Evidências:** resultados/arquivos_por_operacao.csv (experimento=X4, operacao=open); graficos/X4_open_por_profundidade.png; extra E3: resultados/extras/E3_open_real.csv; graficos/extras/E3_open_real.png.

**Análise:**

O número de acessos cresceu como `d + 1`, conforme previsto. O tempo, porém, subiu apenas 0,04 ms por nível, que é o tempo de transferir um bloco. Isso acontece porque os diretórios foram criados em sequência numa partição vazia com alocação contígua (first-fit) e por isso ocupam os blocos consecutivos 0, 1, 2, …, *d*. Somente a primeira leitura, a da raiz, paga a latência rotacional de 4,17 ms, e nem a busca ela paga, pois todos esses blocos estão no cilindro 0, onde o braço já se encontra. Cada diretório seguinte é o próximo bloco físico e, pela regra do modelo, não paga rotação. O tempo do `open` é, portanto, 4,17 + (*d* + 1) × 0,04 ms, o que dá 4,25 ms para *d* = 1 e 4,61 ms para *d* = 10, os mesmos valores da tabela.

O `read` também custa 0,04 ms, porque o bloco de `f.txt` foi alocado logo depois do último diretório e a leitura continua a sequência. O `open` seguinte, por outro lado, volta ao bloco 0 (raiz) e paga a rotação novamente.

A hipótese errou no tempo porque supunha diretórios espalhados pelo disco. Nesse caso, cada nível custaria cerca de 5 ms e o `open` a 10 níveis levaria cerca de 55 ms. O resultado mostra que o custo da resolução depende da localidade dos metadados. Sistemas reais procuram obter essa localidade, como nos grupos de blocos do ext4 e na MFT do NTFS, e sobretudo evitam o disco com caches de nomes (seção 4).

O extra E3 permite comparar com uma máquina real. Nela, o `open` + `close` levou 27 µs no SSD e 41 µs no HDD, praticamente constante de 1 a 10 níveis: a variação ficou abaixo de 1,5 µs, dentro do ruído da medição. O `open` simulado (4,25 a 4,61 ms) é cerca de 160 vezes mais lento, e o acréscimo de um único nível no simulador (40 µs) já custa mais que um `open` real inteiro. Com os metadados na cache, nem a profundidade nem o tipo de mídia influenciam o resultado, já que uma única busca ou rotação do HDD custaria milissegundos e nenhuma medição chegou perto disso. Quem resolve o caminho é a cache de nomes do sistema operacional, componente que o modelo não possui. A diferença de cerca de 14 µs entre os dois volumes é constante e não cresce com a profundidade, logo não vem da resolução do caminho no disco. Acreditamos que seja um custo fixo por volume, por exemplo de drivers de filtro como o antivírus, mas não chegamos a isolá-lo.

É importante notar que o E3 foi medido com a cache quente. Uma medição com a cache fria exigiria privilégio de administrador (por exemplo, `drop_caches` no Linux), e apenas nesse caso o disco apareceria nos tempos. Os números do E3 dependem da máquina e não substituem os resultados oficiais.

**Veredito:** PARCIALMENTE CONFIRMADA

### X5 — Tabelas de arquivos abertos com vários processos

**Hipótese:** Com 8 trabalhadores abrindo 3 páginas cada um (de um conjunto de 12), a soma das tabelas por processo chegará a 24 entradas, cada tabela por processo a 3, e a tabela global ficará limitada pelo número de arquivos distintos (≤ 12). O contador de aberturas de uma página popular passará de 1, pois há compartilhamento.

**Fundamentação:** Silberschatz §11.1.2: há uma tabela por processo (posição, modo) que aponta para uma tabela do sistema, com uma entrada por arquivo aberto e um contador de aberturas. O FCB é lido uma vez, por mais que o arquivo seja aberto várias vezes.

**Configuração:** Carga `fs_servidor_web`: 12 páginas de 8–96 KiB e 40 rodadas. Em cada rodada, 8 processos abrem 3 páginas sorteadas, leem tudo em pedaços de 16 KiB e fecham.

**Resultados:**

| Indicador | Valor |
|---|---:|
| Máx. tabela global (entradas) | 12 |
| Máx. tabela de um processo | 3 |
| Máx. soma das tabelas por processo | 24 |
| Máx. contador de aberturas de um arquivo | 6 |
| Acessos por open / read / close | 2,0 / 3,65 / 0,0 |

**Evidências:** resultados/arquivos.csv (experimento=X5); resultados/arquivos_por_operacao.csv (experimento=X5); graficos/X5_tabelas.png.

**Análise:**

A soma das tabelas por processo (24 entradas) foi o dobro da tabela global (12 entradas). As 24 aberturas simultâneas apontam para apenas 12 entradas globais, e uma das páginas chegou a ter 6 aberturas ao mesmo tempo, cada uma com sua própria posição.

O `close` custou 0 acessos porque todas as aberturas são somente de leitura e o FCB não fica sujo. Já o `read` custou em média 3,65 acessos, porque cada leitura de 16 KiB toca 4 blocos, com exceção da última, que é parcial.

Em termos de memória, o tamanho da tabela global cresce com o número de arquivos distintos abertos, enquanto as tabelas por processo crescem com o número de aberturas. Num servidor real com milhares de conexões, são as tabelas por processo que crescem, e por isso existem limites como o `ulimit -n` no Linux.

**Veredito:** CONFIRMADA

### X6 — Cache de blocos (M1)

**Hipótese:** A taxa de acertos crescerá com a capacidade e saturará quando a cache couber todos os blocos distintos da carga. A LRU acertará mais que a FIFO nas capacidades pequenas (localidade de Zipf). O write-back fará menos escritas físicas e terá resposta menor que o write-through, mas perderá blocos sujos numa queda de energia; o write-through não perderá nada.

**Fundamentação:** Silberschatz §13.4.3 (caching) e §12.6.2 (buffer cache unificada, escrita síncrona × assíncrona). A LRU se aproxima do algoritmo ótimo quando há localidade temporal. No write-back, a escrita é adiada até a expulsão ou até a sincronização periódica (5.000 ms).

**Configuração:** Carga `es_reuso`: 3.000 requisições de 1 bloco, Zipf sobre 4.000 páginas, 65% leituras, chegadas a cada 8 ms em média. FCFS; LRU e FIFO; capacidade de 64 a 2.048 blocos; write-through e write-back. A queda de energia é simulada na metade da carga (11.733 ms).

**Resultados:**

| Cache | Capacidade | Escrita | Acertos (%) | Resposta média | Resposta p95 | Escritas físicas | Perdidos na queda |
|---|---:|---|---:|---:|---:|---:|---:|
| nenhuma | — | — | — | 3.500,0 | 6.427,5 | 1.045 | 0 |
| LRU | 64 | write-through | 39,6 | 74,7 | 211,5 | 1.045 | 0 |
| LRU | 64 | write-back | 39,6 | 23,4 | 110,4 | 715 | 28 |
| FIFO | 64 | write-through | 31,3 | 436,1 | 851,2 | 1.045 | 0 |
| FIFO | 64 | write-back | 33,1 | 46,3 | 223,5 | 810 | 22 |
| LRU | 256 | write-through | 57,7 | 22,0 | 75,9 | 1.045 | 0 |
| LRU | 256 | write-back | 57,7 | 47,6 | 393,4 | 543 | 55 |
| FIFO | 256 | write-back | 53,3 | 46,3 | 354,1 | 607 | 50 |
| LRU | 512 | write-through | 64,2 | 18,2 | 66,5 | 1.045 | 0 |
| LRU | 512 | write-back | 64,2 | 45,4 | 265,4 | 541 | 55 |
| LRU = FIFO | ≥ 1.024 | write-through | 67,1 | 16,9 | 62,6 | 1.045 | 0 |
| LRU | ≥ 1.024 | write-back | 67,0 | 38,9 | 117,2 | 541 | 55 |

Extra E2 (LRU 512, write-back), comparando as leituras que chegam até 1 s depois de uma sincronização com as demais:

| Intervalo de sync | Escalonador | Leituras logo após o sync | Demais leituras | Escritas físicas |
|---|---|---:|---:|---:|
| 1 s | FCFS | 35,2 | 10,0 | 799 |
| 5 s (padrão) | FCFS | 309,9 | 22,0 | 541 |
| 5 s (padrão) | SSTF | 60,2 | 4,5 | 541 |
| 30 s | FCFS | — (nenhum sync antes do fim) | 5,3 | 228 |

**Evidências:** resultados/es.csv (experimento=X6); graficos/X6_acerto.png; graficos/X6_escrita.png; resultados/extras/E2_tempestade_write_back.csv; graficos/extras/E2_tempestade_write_back.png.

**Análise:**

A taxa de acertos saturou em 67% a partir de 1.024 blocos, porque a carga toca apenas 992 blocos distintos. A partir desse ponto a cache guarda todos os blocos, e as únicas faltas são as compulsórias, isto é, a primeira referência a cada bloco. Por isso LRU e FIFO empatam, e qualquer política seria ótima. Nas capacidades pequenas, a LRU foi melhor que a FIFO (39,6% contra 31,3% com 64 blocos). Na distribuição de Zipf, os blocos populares são referenciados repetidamente, e a LRU os mantém na cache, enquanto a FIFO acaba expulsando-os periodicamente apenas por serem antigos.

Sem cache, o disco satura: cada requisição leva cerca de 10,5 ms e chega uma a cada 8 ms, o que resulta em 3,5 s de resposta média. Qualquer cache, mesmo de 64 blocos, tira o disco da saturação e reduz a resposta de 3.500 ms para valores entre 23 e 75 ms.

O write-back reduziu as escritas físicas aproximadamente pela metade (de 1.045 para 541), porque regravações do mesmo bloco são agrupadas na cache. Em contrapartida, chegou a perder 55 blocos na queda de energia, enquanto o write-through não perdeu nenhum.

Um resultado contrariou a hipótese. Com 256 blocos ou mais, a resposta média do write-back (cerca de 45 ms) foi pior que a do write-through (entre 18 e 22 ms). A cada 5 s, o `sincronizar` coloca de uma só vez na fila dezenas de blocos sujos espalhados pelo disco, e com FCFS as leituras que chegam logo depois precisam esperar todas essas escritas. No extra E2, essas leituras levaram 310 ms, contra 22 ms das demais. Com capacidade de 64 blocos isso não ocorre, porque os blocos sujos são expulsos aos poucos, de forma contínua.

O E2 também mostra formas de atenuar o problema. Sincronizar com mais frequência (1 s) suaviza as rajadas, e usar SSTF reordena a rajada, reduzindo a resposta de 310 para 60 ms. Sincronizar raramente (30 s) quase elimina as escritas (228), mas os dados ficam por mais tempo apenas na memória. Esse compromisso entre desempenho e durabilidade também aparece nos sistemas reais (seção 4).

**Veredito:** PARCIALMENTE CONFIRMADA

### X7 — Escalonadores avançados e inanição (M2)

**Hipótese:** Com carga crescente, o SSTF terá a menor resposta média e a maior espera máxima. SCAN e C-SCAN terão espera máxima menor, mas mais deslocamento, e LOOK e C-LOOK serão melhores que SCAN e C-SCAN em todos os indicadores. O C-LOOK terá a espera mais uniforme. O DEADLINE terá a média do SSTF e limitará a espera das leituras a cerca de 200 ms.

**Fundamentação:** Silberschatz §10.4: o C-SCAN dá tempo de espera mais uniforme, e o LOOK e o C-LOOK evitam ir até a borda sem necessidade. O mq-deadline do Linux combina ordenação por posição com prazos por requisição (parâmetros do simulador: 200 ms para leituras e 2.000 ms para escritas).

**Configuração:** Carga concentrada gerada com semente 7 (2.000 requisições), intervalo médio de 14, 11, 9 e 8 ms (de 71 a 125 req/s, aproximadamente); 7 escalonadores; disco rígido.

**Resultados:** (resposta média / espera máxima, em ms; deslocamento em milhões de cilindros)

| Escalonador | 14 ms | 11 ms | 9 ms | 8 ms | Deslocamento a 8 ms |
|---|---|---|---|---|---:|
| FCFS | 15,8 / 71,3 | 26,5 / 128,2 | 61,2 / 244,5 | 281,6 / 616,2 | 1,81 |
| SSTF | 13,4 / 86,6 | 16,7 / 129,0 | 21,9 / 265,5 | 27,8 / 327,1 | 1,15 |
| SCAN | 29,5 / 114,4 | 34,6 / 127,9 | 41,8 / 210,0 | 49,0 / 226,7 | 1,53 |
| C-SCAN | 40,1 / 116,9 | 44,4 / 146,0 | 55,6 / 172,1 | 65,8 / 223,1 | 1,59 |
| LOOK | 13,8 / 72,8 | 17,1 / 105,1 | 24,6 / 161,9 | 31,4 / 170,3 | 1,17 |
| C-LOOK | 14,7 / 102,8 | 19,4 / 97,4 | 28,4 / 139,4 | 38,9 / 163,8 | 1,26 |
| DEADLINE | 13,4 / 86,6 | 16,7 / 129,0 | 22,0 / 204,2 | 28,4 / 244,8 | 1,15 |

Detalhe a 8 ms, calculado a partir das requisições concluídas: a espera máxima das leituras foi de 327,1 ms no SSTF e de 208,3 ms no DEADLINE. A espera máxima de 244,8 ms do DEADLINE corresponde a uma escrita, cujo prazo de 2 s ainda não havia vencido.

**Evidências:** resultados/es.csv (experimento=X7); graficos/X7_espera_max_ms.png; graficos/X7_resposta_p95_ms.png; graficos/X7_deslocamento_total.png.

**Análise:**

O SSTF teve a menor resposta média em todas as cargas, mas sua espera máxima cresceu com a carga, de 87 para 327 ms, o que mostra a inanição aparecendo à medida que a fila aumenta. O FCFS foi o mais justo com carga leve (espera máxima de 71 ms), mas seu desempenho piorou muito quando a fila cresceu, chegando a 616 ms com intervalo de 8 ms.

SCAN e C-SCAN tiveram as piores médias em quase todas as cargas, pelo mesmo motivo observado no X1: com a fila curta, o braço vai até a borda sem necessidade. O retorno do C-SCAN ao cilindro 0 faz com que ele tenha o maior deslocamento entre os dois.

LOOK e C-LOOK confirmaram o que o livro descreve. Eles tiveram deslocamento próximo ao do SSTF e espera máxima entre 48% e 50% menor que a dele com intervalo de 8 ms. O C-LOOK foi o mais uniforme sob carga alta, com as menores esperas máximas (139 e 164 ms).

O DEADLINE se comportou exatamente como o SSTF enquanto nenhum prazo venceu (intervalos de 14 e 11 ms). Sob carga alta, ele limitou a espera das leituras a cerca de 200 ms mais um atendimento (208 ms), quase sem custo na média (28,4 contra 27,8 ms). As escritas não foram limitadas, porque seu prazo é 10 vezes maior. A hipótese errou nesse ponto porque supunha um limite de espera para todas as requisições. A assimetria entre leituras e escritas é proposital no mq-deadline, já que as leituras costumam bloquear processos e as escritas, em geral, não.

**Veredito:** PARCIALMENTE CONFIRMADA

### X8 — DMA e buffering (M3)

**Hipótese:** O custo de CPU da interrupção crescerá linearmente com o tamanho da requisição, e o do DMA será constante. Por isso, o DMA vencerá a partir de requisições de poucos blocos, e o polling será sempre o pior no disco rígido. No buffering, o buffer duplo reduzirá o tempo total de `blocos × (p + c)` para aproximadamente `blocos × max(p, c)`, e o terceiro buffer não trará ganho com taxas constantes.

**Fundamentação:** Silberschatz §13.2.3 (no DMA, a CPU só programa a transferência e trata uma interrupção no fim) e §13.4.2 (o buffering duplo desacopla produtor e consumidor, e o circular generaliza essa ideia). Pelo modelo, a interrupção custa 0,017 ms por bloco, e o DMA, 0,018 ms por requisição.

**Configuração:** Carga aleatória (semente 11, 1.000 requisições, intervalo de 40 ms) com 1, 8, 64 e 256 blocos por requisição, nos modos polling, interrupção e DMA. Buffering: produção de 2 ms/bloco, gravação de 0,5, 2 ou 8 ms/bloco, 1, 2 e 3 buffers, 500 blocos.

**Resultados:**

| Blocos/req. | Polling (% CPU) | Interrupção (% CPU) | DMA (% CPU) |
|---:|---:|---:|---:|
| 1 | 25,17 | 0,042 | 0,045 |
| 8 | 25,87 | 0,338 | 0,045 |
| 64 | 31,44 | 2,706 | 0,045 |
| 256 | 50,52 | 10,820 | 0,045 |

| Gravação (ms/bloco) | Buffers | Tempo total (ms) | Bloqueio do produtor (ms) | Utilização do dispositivo (%) |
|---:|---:|---:|---:|---:|
| 0,5 | 1 / 2 / 3 | 1.250,0 / 1.000,5 / 1.000,5 | 249,5 / 0 / 0 | 20,0 / 25,0 / 25,0 |
| 2,0 | 1 / 2 / 3 | 2.000,0 / 1.002,0 / 1.002,0 | 998,0 / 0 / 0 | 50,0 / 99,8 / 99,8 |
| 8,0 | 1 / 2 / 3 | 5.000,0 / 4.002,0 / 4.002,0 | 3.992 / 2.988 / 2.980 | 80,0 / 99,95 / 99,95 |

**Evidências:** resultados/es.csv (experimento=X8); resultados/buffering.csv; graficos/X8_cpu.png; graficos/X8_buffering.png.

**Análise:**

O custo do DMA ficou constante em 0,045% da CPU, enquanto o da interrupção cresceu na mesma proporção do número de blocos, de 0,042% para 10,8% (256 vezes). Com apenas 1 bloco, a interrupção foi mais barata que o DMA (0,017 contra 0,018 ms), pois programar a controladora não compensa para uma transferência tão pequena. O ponto de equilíbrio é de 2 blocos, o que verificamos com um teste em `test_grupo.py`. Esse resultado está de acordo com o livro, que recomenda o DMA para transferências grandes. O polling variou entre 25% e 50% porque, como no X2, mede a ocupação do disco, que cresce com o tempo de transferência.

No buffering, com 1 buffer o produtor e o dispositivo se alternam e o tempo por bloco é a soma `p + c`. Com 2 buffers as duas atividades se sobrepõem e o tempo por bloco fica próximo de `max(p, c)`. Quando `p = c`, o tempo total caiu de 2.000 para 1.002 ms, com o dispositivo ocupado 99,8% do tempo. Com `c = 0,5`, o ganho foi menor (20%), porque o produtor já era o gargalo, e o buffer duplo é mais útil justamente quando as duas taxas são parecidas.

O terceiro buffer não alterou nenhum resultado com taxas constantes. Quando o dispositivo é o gargalo (8 ms por bloco), o produtor fica bloqueado de qualquer forma. Buffers adicionais só são úteis para absorver variações de taxa, como as rajadas de uma câmera.

**Veredito:** CONFIRMADA

### X9 — Links e compartilhamento (M4)

**Hipótese:** O hard link continuará funcionando depois do `unlink` do nome original (os blocos só são liberados quando `links` zera). O link simbólico para `b.txt` ficará pendente (ENOENT). O laço `l1 ↔ l2` dará ELOOP, e o hard link para diretório dará EPERM. A travessia detectará os ciclos criados por `/dados/sub/volta → /atalhos` e não entrará em laço. Abrir por um link simbólico custará mais leituras de diretório que pelo caminho direto.

**Fundamentação:** Silberschatz §11.3.3–11.3.4 (diretórios em grafo acíclico e em grafo geral): hard links exigem contagem de referências; links simbólicos podem ficar pendentes; um grafo com ciclos exige que a travessia marque o que já visitou; e hard links para diretórios são proibidos justamente para manter o grafo acíclico.

**Configuração:** Carga `fs_links`: 3 diretórios, 3 arquivos, 1 hard link, 4 links simbólicos (um para diretório, um que cria ciclo, dois em laço), 3 rodadas de leitura pelos links, `percorrer /` antes e depois de remover `a.txt` e `b.txt`, e `percorrer /atalhos`.

**Resultados:**

| Indicador | Valor |
|---|---:|
| Operações / erros | 56 / 3 (ELOOP 1, ENOENT 1, EPERM 1) |
| Diretórios lidos nas travessias (3 travessias) | 11 |
| Arquivos encontrados nas travessias | 9 |
| Ciclos (revisitas) detectados | 5 |
| Links pendentes (2 nas travessias + 1 no open) | 3 |
| Links em laço encontrados nas travessias | 6 |
| Acessos médios por percorrer / por open | 44,0 / 4,92 |

Leituras de diretório em cada `open` da carga:

| Caminho aberto | Leituras | Caminho direto equivalente |
|---|---:|---:|
| `/atalhos/a_rigido` (hard link) | 2 | 2 (`/dados/a.txt`) |
| `/atalhos/b_simbolico` | 4 | 2 (`/dados/b.txt`) |
| `/atalhos/dados_simbolico/sub/c.txt` | 5 | 3 (`/dados/sub/c.txt`) |
| `/atalhos/dados_simbolico/sub/volta/a_rigido` (2 links) | 7 | 2 |
| `/atalhos/l1` (laço, termina em ELOOP) | 18 | — |

**Evidências:** resultados/arquivos.csv (experimento=X9); resultados/arquivos_por_operacao.csv (experimento=X9); graficos/X9_links.png.

**Análise:**

Todos os erros previstos ocorreram, cada um uma única vez. O `a_rigido` continuou legível depois do `unlink /dados/a.txt`, porque o FCB tinha `links = 2` e os blocos permaneceram ocupados, enquanto o `b_simbolico` se tornou um ponteiro pendente.

A travessia terminou nas três execuções sem entrar em laço. Os 5 ciclos registrados contam todas as revisitas, como pede a regra. Entre eles está o ciclo verdadeiro (`/dados/sub/volta → /atalhos → dados_simbolico → /dados`), mas também revisitas inofensivas, como chegar novamente a `/atalhos` pela raiz depois de tê-lo visitado pelo link. Um `find -L` real distingue os dois casos comparando cada diretório com os ancestrais da travessia, e não com tudo o que já foi visitado.

Cada link simbólico faz a resolução recomeçar da raiz e pagar novamente os diretórios do alvo, como mostra a segunda tabela. O hard link não tem custo adicional, já que é apenas mais uma entrada de diretório apontando para o mesmo FCB. O link simbólico, por sua vez, custa a leitura dos diretórios do alvo desde a raiz. Por exemplo, abrir `.../c.txt` lê `/` e `/atalhos`, recomeça e lê `/`, `/dados` e `/dados/sub`. O `open` em laço lê `/` e `/atalhos` em cada um dos 9 recomeços até o ELOOP (9 × 2 = 18 leituras), e é ele que eleva a média para 4,92 leituras por `open`. Sem esse caso, a média dos outros 12 `open` é de 3,83.

Cada `percorrer` custou 44 acessos, dos quais 36 (82%) vêm dos dois links em laço, `l1` e `l2`, com 18 leituras cada até o ELOOP. Apenas 3 ou 4 acessos são leituras dos diretórios visitados, e o restante corresponde à resolução dos alvos dos demais links. Um laço de links simbólicos não trava a travessia, graças ao limite de saltos, mas encarece cada visita. Num sistema real, esse custo é absorvido pela memória (dcache), e o Linux aceita até 40 links numa única resolução antes de retornar ELOOP.

**Veredito:** CONFIRMADA

### X10 — Proteção e travas (M5)

**Hipótese:** Os acessos negados serão iguais nos dois modos de bloqueio, pois a proteção não depende da trava. As verificações custarão mais para usuários no fim da ACL ou fora dela. No modo consultivo, as operações de quem não tem a trava passarão (e serão contadas como violações); no mandatório, elas falharão com EBUSY.

**Fundamentação:** Silberschatz §11.6 (listas de acesso e bits rwx de dono, grupo e outros; a ACL permite controle fino, mas tem custo de busca e de tamanho) e §11.1.2 (travas consultivas × mandatórias). A permissão é verificada no `open`, e as operações seguintes usam o modo guardado no descritor.

**Configuração:** Carga `fs_protecao`: alice, bob e carol; `notas.txt` com permissão 600 e uma ACL de 10 usuários fictícios antes de `bob:r--`; `conta.txt` com permissão 666, disputada por P1 e P2 com `lock`/`unlock` em 20 rodadas. A mesma carga roda com bloqueio consultivo e com bloqueio mandatório.

**Resultados:**

| Indicador | Consultivo | Mandatório |
|---|---:|---:|
| Erros totais | 62 | 102 |
| EACCES (negados) | 41 | 41 |
| EPERM (chmod de não dono) | 1 | 1 |
| EBUSY | 20 | 60 |
| Travas concedidas / negadas | 40 / 20 | 40 / 20 |
| Violações consultivas | 40 | — |
| Operações barradas (mandatório) | — | 40 |
| Verificações de permissão (custo) | 961 | 961 |
| Tempo de disco total (ms) | 1.092,0 | 923,6 |

**Evidências:** resultados/arquivos.csv (experimento=X10); resultados/arquivos_por_operacao.csv (experimento=X10); graficos/X10_protecao_travas.png.

**Análise:**

As 41 negações foram idênticas nos dois modos: 1 de bob antes da criação da ACL, 20 de carol (que não está na ACL nem tem permissão como "outros") e 20 de bob pedindo escrita com uma ACL que só concede `r--`.

O total de 961 verificações se decompõe da seguinte forma. Cada `open` de bob custa 11 comparações, já que ele é a 11ª entrada da ACL. Cada `open` de carol ou de alice custa 12, porque percorre a ACL inteira e depois os bits. Os arquivos sem ACL custam 1. O custo da ACL cresce linearmente com a posição do usuário e com o tamanho da lista, e quem não está nela paga a varredura completa. Por esse motivo, os sistemas reais verificam a permissão apenas no `open` e guardam o resultado no descritor.

No modo consultivo, a trava só tem efeito entre processos que a solicitam. P2 tentou o `lock`, teve o pedido negado (20 EBUSY) e escreveu mesmo assim, e P1 também leu sem a trava, totalizando 40 violações que, num sistema real, poderiam corromper dados. No modo mandatório, essas 40 operações falharam com EBUSY e não chegaram ao disco, o que explica o tempo de disco 15% menor.

**Veredito:** CONFIRMADA

### X11 — Métodos de alocação (M6)

**Hipótese:** A contígua terá o acesso mais barato, mas sofrerá fragmentação externa, com realocações caras e falhas (ENOSPC) no crescimento com a partição a 85%, mesmo havendo espaço livre total. A encadeada não fragmenta, mas o acesso direto será muito caro, porque precisa percorrer a corrente. A FAT terá a mesma disposição sem os acessos extras e será a melhor no geral. A indexada terá 1 ou 2 leituras extras por bloco e um bom acesso direto.

**Fundamentação:** Silberschatz §12.4 (contígua: first-fit, fragmentação externa e dificuldade de crescer; encadeada: só o acesso sequencial é eficiente; FAT: ponteiros numa tabela que pode ficar em cache; indexada: acesso direto com sobrecarga do bloco de índice, em esquemas de vários níveis) e §12.5 (bitmap do espaço livre).

**Configuração:** Carga `fs_alocacao` numa partição de 20.000 blocos: arquivos de 1–256 blocos até 70% de ocupação, remoção de 40% deles, novo enchimento até 85%, 60 arquivos crescendo em modo acréscimo (2–32 escritas de 4 KiB) e 5 arquivos grandes com 100 leituras aleatórias e uma leitura sequencial em pedaços de 64 KiB.

**Resultados:**

| Alocador | Acessos/op | Tempo/op (ms) | Falhas (ENOSPC) | Realocações | Segmentos livres | Maior segmento | Descontinuidades/arquivo | Blocos de índice |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| contígua | 7,35 | 34,55 | 113 | 59 | 50 | 154 | 0,00 | — |
| encadeada | 77,52 | 12,73 | 0 | 0 | 1 | 1.939 | 0,64 | — |
| FAT | 1,90 | 4,01 | 0 | 0 | 1 | 1.939 | 0,64 | — |
| indexada | 4,29 | 14,32 | 0 | 0 | 1 | 1.805 | 0,65 | 134 |

Por operação: na contígua, o `write` custa 15,1 acessos e 78,0 ms; na encadeada, o `read` custa 74,9 acessos e o `write`, 142,9; na indexada, o `read` custa 6,1 acessos e 19,0 ms.

**Evidências:** resultados/arquivos.csv (experimento=X11); resultados/arquivos_por_operacao.csv (experimento=X11); graficos/X11_alocacao.png; graficos/X11_fragmentacao.png.

**Análise:**

Na alocação contígua, a fragmentação externa ficou evidente. Restaram 2.052 blocos livres divididos em 50 segmentos, o maior com apenas 154 blocos, e 113 escritas falharam por falta de espaço contíguo. As 59 realocações copiam o arquivo bloco a bloco, alternando leitura na posição antiga e escrita na nova, e cada cópia paga busca e rotação. Por isso o `write` custa 78 ms e a contígua teve o pior tempo por operação, apesar de ser muito eficiente na leitura.

A FAT foi a melhor alocação, com 1,9 acessos e 4,0 ms por operação, porque os ponteiros ficam na memória. Isso ajuda a entender por que a FAT continua sendo usada em mídias removíveis.

A encadeada fez 18 vezes mais acessos que a indexada e, mesmo assim, foi mais rápida em tempo (12,7 contra 14,3 ms por operação). Os blocos da corrente foram alocados pelo critério do primeiro livre e, em geral, são fisicamente consecutivos, de modo que ler a corrente custa 0,04 ms por bloco, sem rotação. A indexada, ao contrário, alterna entre a leitura do índice e a leitura do dado em posições diferentes e paga rotação a cada bloco: uma leitura sequencial de 64 KiB se transforma em 32 acessos alternados de cerca de 4,2 ms cada. A indexada também gastou 134 blocos com índices, o que corresponde a 0,7% do disco.

A encadeada teve desempenho muito ruim na escrita (142,9 acessos por `write`). Cada escrita de 4 KiB em modo acréscimo toca o bloco parcial do cursor e, pela regra de recomeçar quando i ≤ cursor, relê a corrente inteira.

Em resumo, nenhum método foi o melhor em todos os indicadores. A contagem de acessos, sozinha, não prevê o tempo, porque o padrão físico dos acessos (em sequência ou não) pesa tanto quanto o número deles.

**Veredito:** PARCIALMENTE CONFIRMADA

### X12 — Integração ponta a ponta

**Hipótese:** Sem cache, as cargas do sistema de arquivos vão gerar filas longas (as operações chegam a cada 0,5–2 ms e cada acesso aleatório leva cerca de 5 ms), e SSTF e SCAN ajudarão pouco, porque os acessos do compilador estão concentrados em poucos cilindros. A alocação indexada gerará mais requisições e mais tempo que a contígua. Com uma cache LRU de 1.024 blocos em write-back, o compilador terá alta taxa de acertos (reuso de cabeçalhos e diretórios), e o banco de dados, baixa (tabela de 16.384 blocos, muito maior que a cache).

**Fundamentação:** Silberschatz §12.6 (a buffer cache e a disposição física dos dados determinam o desempenho), §12.4.3 (sobrecarga do índice) e §10.4 (o escalonamento só ajuda quando há fila).

**Configuração:** As cargas `fs_compilador` e `fs_banco_dados` rodam na alocação contígua e na indexada, guardando o histórico. O histórico é convertido em requisições (`historico_para_requisicoes`), que passam por FCFS, SSTF e SCAN, com e sem cache LRU de 1.024 blocos em write-back.

**Resultados:**

| Carga + alocador | Requisições | Escalonador | Sem cache: resposta / tempo total | Com LRU: resposta / acertos (%) |
|---|---:|---|---|---|
| compilador + contígua | 5.400 | FCFS | 4.970,9 / 11.986,7 | 0,23 / 90,4 |
| compilador + contígua | 5.400 | SSTF | 5.170,1 / 9.988,0 | 0,15 / 90,4 |
| compilador + indexada | 11.705 | FCFS | 14.961,0 / 32.044,6 | 0,17 / 95,7 |
| compilador + indexada | 11.705 | SSTF | 10.563,6 / 28.847,7 | 0,15 / 95,7 |
| banco + contígua | 3.610 | FCFS | 6.158,8 / 19.847,0 | 2.151,3 / 4,9 |
| banco + contígua | 3.610 | SSTF | 4.106,2 / 15.415,1 | 1.411,9 / 4,8 |
| banco + indexada | 10.314 | FCFS | 22.146,1 / 52.179,6 | 765,1 / 74,5 |
| banco + indexada | 10.314 | SSTF | 17.353,2 / 42.512,1 | 509,5 / 74,6 |

Deslocamento no compilador + contígua com cache: FCFS 104, SSTF 80 e SCAN 49.996 cilindros. Escritas físicas com cache: 0 no compilador e entre 670 e 690 no banco.

**Evidências:** resultados/es.csv (experimento=X12); graficos/X12_resposta.png; graficos/X12_deslocamento.png.

**Análise:**

Sem cache, o disco satura em todas as combinações. O tempo total do compilador na alocação contígua com FCFS (11.986,7 ms) é praticamente igual ao tempo de disco medido no X3 (11.986,65 ms), o que significa que o disco ficou ocupado durante todo o tempo e as respostas chegaram à ordem de segundos.

Ainda sem cache, os escalonadores reduziram o tempo total do banco de dados em 22% (de 19,8 para 15,4 s), porque conseguem ordenar os acessos aleatórios dentro da tabela. No compilador, a redução foi de 17% e a resposta média nem chegou a melhorar, já que os dados ocupam poucos cilindros e o custo dominante é a rotação, não a busca. A alocação indexada sem cache teve o pior desempenho, com 2,2 vezes mais requisições e 2,7 vezes mais tempo que a contígua. Cada bloco de dados exige antes a leitura do bloco de índice, o que quebra a sequência física.

Com a cache, o comportamento do compilador muda completamente. A taxa de acertos fica entre 90% e 96%, a resposta cai para 0,15 a 0,23 ms e a diferença entre contígua e indexada desaparece. Os blocos de índice são os mais acessados da cache, e é por isso que a indexada chega a ter mais acertos (95,7%).

No banco de dados com cache, a taxa de acertos da contígua (4,9%) ficou próxima da razão entre a capacidade da cache e o tamanho da tabela (1.024 / 16.384 = 6,25%), como se espera para acessos uniformes. Na indexada, 74,5% de acertos vêm dos 17 blocos de índice, que aparecem em 2 de cada 3 requisições, e isso faz a indexada com cache ser mais rápida que a contígua com cache (510 contra 1.412 ms com SSTF). Na contígua, as escritas, somadas ao log, e as faltas continuam congestionando o disco.

Com a fila quase sempre vazia, o SCAN foi até a borda 5 vezes e percorreu 49.996 cilindros, contra 80 do SSTF. Por fim, o compilador não teve nenhuma escrita física porque a carga termina antes da primeira sincronização (5 s). Todos os arquivos `.o` gravados ainda estavam apenas na memória no fim da simulação, e uma queda de energia nesse momento os perderia.

**Veredito:** PARCIALMENTE CONFIRMADA

### X13 — Armazenamento flash (bônus)

**Hipótese:** O flash terá resposta uma a duas ordens de grandeza menor que o disco rígido, pois não há busca nem rotação. Como o dispositivo começa vazio e as cargas gravam poucos milhares de páginas, a coleta de lixo não será acionada no X13 (amplificação de escrita = 1, zero apagamentos). Com o dispositivo cheio (extra E1), a amplificação será alta e cairá com mais área de reserva.

**Fundamentação:** Silberschatz §10.1.2 (memória não volátil: sem partes móveis, apagamento por bloco, desgaste, FTL e coleta de lixo). A gravação só ocorre em página apagada, e o apagamento é feito por bloco de 64 páginas. Com a área de reserva de 7% do modelo, uma aproximação analítica conhecida para coleta gulosa com gravações uniformes dá amplificação de aproximadamente (1 + r) / (2r), cerca de 7,6.

**Configuração:** Cargas `es_reuso` e `es_aleatoria`, FCFS, disco rígido × flash (oficial). Extra E1: flash de 32.000 páginas lógicas, preenchido e com 64.000 regravações de envelhecimento, seguidas de 60.000 gravações aleatórias de 1 página, com reserva de 7%, 15% e 28%.

**Resultados:**

| Carga | Dispositivo | Resposta média | Resposta p95 | Páginas do host | Páginas programadas | Amplificação | Apagamentos |
|---|---|---:|---:|---:|---:|---:|---:|
| reuso | hdd | 3.500,0 | 6.427,5 | — | — | — | — |
| reuso | flash | 0,087 | 0,200 | 1.045 | 1.045 | 1,00 | 0 |
| aleatória | hdd | 50,1 | 145,1 | — | — | — | — |
| aleatória | flash | 0,644 | 1,600 | 4.792 | 4.792 | 1,00 | 0 |

| E1: reserva | Amplificação (100% / 50% regravado) | Apagamentos | Escrita média (ms) | Escrita p99 (ms) |
|---:|---|---:|---:|---:|
| 7% | 7,95 / 8,00 | 7.494 | 1,95 | 112,3 |
| 15% | 4,07 / 4,71 | 3.848 | 0,99 | 49,2 |
| 28% | 2,85 / 2,89 | 2.814 | 0,69 | 31,3 |

**Evidências:** resultados/es.csv (experimento=X13); graficos/X13_flash.png; resultados/extras/E1_flash_estado_estacionario.csv; graficos/extras/E1_flash_amplificacao.png.

**Análise:**

O flash respondeu entre 0,09 e 0,64 ms, contra 50 a 3.500 ms do disco rígido. Na carga `reuso`, o disco estava saturado e o flash não, já que no flash uma leitura custa 0,025 ms e uma gravação 0,2 ms, contra cerca de 10 ms no disco.

Como previsto, a amplificação no X13 foi 1,0 e não houve nenhum apagamento. As cargas gravam no máximo 4.792 páginas num dispositivo de 342.400, então a FTL apenas escreve em log em páginas novas e nunca precisa recuperar espaço. O X13 oficial, portanto, representa um SSD novo e não mostra o custo que aparece com o uso.

Esse custo aparece no estado estacionário do extra E1. Com reserva de 7%, cada página gravada pelo host exigiu cerca de 8 programações, valor próximo da aproximação teórica de 7,6. A escrita média subiu de 0,2 para 1,95 ms, e o percentil 99 chegou a 112 ms por causa das pausas da coleta, que ocorrem durante a própria gravação. Dobrar a reserva para 15% reduziu a amplificação pela metade, e com 28% ela caiu para cerca de 2,9, com os apagamentos (e portanto o desgaste) diminuindo na mesma proporção. Isso explica por que SSDs corporativos têm mais área de reserva (overprovisioning) e por que existe o comando TRIM, que informa à FTL quais páginas o sistema de arquivos já liberou.

Regravar apenas metade do espaço lógico não ajudou a coleta gulosa, porque ela não separa dados muito regravados (quentes) de dados pouco alterados (frios). FTLs reais fazem essa separação.

**Veredito:** CONFIRMADA

## 4. Relação com sistemas reais

**Escalonadores de E/S (X1, X7).** O mq-deadline do Linux, que serviu de base para o nosso DEADLINE, usa por padrão prazos de 500 ms para leituras e 5 s para escritas e atende lotes de requisições na ordem de posição ([kernel: mq-deadline.c](https://elixir.bootlin.com/linux/v5.8.7/source/block/mq-deadline.c)). O simulador reduz esses prazos para 200 ms e 2 s, mas mantém a mesma assimetria. O X7 mostra a razão dessa escolha: as leituras costumam bloquear processos, e o DEADLINE limitou a espera delas sem perder a média do SSTF. O BFQ privilegia a latência de aplicações interativas e distribui a banda por processo, aceitando alguma perda de vazão ([kernel: BFQ](https://kernel.org/doc/html/v6.15/block/bfq-iosched.html)), o que ataca a inanição observada no SSTF no X1 (carga concentrada) e no X7. Para SSDs NVMe, costuma-se usar o escalonador `none`, sem reordenação, porque o dispositivo não tem braço e possui filas internas profundas. O X13 ajuda a entender essa escolha, já que sem busca nem rotação não há ganho em reordenar. Algumas distribuições adotam o BFQ para discos rotacionais e o mq-deadline ou `none` para os demais ([SUSE: Tuning I/O](https://documentation.suse.com/sles/html/SLES-all/cha-tuning-io.html)). Windows e macOS não oferecem ao usuário uma escolha equivalente de escalonador, e boa parte da reordenação fica a cargo do próprio dispositivo (NCQ e filas NVMe).

**Polling × interrupção × DMA (X2, X8).** O Linux oferece polling de conclusão para dispositivos de baixíssima latência (`io_poll`) e um polling híbrido, que espera o tempo médio previsto antes de começar a testar o dispositivo ([LWN: block layer](https://lwn.net/Articles/735275/)). Isso está de acordo com a conclusão do X2: o polling compensa quando o dispositivo responde em menos tempo que o custo de uma interrupção. Frameworks como o SPDK levam essa ideia ao extremo, com drivers NVMe que não usam interrupção. O DMA é usado em praticamente todos os discos, dispositivos NVMe e placas de rede (bus mastering), e o X8 mostra que ele só compensa a partir de transferências maiores que um bloco.

**Cache de páginas e gravação periódica (X6, X12, E2).** O Linux acorda periodicamente as threads de flush (`dirty_writeback_centisecs`), grava os dados sujos mais antigos (`dirty_expire_centisecs`) e impõe limites ao volume de dados sujos (`dirty_background_ratio`, `dirty_ratio`) ([kernel: sysctl/vm](https://www.kernel.org/doc/Documentation/sysctl/vm.txt)). O lazy writer do Windows roda a cada segundo e grava uma fração (1/8) das páginas sujas, em vez de despejar todas de uma vez ([Microsoft: File Caching](https://learn.microsoft.com/en-us/windows/win32/FileIO/file-caching)). Essa é a mesma forma de atenuar as rajadas que medimos no E2: gravar pouco e com frequência evita os picos que, no nosso modelo de sincronização total a cada 5 s, multiplicaram por 14 a resposta das leituras. Em todos esses sistemas, dados em write-back se perdem numa queda de energia (no X6, até 55 blocos), e por isso existem o `fsync()` e o write-through por arquivo (`FILE_FLAG_WRITE_THROUGH` no Windows). No macOS, o `fsync()` entrega os dados ao disco, mas não garante que o dispositivo os grave fisicamente, e para isso existe o `F_FULLFSYNC` ([Apple: fsync(2)](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)). Essa camada de cache do próprio dispositivo não existe no nosso modelo.

**Resolução de caminhos e cache de diretórios (X4, X9).** O Linux mantém o dcache, uma cache de nomes (dentries) que torna a resolução de caminhos quase sempre independente do disco ([kernel: path-lookup](https://www.kernel.org/doc/Documentation/filesystems/path-lookup.md)). No nosso modelo, que não tem essa cache, cada nível custa uma leitura (X4) e cada link simbólico recomeça a busca pela raiz (X9). No Windows, esse papel é cumprido pelas caches do gerenciador de objetos e do NTFS, o que medimos no extra E3: no Windows 11 com NTFS, o `open` levou 27 µs no SSD e 41 µs no HDD, sem variar de 1 a 10 níveis, contra 4,25 a 4,61 ms no simulador. O Linux também limita a quantidade de links simbólicos seguidos numa resolução a 40 antes de retornar ELOOP, enquanto o nosso limite é de 8.

**Tabelas de arquivos abertos (X5).** No Unix, cada processo tem uma tabela de descritores que aponta para descrições de arquivo aberto (`struct file`, com posição e modo), que por sua vez apontam para o inode compartilhado. No Windows, a tabela de handles do processo aponta para objetos de arquivo, e um bloco de controle por arquivo é compartilhado. Em ambos os casos há a mesma divisão em dois níveis observada no X5. Uma diferença é que, no Unix, após um `fork` pai e filho compartilham a mesma posição, caso que o modelo não simula.

**Proteção (X10).** O Linux usa bits rwx e, opcionalmente, ACLs POSIX, enquanto NTFS e APFS usam ACLs mais completas (o APFS suporta permissões Unix e ACLs NFSv4). Todos verificam a permissão no `open` e guardam o modo no descritor, como no modelo. O custo linear da ACL que medimos também existe nos sistemas reais, e por isso as ACLs costumam ser curtas e baseadas em grupos.

**Travas (X10).** No Windows, as travas de `LockFileEx` são mandatórias: um `ReadFile` ou `WriteFile` de outro processo na região travada falha com `ERROR_LOCK_VIOLATION` ([Microsoft: Locking byte ranges](https://learn.microsoft.com/en-ie/Windows/Win32/fileio/locking-and-unlocking-byte-ranges-in-files)), comportamento equivalente ao EBUSY do nosso modo mandatório. No Linux, as travas (`flock`, `fcntl`) são consultivas, e o suporte a travas mandatórias foi removido do kernel na versão 5.15 ([LKML: fcntl.2](https://lkml.iu.edu/hypermail/linux/kernel/2110.0/00378.html)). O macOS segue o modelo BSD, também consultivo. O X10 ilustra o risco desse modelo, já que no modo consultivo 40 operações ignoraram a trava.

**Alocação (X11, X12).** A FAT (FAT32 e exFAT) continua sendo o padrão de pendrives e cartões de memória: por especificação, cartões SDHC usam FAT32 e SDXC usam exFAT ([SanDisk: SDHC × SDXC](https://www.sandisk.com/topics/memory-cards/sdhc-vs-sdxc-comparison)). A simplicidade de manter a tabela em memória foi justamente o que favoreceu a FAT no X11. O ext2 e o ext3 usavam blocos de índice diretos, indiretos e duplamente indiretos (até 1.024 ponteiros por bloco de 4 KiB, como no nosso índice), e o ext4 substituiu esse esquema por extents, intervalos contíguos de blocos, com até 4 extents dentro do próprio inode ([kernel: ext4 i_block](https://docs.kernel.org/filesystems/ext4/ifork.html)). Essa solução combina as vantagens da alocação contígua (sequência física e poucos metadados) com a flexibilidade da indexada, e evita o problema observado no X11, em que reler o índice quebrava a sequência. No NTFS, a MFT guarda os metadados e, no caso de arquivos pequenos, os próprios dados (atributos residentes), economizando um acesso ([NTFS.com: MFT](https://www.ntfs.com/ntfs-mft.htm)). O APFS usa copy-on-write nos metadados e clones que compartilham blocos ([Wikipedia: APFS](https://en.wikipedia.org/wiki/Apple_File_System)), seguindo a mesma ideia da FTL do X13 de nunca sobrescrever dados no lugar.

**Flash (X13, E1).** SSDs e o armazenamento de celulares (UFS e eMMC) têm uma FTL semelhante à nossa, com coleta de lixo, nivelamento de desgaste e área de reserva. O TRIM permite que o sistema informe ao dispositivo quais páginas estão livres, reduzindo a amplificação medida no E1.

## 5. Limitações do simulador

O modelo do disco é simplificado. A busca é linear na distância, quando na prática se aproxima de √d para distâncias curtas, e a rotação é sempre de meia volta, sem depender da posição angular. Também não há cache no disco nem NCQ, e em discos reais o próprio dispositivo reordena as requisições, o que reduziria a diferença entre os escalonadores no X1 e no X7. Além disso, a regra de que a rotação só é zero no bloco imediatamente seguinte exagera o ganho da sequência física, e é ela que explica a diferença de 590 vezes no X1 sequencial e os 0,04 ms por nível do X4.

A camada de blocos do sistema de arquivos não tem cache de diretórios, de inodes nem de índices, o que aumenta o custo do `open` (X4), dos links (X9) e da alocação indexada (X11). Na integração (X12), a cache LRU mostrou o quanto essa ausência altera as conclusões. O extra E3 quantifica essa distorção numa máquina real, onde o `open` leva cerca de 27 µs no SSD ou 41 µs no HDD, independentemente da profundidade, aproximadamente 160 vezes menos que no modelo. As conclusões do X4 e do X9 sobre a contagem de acessos valem, portanto, para o primeiro acesso a um caminho (cache fria), já que os acessos seguintes quase nunca chegam ao disco.

No modelo, os metadados ficam no diretório e não num inode. Pela regra, o `close` de um arquivo sujo grava o bloco do diretório onde ele foi criado (`fcb.pai`). Com hard links (X9), isso continua valendo mesmo que o arquivo tenha sido aberto por outro nome ou que o nome original já tenha sido removido. No Unix, tamanho e datas ficam no inode, separados das entradas de diretório, e um nome a mais não muda onde os metadados são gravados.

Nas cargas de arquivos, os instantes das operações são fixos (0,5 a 2 ms entre operações) e não esperam o disco. No X12, isso cria filas de vários segundos que não ocorreriam na prática, pois um processo real esperaria o `read` terminar antes de emitir a próxima operação.

O write-back também é simplificado. A sincronização é total a cada 5 s, sem limite de blocos sujos nem gravação por idade, o que torna as rajadas do X6 e do E2 mais intensas do que seriam no Linux ou no Windows. O fim da simulação não faz flush, e por isso o X12 mostra 0 escritas físicas no compilador.

Algumas regras do enunciado diferem do que os sistemas reais fazem, como recomeçar a corrente da encadeada quando i ≤ cursor (X11) e contar toda revisita como ciclo (X9). Os números refletem essas regras, e discutimos o efeito em cada experimento.

Também não modelamos os lotes (`fifo_batch`) nem a separação entre filas de leitura e de escrita do mq-deadline real, nem a disputa pela CPU ou o tempo gasto nas próprias decisões do escalonador. No flash, não há TRIM, separação entre dados quentes e frios nem paralelismo entre chips. O X13 oficial usa um dispositivo novo (amplificação de escrita igual a 1), e o estado estacionário só aparece no extra E1, com um dispositivo menor.

Por fim, as cargas sintéticas (Zipf, exponencial e concentrada 80/20) são apenas aproximações de cargas reais, e como usamos uma única semente por carga não é possível calcular intervalos de confiança.

## 6. Conclusões

Num disco rígido, o escalonador só faz diferença quando há fila, e o maior ganho vem de preservar a sequência física dos blocos, o que elimina a rotação. Reduzir a distância percorrida pelo braço ajuda menos do que se poderia esperar, porque a rotação e a busca mínima são custos fixos (X1).

Entre os escalonadores, SSTF e DEADLINE obtiveram as menores médias, e o DEADLINE ainda limita a espera das leituras sem perder desempenho médio. LOOK e C-LOOK representaram o melhor equilíbrio entre desempenho e justiça, enquanto SCAN e C-SCAN tiveram desempenho ruim com carga leve, por irem até a borda sem necessidade (X1 e X7).

O polling só compensa em dispositivos com latência de microssegundos, e o DMA só a partir de dois blocos. O buffer duplo praticamente dobra a vazão quando produtor e dispositivo têm a mesma velocidade, e um terceiro buffer não traz ganho com taxas constantes (X2 e X8).

A cache foi a otimização de maior impacto. Ela tira o disco da saturação e faz as diferenças entre métodos de alocação praticamente desaparecerem. O write-back reduz o número de escritas, mas em troca perde dados numa queda de energia e cria picos de latência a cada sincronização (X6, X12 e E2).

Os metadados têm custo elevado: a profundidade do caminho, os links simbólicos e os blocos de índice acrescentam acessos. Como esses acessos podem ou não cair em sequência física, a contagem de acessos sozinha não é suficiente para prever o tempo (X4, X9 e X11).

Por fim, um SSD novo não mostra o custo da coleta de lixo. Em uso contínuo, a amplificação de escrita chegou a cerca de 8 com 7% de reserva e diminuiu com o aumento da área de reserva (X13 e E1).

## 7. Referências

- SILBERSCHATZ, A.; GALVIN, P. B.; GAGNE, G. *Operating System Concepts*. 9. ed. Wiley, 2013. Seções 10.1, 10.4, 11.1–11.6, 12.4–12.6, 13.2–13.4.
- Linux kernel — mq-deadline (código-fonte, valores `read_expire` e `write_expire`): https://elixir.bootlin.com/linux/v5.8.7/source/block/mq-deadline.c
- Linux kernel — BFQ (Budget Fair Queueing): https://kernel.org/doc/html/v6.15/block/bfq-iosched.html
- SUSE — System Analysis and Tuning Guide, Tuning I/O performance: https://documentation.suse.com/sles/html/SLES-all/cha-tuning-io.html
- LWN — Improvements in the block layer (polling e polling híbrido): https://lwn.net/Articles/735275/
- Linux kernel — Documentation for /proc/sys/vm (`dirty_*`): https://www.kernel.org/doc/Documentation/sysctl/vm.txt
- Microsoft — File Caching (lazy writer): https://learn.microsoft.com/en-us/windows/win32/FileIO/file-caching
- Apple — fsync(2) e F_FULLFSYNC: https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html
- Linux kernel — Pathname lookup (dcache): https://www.kernel.org/doc/Documentation/filesystems/path-lookup.md
- Linux man-pages — path_resolution(7) (limite de links simbólicos por resolução): https://man7.org/linux/man-pages/man7/path_resolution.7.html
- Microsoft — Locking and Unlocking Byte Ranges in Files: https://learn.microsoft.com/en-ie/Windows/Win32/fileio/locking-and-unlocking-byte-ranges-in-files
- Linux man-pages — remoção das travas mandatórias no Linux 5.15: https://lkml.iu.edu/hypermail/linux/kernel/2110.0/00378.html
- Linux kernel — ext4, The Contents of inode.i_block (blocos indiretos e extents): https://docs.kernel.org/filesystems/ext4/ifork.html
- NTFS.com — NTFS Master File Table: https://www.ntfs.com/ntfs-mft.htm
- Wikipedia — Apple File System: https://en.wikipedia.org/wiki/Apple_File_System
- SanDisk — SDHC vs SDXC (FAT32 × exFAT): https://www.sandisk.com/topics/memory-cards/sdhc-vs-sdxc-comparison

## 8. Declaração de autoria e uso de IA

Diogo: escalonadores (N2 e M2), os experimentos X1, X2 e X7, o repositório e a organização da entrega.
Pedro: sistema de arquivos e links (N3 e M4), os experimentos X3, X4, X5 e X9.
Elisa: cache, DMA e buffering (M1 e M3), a integração e os experimentos X6, X8 e X12.
Maria Júlia: proteção e travas (M5), alocação (M6), o bônus do flash, os experimentos X10, X11 e X13 e a pesquisa sobre sistemas reais da seção 4.


| Ferramenta | Finalidade | Onde foi usada (arquivos ou seções) |
|---|---|---|
| Claude (Anthropic), Opus 5.5 | Implementação do código a partir das regras do enunciado e dos comentários dos arquivos; testes adicionais; script de experimentos extras; gráficos adicionais; redação inicial do relatório (hipóteses formuladas a partir do Silberschatz, análises e pesquisa sobre sistemas reais) | `simes/es/escalonadores.py`, `simes/es/cpu.py`, `simes/es/cache.py`, `simes/es/buffering.py`, `simes/arquivos/sistema.py`, `simes/arquivos/alocacao.py`, `simes/integracao.py`, `simes/disco.py` (DiscoFlash), `testes/test_grupo.py`, `extras/experimentos_extras.py`, `graficos.py` (gráficos acrescentados), seções 0 a 7 deste relatório |
| Claude (Anthropic), Opus 5.5 | Revisão final: conferência dos números do relatório contra `resultados/*.csv` e da reprodutibilidade dos experimentos; correção de imprecisões nas análises do X4 e do X9 (a partir de um rastreamento operação a operação das cargas); script e redação do extra E3 (medição real do `open`); explicação do código do N3/M4 para estudo da arguição | `extras/e3_open_real.py`, `resultados/extras/E3_open_real.csv`, `graficos/extras/E3_open_real.png`, seções 1, 3 (X4 e X9), 4, 5, 7 e 8 deste relatório |
