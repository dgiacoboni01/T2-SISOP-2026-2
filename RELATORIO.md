# Relatório — SimES: simulador de E/S e de sistema de arquivos

**Integrantes:** [NOME COMPLETO DO INTEGRANTE 1, NOME COMPLETO DO INTEGRANTE 2, ...]

**Repositório:** [https://github.com/USUARIO/REPOSITORIO]

## 0. Resumo

Implementamos todo o SimES: o núcleo (SSTF, SCAN, custo de CPU por polling e por interrupção, sistema de arquivos com tabelas de arquivos abertos e `unlink` adiado), os seis módulos (M1 cache LRU/FIFO, M2 C-SCAN/LOOK/C-LOOK/DEADLINE, M3 DMA e buffering, M4 links, M5 proteção e travas, M6 alocação encadeada/FAT/indexada), a integração ponta a ponta e o bônus de armazenamento flash (FTL em log com coleta de lixo gulosa). Os 50 testes passam (26 fornecidos e 24 nossos), e os 13 experimentos rodam. Os três resultados mais importantes:
(1) Num disco rígido, o que pesa é a latência rotacional, não o deslocamento do braço. SSTF/SCAN cortam o deslocamento em até 16 vezes nas rajadas, mas a resposta cai só 45%. A maior vitória é manter a sequência física, que zera a rotação: no X1 sequencial, FCFS leva 10,2 s de resposta média e SSTF leva 17 ms.
(2) Com write-back, a sincronização periódica despeja de uma vez os blocos sujos e cria "tempestades" de escrita. As leituras que chegam logo depois esperam 14 vezes mais (X6 + extra E2). E uma queda de energia perde até 55 blocos.
(3) Os metadados ditam o desempenho: o índice relido a cada acesso faz a alocação indexada ficar 2,7 vezes mais lenta que a contígua na integração. Com uma cache LRU, os blocos de índice ficam quentes e a diferença praticamente some (X11, X12).

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

Resultado dos testes automáticos (`python3 executar.py testes`): `Ran 50 tests in 0.085s — OK` (nenhum teste pulado; 24 dos 50 testes são nossos, em `testes/test_grupo.py`).

Experimentos extras (declarados, em configuração separada): `extras/experimentos_extras.py` grava em `resultados/extras/` e `graficos/extras/`. Não alteram `parametros.py`, as cargas nem `experimentos.py`; os resultados oficiais são os de `resultados/*.csv`.

## 2. Implementação

### N2 — Subsistema de E/S

**Onde:** `simes/es/escalonadores.py` (`SSTF`, `SCAN` e as funções auxiliares `_mais_proxima`, `_a_frente`, `_direcao_do_movimento`); `simes/es/cpu.py` (`custo_cpu`).

**Decisões:** Todos os escalonadores usam um único auxiliar, `_mais_proxima(fila, cabeca, filtro)`, que percorre a fila na ordem de chegada e só troca de candidata quando a distância é *estritamente* menor. Assim, a regra de empate (vence a que chegou antes) vale automaticamente. O filtro `_a_frente` implementa a regra comum de que uma requisição no mesmo cilindro da cabeça está "à frente" nas duas direções.
- **SSTF:** escolhe a mais próxima sem filtro. A direção devolvida é a do movimento (+1/−1), ou a atual se o alvo está no mesmo cilindro.
- **SCAN:** procura a mais próxima à frente. Se não houver, devolve `passar_por=[borda]` (0 ou `max_cilindro`), inverte a direção e escolhe a mais próxima *a partir da borda* na nova direção, que é a mesma requisição que estaria mais perto da cabeça entre as de trás.
- **Polling:** custa `duracao_ms` (espera ocupada durante todo o atendimento). **Interrupção:** custa `n_blocos × (CUSTO_INTERRUPCAO_MS + CUSTO_COPIA_MS_POR_BLOCO)`.

**Dificuldades:** No SCAN, a dúvida era a partir de onde medir a "mais próxima" depois de inverter. Medir da borda e medir da cabeça dão a mesma escolha, porque depois da inversão todas as requisições estão do mesmo lado. Conferimos com o exemplo do livro: 236 cilindros descendo e 331 subindo, com borda em 199.

### N3 — Sistema de arquivos

**Onde:** `simes/arquivos/sistema.py`: `_resolver_partes`, `_pai_e_nome`, `_criar_entrada`, `resolver`, `mkdir`, `create`, `open`, `read`, `write`, `seek`, `close`, `unlink`.

**Decisões:**
- **Resolução de caminhos (`_resolver_partes`):** antes de procurar cada componente, lê o diretório onde ele está (1 leitura). Por isso `/a/b/c` custa 3 leituras e `/` não custa nada. Componente ausente dá ENOENT; atravessar um arquivo dá ENOTDIR. Toda operação que cria ou remove uma entrada usa `_pai_e_nome`, que resolve o pai como o `resolver` e depois lê o próprio pai: é o "até o pai, inclusive" das regras (`create /a/b/f.txt` custa 3 leituras).
- **mkdir/create:** alocam pelo alocador (1 bloco para diretório, `ceil(tamanho/4096)` para arquivo), aplicam os extras do alocador e gravam o(s) bloco(s) de diretório exigido(s). Se o alocador lança ENOSPC, o FCB recém-criado é descartado, para não deixar FCB órfão.
- **Tabelas de arquivos abertos:** `tabela_global[fcb_id]` guarda uma `EntradaGlobal` com `contador`; `tabelas_processo[proc][handle]` guarda uma `EntradaProcesso` com modo e posição. Dois processos que abrem o mesmo arquivo compartilham a entrada global (contador 2) e têm posições independentes. Abrir um *handle* que o processo já usa dá EINVAL. Quando a tabela de um processo esvazia, ela é removida.
- **read/write:** calculam os blocos lógicos do intervalo `[posição, posição+n)` (em `read`, limitado ao tamanho). O `write` primeiro estende o arquivo com `alocador.estender` se faltarem blocos e depois grava. O modo `'a'` reposiciona no fim antes de cada escrita, como `O_APPEND`. Toda escrita marca o FCB como sujo; o `close` grava então o bloco do diretório pai (1 escrita).
- **unlink com arquivo aberto:** remove a entrada e grava o pai. `links` cai e, se chegar a 0, os blocos só são liberados se não houver entrada na tabela global. Caso contrário, quem libera é o `close` que zerar o contador. Se esse arquivo removido estiver sujo, o `close` não grava o pai, porque o arquivo já não tem entrada de diretório.
- `unlink` de diretório dá EISDIR, como no Linux.

**Dificuldades:** O ponto delicado foi o custo exato de cada operação. Por exemplo, o `open` não escreve nada, e o `close` só escreve se houve escrita. Os testes do núcleo conferem cada um desses custos.

### M1 — Cache de blocos

**Onde:** `simes/es/cache.py` (`_CacheOrdenada`, `CacheLRU`, `CacheFIFO`).

**Decisões:** Uma base comum guarda um `OrderedDict` bloco → sujo, cuja ordem é a ordem de expulsão (a vítima é o primeiro item). A LRU chama `move_to_end` em todo acerto e em toda escrita; a FIFO não reordena nunca. No write-through, `escrever` devolve `[bloco]` e o bloco nunca fica sujo. No write-back, o bloco é marcado como sujo e só vai ao disco quando é expulso ou no `sincronizar`. A expulsão devolve apenas as vítimas sujas.

**Dificuldades:** Uma regravação de um bloco que já está na cache não pode expulsar ninguém. Há um teste nosso para isso.

### M2 — Escalonamento avançado

**Onde:** `simes/es/escalonadores.py` (`CSCAN`, `LOOK`, `CLOOK`, `Deadline`).

**Decisões:** O C-SCAN sem requisições à frente devolve `passar_por=[max_cilindro, 0]`, de modo que o retorno conta como deslocamento. O LOOK inverte onde está. O C-LOOK salta para a requisição de menor cilindro (empate: a mais antiga). O DEADLINE procura a requisição vencida (`agora >= prazo`) de prazo mais antigo e mantém a direção; sem nenhuma vencida, delega ao SSTF. Os valores do livro conferem: C-SCAN 382, LOOK 299 e C-LOOK 322.

**Dificuldades:** Nenhuma relevante.

### M3 — DMA e buffering

**Onde:** `simes/es/cpu.py` (modo `dma`) e `simes/es/buffering.py`.

**Decisões:** O DMA custa `CUSTO_PROGRAMAR_DMA_MS + CUSTO_INTERRUPCAO_MS`, qualquer que seja o tamanho. O buffering é uma recorrência direta das regras: o bloco *k* começa a ser gerado em `max(fim_produção[k−1], fim_gravação[k−n])`, a espera entra em `bloqueio_produtor_ms` e a gravação começa em `max(fim_produção[k], fim_gravação[k−1])`.

**Dificuldades:** Nenhuma relevante; os testes de 1 e 2 buffers conferem com fórmulas fechadas.

### M4 — Links e compartilhamento

**Onde:** `simes/arquivos/sistema.py` (`_resolver_partes`, `link`, `symlink`, `percorrer`).

**Decisões:**
- **Link simbólico na resolução:** quando um componente é simbólico (e não é o último ou `seguir_simbolico=True`), os componentes do alvo substituem o link no início da lista de pendentes e a busca recomeça da raiz, pagando de novo a leitura dos diretórios do alvo. Mais de 8 links seguidos dá ELOOP.
- **Link pendente:** para distinguir um link pendente de um ENOENT comum, contamos quantos dos próximos componentes vieram de um alvo. Se o nome ausente veio de um alvo, o erro sai marcado (`pendente=True`), e o `open` conta `links_pendentes`.
- **Hard link:** `link` não segue o link simbólico do último componente (como o `link()` do Linux), recusa diretórios com EPERM e incrementa `links`.
- **`percorrer`:** é uma DFS com um conjunto `visitados` por travessia. Revisitar um diretório conta `ciclos_detectados` e não desce. Links simbólicos são seguidos como no `find -L`: alvo inexistente conta `links_pendentes`, e alvo em laço conta `links_em_laco`, um indicador nosso.

**Dificuldades:** Pela regra do enunciado, toda revisita conta como "ciclo", inclusive chegar a `/atalhos` pela segunda vez sem que exista laço. Discutimos isso no X9.

### M5 — Proteção e travas

**Onde:** `simes/arquivos/sistema.py` (`_verificar_permissao`, `_verificar_trava`, `usuario`, `chmod`, `setacl`, `lock`, `unlock`). Os dois ganchos são redefinidos na seção do M5 da própria classe e, por virem depois, substituem as versões vazias.

**Decisões:**
- **Verificação de permissão:** root sempre passa (conta 1). Se a ACL tem entrada para o usuário, vale a primeira, e a posição dela é somada a `verificacoes`. Senão, valem os bits do dono ou de "outros", e soma-se `len(acl)+1`.
- **chmod:** aceita `'640'` (texto) e `640` (o simulador converte argumentos numéricos em `int`), sempre interpretando em octal.
- **Travas:** com bloqueio consultivo, ler ou escrever um arquivo travado por outro processo só conta uma violação. Com bloqueio mandatório, dá EBUSY. O `close` do dono libera a trava, como o `fcntl` do POSIX, que solta as travas do processo em qualquer `close`. Destravar sem ser o dono não faz nada.

**Dificuldades:** A conversão automática de `'600'` para o inteiro 600 pelo simulador, que, sem tratamento, viraria 0o1130.

### M6 — Alocação em disco

**Onde:** `simes/arquivos/alocacao.py` (`AlocadorEncadeado`, `AlocadorFAT`, `AlocadorIndexado`).

**Decisões:**
- **Encadeada:** usa `primeiros_livres`. `estender` devolve 1 escrita no antigo último bloco. `bloco_fisico` segue a regra do cursor ao pé da letra: se *i* ≤ cursor, recomeça do bloco 0.
- **FAT:** herda a alocação da encadeada, mas não tem extras.
- **Indexada:** precisa de `1` bloco de índice, ou de `1 + ceil(n/1024)` quando *n* > 1024. Ao estender de 1 para 2 níveis, o índice antigo passa a ser o de 1º nível e os novos são de 2º nível. Os extras são uma escrita por bloco de índice. Acrescentamos o indicador `blocos_indice`.

**Dificuldades:** A regra "i ≤ cursor recomeça" penaliza escritas desalinhadas em modo acréscimo: cada `write` de 4 KiB toca de novo o bloco do cursor e relê a corrente inteira. Mantivemos a regra (todos os grupos devem obter os mesmos números) e analisamos o efeito no X11.

### Integração ponta a ponta

**Onde:** `simes/integracao.py`.

**Decisões:** Ordenamos o histórico pelo instante com `sorted`, que é estável. Assim, os acessos da mesma operação (mesmo instante) mantêm a ordem em que o sistema de arquivos os emitiu. Os ids são 0, 1, 2…

**Dificuldades:** Nenhuma relevante.

### Bônus — DiscoFlash

**Onde:** `simes/disco.py` (`DiscoFlash`).

**Decisões:**
- **FTL em log:** um mapa lógico → físico (dicionário esparso) e o mapa reverso por página física. Cada bloco apagável tem um contador de páginas válidas e um contador de desgaste.
- **Gravação:** toda gravação vai para a próxima página livre do bloco ativo e invalida a página antiga.
- **Coleta de lixo:** quando sobram até 2 blocos apagados (reserva), a coleta gulosa escolhe o bloco cheio com menos páginas válidas (empate: o menos desgastado), copia as válidas (leitura + programação) e apaga o bloco. O tempo da coleta é cobrado da gravação que a disparou.
- **Leitura:** custa `n × 0,025 ms`.
- **Área física:** `ceil(320.000 × 1,07)` páginas, arredondada para blocos de 64 páginas (5.350 blocos).
- **Extras:** `preencher()` envelhece o dispositivo, mas só é usado no experimento extra E1. Expomos também `cilindros`, porque o simulador de E/S consulta esse atributo.

**Dificuldades:** Garantir que a coleta nunca fica sem página livre no meio da cópia; a reserva de 2 blocos garante isso. Há um teste que confere que nenhum dado se perde depois de milhares de coletas.

## 3. Experimentos

Todos os tempos estão em ms. "Resposta" é o intervalo entre a chegada e o fim do atendimento; "espera" é o intervalo entre a chegada e o início. A taxa de ocupação do disco foi estimada pela % de CPU do polling no X2 (no polling, a CPU fica ocupada exatamente enquanto o disco trabalha).

### X1 — Escalonadores sob quatro padrões de carga

**Hipótese:** SSTF e SCAN terão deslocamento e resposta média bem menores que o FCFS nas cargas com fila (sequencial e rajadas) e ganho pequeno na aleatória e na concentrada, onde a fila é curta. O SSTF terá a menor resposta média e a maior espera máxima (inanição). O SCAN terá espera máxima menor que o SSTF.

**Fundamentação:** Silberschatz §10.4: o FCFS é justo mas não otimiza o movimento do braço; o SSTF é guloso e pode causar inanição; o SCAN limita a espera a cerca de duas varreduras. Pelo modelo (`parametros.py`), cada acesso fora de sequência paga 1 ms de busca mínima + 0,003 ms/cilindro + 4,17 ms de rotação. O ganho de reordenar depende de haver fila para reordenar.

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
- **Sequencial:** o FCFS colapsa. Os processos P1 (cilindro ≈312) e P2 (≈3.906) se alternam, então cada requisição atravessa ~3.600 cilindros e paga 1 + 10,8 + 4,17 + 0,32 ≈ 16,3 ms. Como chega uma requisição a cada 6 ms em média, o disco satura: ocupação de 99,998% no X2, e as chegadas terminam em 11,9 s, mas o disco só termina em 32,5 s. Com SSTF ou SCAN, o braço fica num dos fluxos, e cada requisição seguinte começa no bloco seguinte ao último transferido, o que pela regra do modelo **elimina a latência rotacional** (0,32 ms por requisição). A resposta cai 590 vezes. O ganho vem da sequência física, não da distância percorrida: o deslocamento cai só 3 vezes.
- **Rajadas:** o deslocamento cai 16 vezes, mas a resposta só 45%. Com 50 requisições ordenadas pela superfície, a distância média é de ~100 cilindros (0,3 ms de busca), mas cada uma ainda paga 1 ms de busca mínima + 4,17 ms de rotação + 0,32 ms: ≈5,8 ms contra ≈10,5 ms no FCFS. A razão 5,8/10,5 = 0,55 bate com 147,5/267,0 = 0,55. **Num disco rígido, a rotação e o custo fixo da busca limitam o que qualquer escalonador consegue.**
- **Aleatória e concentrada:** a fila é curta (ocupação de ~86%). O SSTF melhora a média, mas na concentrada a espera máxima piora em relação ao FCFS (130,5 contra 96,0): é a inanição prevista, com requisições fora da zona quente (20% inicial do disco, que recebe 80% dos acessos) sendo preteridas.
- **SCAN:** na concentrada e na aleatória, o SCAN percorre *mais* cilindros que o FCFS. Com a fila quase vazia, sempre que não há nada à frente, a regra do SCAN obriga o braço a ir até a borda (cilindro 4.999 ou 0), mesmo com 80% das requisições perto do cilindro 0. É exatamente o defeito que o LOOK corrige (X7).
- **Vazão:** é igual entre os escalonadores sempre que o disco não satura, porque é ditada pela taxa de chegada.

**Veredito:** PARCIALMENTE CONFIRMADA

### X2 — Polling × interrupção conforme o dispositivo

**Hipótese:** No disco rígido, o polling gastará uma fração de CPU próxima da ocupação do disco (dezenas de %), e a interrupção, ~1%. No dispositivo rápido, o polling ficará competitivo e poderá até gastar menos CPU que a interrupção.

**Fundamentação:** Silberschatz §13.2.1–13.2.2: o polling desperdiça ciclos em espera ocupada, mas evita o custo da interrupção (troca de contexto e tratador). Ele compensa quando o dispositivo responde mais rápido que esse custo. No modelo, por requisição de 8 blocos: polling custa a duração do atendimento; interrupção custa 8 × (0,015 + 0,002) = 0,136 ms. No dispositivo rápido, a duração é 8 × 0,008 = 0,064 ms.

**Configuração:** FCFS; cargas aleatória e sequencial; disco rígido e dispositivo rápido (sem busca nem rotação); modos polling e interrupção. Mede a % do tempo total de CPU gasta com E/S.

**Resultados:**

| Dispositivo | Carga | Polling (% CPU) | Interrupção (% CPU) | Resposta média |
|---|---|---:|---:|---:|
| hdd | aleatória | 86,28 | 1,12 | 50,1 |
| hdd | sequencial | 99,998 | 0,84 | 10.241,0 |
| rápido | aleatória | 0,53 | 1,12 | 0,064 |
| rápido | sequencial | 1,07 | 2,28 | 0,064 |

**Evidências:** resultados/es.csv (experimento=X2); graficos/X2_cpu.png.

**Análise:**
- **Disco rígido:** o polling queima praticamente toda a CPU durante o tempo em que o disco trabalha (86% e ~100%), enquanto a interrupção custa ~1%. O polling mede, na prática, a ocupação do disco.
- **Dispositivo rápido:** a relação se inverte. Cada requisição dura 0,064 ms, menos que os 0,136 ms de 8 interrupções e 8 cópias, então o polling gasta **metade** da CPU da interrupção.
- **Ponto de equilíbrio:** pelo modelo, o polling compensa quando a duração da requisição é menor que `n × 0,017 ms`, ou seja, para dispositivos de latência de microssegundos.
- **Sequencial no dispositivo rápido:** a % de interrupção dobra (2,28%) não porque cada requisição custe mais, mas porque a carga termina em 11,9 s em vez de 32,5 s (o rígido saturava): o mesmo custo dividido por um tempo menor.
- O modo de CPU não altera a resposta: no modelo, a CPU não está no caminho crítico do disco.

**Veredito:** CONFIRMADA

### X3 — Acesso sequencial × direto

**Hipótese:** Por operação de leitura, os dois padrões farão o mesmo número de acessos (1 bloco de 4 KiB), mas a leitura direta (banco de dados) custará várias vezes mais tempo, pois quase toda leitura paga busca + rotação, enquanto a sequencial (compilador) reaproveita a sequência física.

**Fundamentação:** Silberschatz §11.2 (métodos de acesso sequencial e direto) e §12.4.1 (a alocação contígua favorece os dois, com endereço direto). No modelo, uma leitura em sequência custa 0,04 ms; fora de sequência, ≥ 5,2 ms.

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

No total, foram 1,38 acesso/op e 3,07 ms/op no compilador, contra 0,55 acesso/op e 3,01 ms/op no banco (os `seek` diluem a média).

**Evidências:** resultados/arquivos_por_operacao.csv (experimento=X3); resultados/arquivos.csv (experimento=X3); graficos/X3_tempo_por_operacao.png.

**Análise:**
- **Banco de dados:** a leitura direta custa 3,7 vezes mais que a sequencial, com o mesmo número de acessos. A tabela ocupa 16.384 blocos (256 cilindros), então uma posição aleatória está a ~85 cilindros: 1 + 0,26 + 4,17 + 0,04 ≈ 5,47 ms, quase exatamente os 5,49 medidos.
- **Compilador:** a primeira leitura de cada arquivo paga busca + rotação (o `open` anterior leu diretórios em outro lugar), e as seguintes custam 0,04 ms. A média de 1,48 ms reflete arquivos de ~8 blocos.
- **Custos fixos do sistema de arquivos:** `open` (3 leituras de diretório, 8,2 ms) e `create` (4 acessos) dominam o tempo do compilador. Isso antecipa o X4.
- Pela hipótese, o seek não acessa o disco e o log em modo acréscimo grava 1 bloco por transação. Ambos conferem.

**Veredito:** CONFIRMADA

### X4 — Custo de resolver caminhos conforme a profundidade

**Hipótese:** O `open` fará `d + 1` leituras para um arquivo a `d` diretórios de profundidade (a raiz mais os `d` diretórios), e o tempo crescerá aproximadamente na mesma proporção, cerca de 5 ms por nível, já que cada diretório é um bloco em outro lugar do disco.

**Fundamentação:** Silberschatz §11.3 (diretórios em árvore) e as regras de custo de `sistema.py`: resolver lê um bloco por diretório do caminho. Sem cache de diretórios, cada nível é um acesso ao disco.

**Configuração:** Uma cadeia de `d` diretórios (`/n1/n2/…/nd`) com `f.txt` no fim, `d` = 1…10, e 20 ciclos de `open` + `read` + `close` de 4 KiB por P2. Alocação contígua.

**Resultados:**

| Profundidade | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Acessos por open | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
| Tempo por open (ms) | 4,25 | 4,29 | 4,33 | 4,37 | 4,41 | 4,45 | 4,49 | 4,53 | 4,57 | 4,61 |

**Evidências:** resultados/arquivos_por_operacao.csv (experimento=X4, operacao=open); graficos/X4_open_por_profundidade.png.

**Análise:**
- **Acessos:** crescem exatamente como `d + 1`.
- **Tempo:** sobe só **0,04 ms por nível**, o tempo de transferir um bloco. Os diretórios foram criados em sequência numa partição vazia com alocação contígua (first-fit), então ocupam os blocos 0, 1, 2, …, *d* consecutivos. Só a primeira leitura (raiz) paga busca + rotação (≈4,2 ms); cada diretório seguinte é o bloco físico seguinte e, pela regra do modelo, não paga rotação.
- O custo real da resolução depende da **localidade dos metadados**. Sistemas reais tentam obter essa localidade (grupos de blocos do ext4, MFT do NTFS) e, sobretudo, evitam o disco com caches de nomes (seção 4).
- **Por que a hipótese errou no tempo:** ela supunha diretórios espalhados. Com diretórios espalhados, cada nível custaria ~5 ms, e o `open` a 10 níveis levaria ~55 ms.

**Veredito:** PARCIALMENTE CONFIRMADA

### X5 — Tabelas de arquivos abertos com vários processos

**Hipótese:** Com 8 trabalhadores abrindo 3 páginas cada uma (de um conjunto de 12), a soma das tabelas por processo chegará a 24 entradas, cada tabela por processo a 3, e a tabela global ficará limitada pelo número de arquivos distintos (≤ 12). O contador de aberturas de uma página popular passará de 1 (há compartilhamento).

**Fundamentação:** Silberschatz §11.1.2: há uma tabela por processo (posição, modo) que aponta para uma tabela do sistema (uma entrada por arquivo aberto, com contador de aberturas). O FCB é lido uma vez, por mais que o arquivo seja aberto várias vezes.

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
- **Tabelas:** a soma das tabelas por processo (24) é o dobro da tabela global (12). As 24 aberturas simultâneas apontam para só 12 entradas globais, e uma das páginas chegou a ter 6 aberturas ao mesmo tempo, cada uma com sua posição.
- **close:** custa 0 acessos porque as aberturas são só de leitura (FCB não sujo).
- **read:** custa 3,65 acessos porque cada leitura de 16 KiB toca 4 blocos, exceto a última, parcial.
- **Memória:** o tamanho da tabela global cresce com o número de *arquivos distintos* abertos, e o das tabelas por processo com o número de *aberturas*. Num servidor real com milhares de conexões, a memória das tabelas por processo é o que cresce. Por isso existem limites como `ulimit -n` no Linux.

**Veredito:** CONFIRMADA

### X6 — Cache de blocos (M1)

**Hipótese:** A taxa de acertos crescerá com a capacidade e saturará quando a cache couber todos os blocos distintos da carga. A LRU acertará mais que a FIFO nas capacidades pequenas (localidade de Zipf). O write-back fará menos escritas físicas e terá resposta menor que o write-through, mas perderá blocos sujos numa queda de energia; o write-through não perderá nada.

**Fundamentação:** Silberschatz §13.4.3 (caching) e §12.6.2 (buffer cache unificada, escrita síncrona × assíncrona). A LRU aproxima o algoritmo ótimo quando há localidade temporal. No write-back, a escrita é adiada até a expulsão ou a sincronização periódica (5.000 ms).

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
- **Saturação dos acertos:** os acertos saturam em 67% a partir de 1.024 blocos, porque a carga toca só 992 blocos distintos. A partir daí, a cache guarda tudo, e as únicas faltas são as compulsórias (a primeira referência a cada bloco). LRU e FIFO empatam, e qualquer política seria ótima.
- **LRU × FIFO:** nas capacidades pequenas, a LRU vence (39,6% contra 31,3% com 64 blocos), porque na distribuição de Zipf os blocos populares são re-referenciados e a LRU os mantém, enquanto a FIFO os expulsa periodicamente só por serem antigos.
- **Sem cache, o disco satura:** cada requisição leva ~10,5 ms e chega uma a cada 8 ms, o que dá 3,5 s de resposta. Qualquer cache, mesmo de 64 blocos, tira o disco da saturação: a resposta cai de 3.500 para 23–75 ms.
- **Write-back:** reduz as escritas físicas pela metade (1.045 para 541), porque regravações do mesmo bloco se fundem na cache. Em compensação, perde até 55 blocos na queda; o write-through nunca perde nada.
- **Contradição com a hipótese (tempestade de escrita):** com 256 blocos ou mais, a resposta média do write-back (≈45 ms) é **pior** que a do write-through (≈18–22 ms). A cada 5 s, o `sincronizar` joga dezenas de blocos sujos espalhados na fila de uma vez. Com FCFS, as leituras que chegam logo depois esperam todas essas escritas: no extra E2, essas leituras levam 310 ms contra 22 ms das demais. Com capacidade 64, isso não acontece porque os sujos são expulsos aos poucos, de forma contínua.
- **Mitigação:** sincronizar com mais frequência (1 s) suaviza as rajadas. Usar SSTF reordena a rajada (310 para 60 ms). Sincronizar raramente (30 s) quase elimina as escritas (228), mas os dados ficam só na memória. É o mesmo dilema de qualquer sistema real (seção 4).

**Veredito:** PARCIALMENTE CONFIRMADA

### X7 — Escalonadores avançados e inanição (M2)

**Hipótese:** Com carga crescente, o SSTF terá a menor resposta média e a maior espera máxima. SCAN e C-SCAN terão espera máxima menor mas mais deslocamento, e LOOK e C-LOOK serão melhores que SCAN e C-SCAN em tudo. O C-LOOK terá a espera mais uniforme. O DEADLINE terá a média do SSTF e limitará a espera das leituras a ~200 ms.

**Fundamentação:** Silberschatz §10.4: o C-SCAN dá tempo de espera mais uniforme, e o LOOK/C-LOOK evitam ir até a borda sem necessidade. O mq-deadline do Linux combina ordenação por posição com prazos por requisição (parâmetros do simulador: 200 ms para leituras e 2.000 ms para escritas).

**Configuração:** Carga concentrada gerada com semente 7 (2.000 requisições), intervalo médio de 14, 11, 9 e 8 ms (≈71 a 125 req/s); 7 escalonadores; disco rígido.

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

Detalhe a 8 ms (nossa análise, a partir das requisições concluídas): espera máxima das **leituras** de 327,1 ms no SSTF e 208,3 ms no DEADLINE. A espera máxima de 244,8 ms do DEADLINE é de uma **escrita** (prazo de 2 s, ainda não vencido).

**Evidências:** resultados/es.csv (experimento=X7); graficos/X7_espera_max_ms.png; graficos/X7_resposta_p95_ms.png; graficos/X7_deslocamento_total.png.

**Análise:**
- **SSTF:** tem a menor média em todas as cargas e uma espera máxima que cresce com a carga (87 para 327 ms): a inanição aparece à medida que a fila cresce.
- **FCFS:** é o mais justo com carga leve (espera máxima de 71 ms), mas explode quando a fila cresce (616 ms a 8 ms).
- **SCAN e C-SCAN:** têm as *piores* médias em quase todas as cargas, pelo mesmo motivo do X1 (idas até a borda com a fila curta). O retorno do C-SCAN ao cilindro 0 dá a ele o maior deslocamento.
- **LOOK e C-LOOK:** confirmam o livro. Têm deslocamento próximo ao do SSTF e espera máxima 48–50% menor que a dele a 8 ms. O C-LOOK é o mais uniforme sob carga alta (menor espera máxima: 139 e 164 ms).
- **DEADLINE:** se comporta exatamente como o SSTF enquanto nenhum prazo vence (14 e 11 ms). Sob carga alta, corta a espera das leituras para ~200 ms + um atendimento (208 ms), quase sem custo na média (28,4 contra 27,8). Ele não limita as escritas, porque o prazo delas é 10 vezes maior.
- **Por que a hipótese errou no DEADLINE:** ela supunha um limite de espera para todas as requisições, mas é exatamente a assimetria entre leituras e escritas do mq-deadline que o explica: leituras costumam bloquear processos; escritas, em geral, não.

**Veredito:** PARCIALMENTE CONFIRMADA

### X8 — DMA e buffering (M3)

**Hipótese:** O custo de CPU da interrupção crescerá linearmente com o tamanho da requisição, e o do DMA será constante. Por isso, o DMA vencerá a partir de requisições de poucos blocos, e o polling será sempre o pior no disco rígido. No buffering, o buffer duplo reduzirá o tempo total de `blocos × (p + c)` para ≈ `blocos × max(p, c)`, e o terceiro buffer não trará ganho com taxas constantes.

**Fundamentação:** Silberschatz §13.2.3 (DMA: a CPU só programa a transferência e trata uma interrupção no fim) e §13.4.2 (buffering duplo desacopla produtor e consumidor; o circular generaliza). Pelo modelo, a interrupção custa 0,017 ms por bloco, e o DMA, 0,018 ms por requisição.

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
- **DMA × interrupção:** o DMA fica constante em 0,045%, e a interrupção cresce exatamente com o número de blocos (0,042% para 10,8%, ×256). Com **1 bloco**, a interrupção é *mais barata* que o DMA (0,017 contra 0,018 ms): programar a controladora não compensa para uma única palavra ou bloco. O ponto de equilíbrio é 2 blocos (teste nosso em `test_grupo.py`). É o que o livro diz: DMA para transferências grandes.
- **Polling:** varia de 25% a 50% porque mede a ocupação do disco, que cresce com o tempo de transferência.
- **Buffering:** com 1 buffer, produtor e dispositivo se alternam, e o tempo é a soma `p + c` por bloco. Com 2, eles se sobrepõem, e o tempo é ≈ `max(p, c)` por bloco: 2.000 para 1.002 ms quando `p = c`, com o dispositivo a 99,8%.
- **Terceiro buffer:** não muda nada com taxas constantes. Quando o dispositivo é o gargalo (8 ms), o produtor fica bloqueado de qualquer jeito; buffers extras só absorvem *variações* de taxa, como rajadas de uma câmera.
- **Onde o buffer duplo ajuda mais:** quando `p ≈ c` (2× mais rápido). Com `c = 0,5`, o ganho é pequeno (20%), porque o produtor já é o gargalo.

**Veredito:** CONFIRMADA

### X9 — Links e compartilhamento (M4)

**Hipótese:** O hard link continuará funcionando depois do `unlink` do nome original (os blocos só são liberados quando `links` zera). O link simbólico para `b.txt` ficará pendente (ENOENT). O laço `l1 ↔ l2` dará ELOOP, e o hard link para diretório dará EPERM. A travessia detectará os ciclos criados por `/dados/sub/volta → /atalhos` e não entrará em laço. Abrir por um link simbólico custará mais leituras de diretório que pelo caminho direto.

**Fundamentação:** Silberschatz §11.3.3–11.3.4 (diretórios em grafo acíclico e em grafo geral): hard links exigem contagem de referências; links simbólicos podem ficar pendentes; um grafo com ciclos exige que a travessia marque o que visitou; hard links para diretórios são proibidos justamente para manter o grafo acíclico.

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

**Evidências:** resultados/arquivos.csv (experimento=X9); resultados/arquivos_por_operacao.csv (experimento=X9); graficos/X9_links.png.

**Análise:**
- **Erros previstos:** todos aconteceram, cada um uma única vez. `a_rigido` continuou legível depois do `unlink /dados/a.txt` (o FCB tinha `links = 2`, e os blocos ficaram ocupados). O `b_simbolico` virou um ponteiro pendente.
- **Ciclos:** a travessia terminou nas três execuções e não entrou em laço. Os 5 "ciclos" contam toda revisita, como pede a regra: o ciclo verdadeiro (`/dados/sub/volta → /atalhos → dados_simbolico → /dados`) e as revisitas inofensivas, como chegar de novo a `/atalhos` pela raiz depois de tê-lo visitado pelo link. Um `find -L` real distingue os dois casos comparando com os *ancestrais* da travessia, e não com tudo o que já foi visitado.
- **Custo de seguir links:** o `open` médio custa 4,92 leituras, contra 3 pelo caminho direto. Abrir `/atalhos/dados_simbolico/sub/c.txt` lê `/`, `/atalhos`, recomeça na raiz e lê `/`, `/dados`, `/dados/sub`: 5 leituras. Cada `percorrer` custa ~44 acessos, porque seguir cada link simbólico resolve o alvo desde a raiz.

**Veredito:** CONFIRMADA

### X10 — Proteção e travas (M5)

**Hipótese:** Os acessos negados serão iguais nos dois modos de bloqueio, pois a proteção não depende da trava. As verificações custarão mais para usuários no fim da ACL ou fora dela. No modo consultivo, as operações de quem não tem a trava passarão (e serão contadas como violações); no mandatório, elas falharão com EBUSY.

**Fundamentação:** Silberschatz §11.6 (listas de acesso e bits rwx de dono/grupo/outros; a ACL permite controle fino, mas tem custo de busca e de tamanho) e §11.1.2 (travas consultivas × mandatórias). A permissão é verificada no `open`, e as operações seguintes usam o modo do descritor.

**Configuração:** Carga `fs_protecao`: alice, bob e carol; `notas.txt` 600 com uma ACL de 10 usuários fictícios antes de `bob:r--`; `conta.txt` 666 disputada por P1 e P2 com `lock`/`unlock` em 20 rodadas. A mesma carga roda com bloqueio consultivo e com bloqueio mandatório.

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
- **Negações:** as 41 negações são idênticas nos dois modos: 1 de bob antes da ACL, 20 de carol (fora da ACL e de "outros") e 20 de bob pedindo `w` com ACL só `r--`.
- **Custo das verificações:** o total de 961 decompõe-se assim: cada `open` de bob custa 11 comparações (ele é a 11ª entrada da ACL), cada `open` de carol ou de alice custa 12 (a ACL inteira mais os bits), e os arquivos sem ACL custam 1. O custo da ACL cresce linearmente com sua posição e tamanho, e quem não está nela paga a varredura inteira. Por isso sistemas reais verificam só no `open` e guardam o resultado no descritor.
- **Travas:** no modo consultivo, a trava só funciona entre processos que a pedem. P2 tentou o `lock`, foi negado (20 EBUSY) e mesmo assim escreveu, assim como P1 leu sem a trava: 40 violações, possíveis corrupções. No mandatório, essas 40 operações viraram EBUSY e não chegaram ao disco, por isso o tempo de disco é 15% menor.

**Veredito:** CONFIRMADA

### X11 — Métodos de alocação (M6)

**Hipótese:** A contígua terá o acesso mais barato, mas sofrerá fragmentação externa: realocações caras e falhas (ENOSPC) no crescimento com a partição a 85%, mesmo com espaço livre total. A encadeada não fragmenta, mas o acesso direto será caríssimo (percorre a corrente). A FAT terá a mesma disposição sem os acessos extras e será a melhor no geral. A indexada terá 1–2 leituras extras por bloco e um bom acesso direto.

**Fundamentação:** Silberschatz §12.4 (contígua: first-fit, fragmentação externa, dificuldade de crescer; encadeada: só acesso sequencial eficiente; FAT: ponteiros numa tabela cacheável; indexada: acesso direto com sobrecarga do bloco de índice, em esquemas de vários níveis) e §12.5 (bitmap do espaço livre).

**Configuração:** Carga `fs_alocacao` numa partição de 20.000 blocos: arquivos de 1–256 blocos até 70% de ocupação, remoção de 40% deles, novo enchimento até 85%, 60 arquivos crescendo em modo acréscimo (2–32 escritas de 4 KiB) e 5 arquivos grandes com 100 leituras aleatórias e uma leitura sequencial em pedaços de 64 KiB.

**Resultados:**

| Alocador | Acessos/op | Tempo/op (ms) | Falhas (ENOSPC) | Realocações | Segmentos livres | Maior segmento | Descontinuidades/arquivo | Blocos de índice |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| contígua | 7,35 | 34,55 | 113 | 59 | 50 | 154 | 0,00 | — |
| encadeada | 77,52 | 12,73 | 0 | 0 | 1 | 1.939 | 0,64 | — |
| FAT | 1,90 | 4,01 | 0 | 0 | 1 | 1.939 | 0,64 | — |
| indexada | 4,29 | 14,32 | 0 | 0 | 1 | 1.805 | 0,65 | 134 |

Por operação: na contígua, `write` custa 15,1 acessos e 78,0 ms; na encadeada, `read` custa 74,9 acessos e `write` 142,9; na indexada, `read` custa 6,1 acessos e 19,0 ms.

**Evidências:** resultados/arquivos.csv (experimento=X11); resultados/arquivos_por_operacao.csv (experimento=X11); graficos/X11_alocacao.png; graficos/X11_fragmentacao.png.

**Análise:**
- **Contígua:** a fragmentação externa aparece com clareza. Restaram 2.052 blocos livres em 50 pedaços, o maior com apenas 154 blocos, e 113 escritas falharam por falta de espaço *contíguo*. As 59 realocações copiam o arquivo bloco a bloco, alternando leitura no lugar antigo e escrita no novo, e cada cópia paga busca + rotação. Por isso o `write` custa 78 ms e a contígua tem o **pior** tempo por operação, apesar de ler muito bem.
- **FAT:** é a melhor (1,9 acessos e 4,0 ms por operação), porque os ponteiros estão na memória. Isso explica a sobrevivência da FAT em mídia removível.
- **Encadeada:** faz 18 vezes mais acessos que a indexada e mesmo assim é **mais rápida** em tempo (12,7 contra 14,3 ms/op). Os blocos da corrente foram alocados pelo first-free e são, em geral, fisicamente consecutivos, então ler a corrente custa 0,04 ms por bloco, sem rotação. A indexada, ao contrário, alterna leitura do índice e leitura do dado em lugares diferentes e paga rotação **a cada bloco**: uma leitura sequencial de 64 KiB vira 32 acessos alternados, ~4,2 ms cada.
- **Regra do cursor:** a encadeada escreve muito mal (142,9 acessos por `write`). Cada escrita de 4 KiB em modo acréscimo toca o bloco parcial do cursor, e pela regra "i ≤ cursor recomeça" relê a corrente inteira.
- **Indexada:** gastou 134 blocos em índices (0,7% do disco).
- **Resumo:** nenhum método vence em tudo. A contagem de acessos sozinha engana, e o padrão físico dos acessos (sequência ou não) importa tanto quanto o número deles.

**Veredito:** PARCIALMENTE CONFIRMADA

### X12 — Integração ponta a ponta

**Hipótese:** Sem cache, as cargas do sistema de arquivos vão gerar filas longas (as operações chegam a cada 0,5–2 ms e cada acesso aleatório leva ~5 ms), e SSTF/SCAN ajudarão pouco, porque os acessos do compilador estão concentrados em poucos cilindros. A alocação indexada gerará mais requisições e mais tempo que a contígua. Com uma cache LRU de 1.024 blocos em write-back, o compilador terá alta taxa de acertos (reuso de cabeçalhos e diretórios), e o banco de dados, baixa (tabela de 16.384 blocos ≫ cache).

**Fundamentação:** Silberschatz §12.6 (a buffer cache e a disposição física dos dados determinam o desempenho), §12.4.3 (overhead do índice) e §10.4 (escalonamento só ajuda com fila).

**Configuração:** As cargas `fs_compilador` e `fs_banco_dados` rodam na alocação contígua e na indexada, guardando o histórico. O histórico vira requisições (`historico_para_requisicoes`), que passam por FCFS, SSTF e SCAN, com e sem cache LRU de 1.024 blocos em write-back.

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

Deslocamento no compilador + contígua com cache: FCFS 104, SSTF 80, SCAN 49.996 cilindros. Escritas físicas com cache: 0 (compilador) e 670–690 (banco).

**Evidências:** resultados/es.csv (experimento=X12); graficos/X12_resposta.png; graficos/X12_deslocamento.png.

**Análise:**
- **Sem cache, tudo satura:** o tempo total do compilador na contígua com FCFS (11.986,7 ms) é praticamente igual ao tempo de disco medido no X3 (11.986,65 ms), ou seja, o disco fica ocupado 100% do tempo, e as respostas são de segundos.
- **Escalonadores sem cache:** reduzem o tempo total do banco em 22% (19,8 para 15,4 s), porque ordenam acessos aleatórios dentro da tabela. No compilador, a redução é de 17% e a resposta média nem melhora, porque os dados ocupam poucos cilindros: o custo é a rotação, não a busca.
- **Indexada sem cache:** é desastrosa, com 2,2 vezes mais requisições e 2,7 vezes mais tempo. Cada bloco de dados exige antes a leitura do bloco de índice, o que quebra a sequência física.
- **Com cache, o compilador vira outro sistema:** 90–96% de acertos, resposta de 0,15–0,23 ms, e a diferença entre contígua e indexada **desaparece**. Os blocos de índice são os mais quentes da cache, e é por isso que a indexada tem *mais* acertos (95,7%).
- **Banco de dados com cache:** na contígua, a taxa de 4,9% é quase exatamente a razão entre a capacidade da cache e o tamanho da tabela (1.024 / 16.384 = 6,25%), como esperado para acessos uniformes. Na indexada, 74,5% dos acertos vêm dos 17 blocos de índice (2 de cada 3 requisições), o que faz a indexada com cache ficar *mais rápida* que a contígua com cache (510 contra 1.412 ms com SSTF). Na contígua, as escritas (com o log) e as faltas ainda congestionam o disco.
- **SCAN com cache:** com a fila quase sempre vazia, o SCAN vai até a borda 5 vezes (49.996 cilindros contra 80 do SSTF).
- **Escritas físicas zeradas no compilador:** a carga termina antes da primeira sincronização (5 s). Todos os `.o` gravados ainda estavam só na memória no fim da simulação, e uma queda de energia os perderia.

**Veredito:** PARCIALMENTE CONFIRMADA

### X13 — Armazenamento flash (bônus)

**Hipótese:** O flash terá resposta uma a duas ordens de grandeza menor que o disco rígido, pois não há busca nem rotação. Como o dispositivo começa vazio e as cargas gravam poucos milhares de páginas, a coleta de lixo não será acionada no X13 (amplificação de escrita = 1, zero apagamentos). Com o dispositivo cheio (extra E1), a amplificação será alta e cairá com mais área de reserva.

**Fundamentação:** Silberschatz §10.1.2 (memória não volátil: sem partes móveis, apagamento por bloco, desgaste, FTL e coleta de lixo). A gravação só ocorre em página apagada, e o apagamento é por bloco de 64 páginas. Com a área de reserva de 7% do modelo, uma aproximação analítica conhecida para coleta gulosa com gravações uniformes dá amplificação ≈ (1 + r) / (2r), cerca de 7,6.

**Configuração:** Cargas `es_reuso` e `es_aleatoria`, FCFS, disco rígido × flash (oficial). Extra E1: flash de 32.000 páginas lógicas, preenchido e com 64.000 regravações de envelhecimento, depois 60.000 gravações aleatórias de 1 página, com reserva de 7%, 15% e 28%.

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
- **Resposta:** o flash responde em 0,09–0,64 ms contra 50–3.500 ms do disco. Na carga `reuso`, o disco está saturado e o flash não: uma leitura custa 0,025 ms e uma gravação 0,2 ms, contra ~10 ms no disco.
- **Amplificação no X13:** como previsto, a amplificação foi 1,0 e houve zero apagamentos. As cargas gravam no máximo 4.792 páginas num dispositivo de 342.400; a FTL apenas escreve em log em páginas novas, sem nunca precisar recuperar espaço. O X13 oficial mostra o SSD *novo* e esconde o custo que aparece com o uso.
- **Estado estacionário (E1):** com a reserva de 7%, cada página gravada pelo host custa ~8 programações, perto da aproximação teórica de 7,6. A escrita média sobe de 0,2 para 1,95 ms, e o p99 vai a 112 ms (pausas da coleta, que ocorrem durante a própria gravação). Dobrar a reserva para 15% corta a amplificação pela metade, e 28% a reduz para ~2,9, com apagamentos (desgaste) caindo na mesma proporção. Isso explica SSDs "enterprise" com mais overprovisioning e o comando TRIM, que devolve à FTL páginas que o sistema de arquivos já liberou.
- **Frente quente:** regravar só metade do espaço lógico não ajudou a coleta gulosa, porque ela não separa dados quentes de frios. FTLs reais fazem essa separação.

**Veredito:** CONFIRMADA

## 4. Relação com sistemas reais

**Escalonadores de E/S (X1, X7).** O mq-deadline do Linux, base do nosso DEADLINE, usa por padrão prazos de 500 ms para leituras e 5 s para escritas, e atende lotes de requisições na ordem de posição ([kernel: mq-deadline.c](https://elixir.bootlin.com/linux/v5.8.7/source/block/mq-deadline.c)). O simulador reduziu esses prazos para 200 ms e 2 s, mas a assimetria é a mesma. O X7 mostra por que ela existe: leituras costumam bloquear processos, e o DEADLINE limitou a espera delas sem perder a média do SSTF. O BFQ privilegia a latência de aplicações interativas e distribui banda por processo, aceitando perder vazão ([kernel: BFQ](https://kernel.org/doc/html/v6.15/block/bfq-iosched.html)). Ele ataca a inanição que o SSTF mostrou no X1 (concentrada) e no X7. Para SSDs NVMe, costuma-se usar `none` (sem reordenação), porque o dispositivo não tem braço e possui filas internas profundas. O X13 explica: sem busca nem rotação, reordenar não traz ganho. Algumas distribuições escolhem BFQ para discos rotacionais e mq-deadline/none para os demais ([SUSE: Tuning I/O](https://documentation.suse.com/sles/html/SLES-all/cha-tuning-io.html)). Windows e macOS não oferecem ao usuário uma escolha equivalente de escalonador; boa parte da reordenação fica no próprio dispositivo (NCQ/filas NVMe).

**Polling × interrupção × DMA (X2, X8).** O Linux oferece polling de conclusão para dispositivos de baixíssima latência (`io_poll`) e polling híbrido, que dorme pelo tempo médio esperado antes de começar a testar ([LWN: block layer](https://lwn.net/Articles/735275/)). É a conclusão do X2: o polling ganha quando o dispositivo responde em menos tempo que o custo de uma interrupção. Frameworks como o SPDK levam isso ao extremo, com drivers NVMe sem interrupção. O DMA é universal em discos, NVMe e placas de rede (bus mastering); o X8 mostra que ele só compensa a partir de transferências de mais de uma palavra ou bloco.

**Cache de páginas e gravação periódica (X6, X12, E2).** O Linux acorda as threads de flush periodicamente (`dirty_writeback_centisecs`) e grava dados sujos "velhos" (`dirty_expire_centisecs`), além de limites por volume de dados sujos (`dirty_background_ratio`, `dirty_ratio`) ([kernel: sysctl/vm](https://www.kernel.org/doc/Documentation/sysctl/vm.txt)). O lazy writer do Windows roda a cada segundo e grava uma fração (1/8) das páginas sujas, em vez de despejar tudo de uma vez ([Microsoft: File Caching](https://learn.microsoft.com/en-us/windows/win32/FileIO/file-caching)). Essa é exatamente a mitigação da tempestade que medimos no E2: gravar pouco e com frequência suaviza as rajadas que, no nosso modelo de sincronização total a cada 5 s, multiplicaram por 14 a resposta das leituras. Em todos os sistemas, dados em write-back se perdem numa queda (X6: até 55 blocos). Por isso existem `fsync()` e o write-through por arquivo (`FILE_FLAG_WRITE_THROUGH` no Windows). No macOS, o `fsync()` entrega os dados ao disco, mas não garante que o dispositivo os grave fisicamente. Para isso existe o `F_FULLFSYNC` ([Apple: fsync(2)](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)), uma camada de cache (a do dispositivo) que nosso modelo não tem.

**Resolução de caminhos e cache de diretórios (X4, X9).** O Linux mantém o dcache, uma cache de nomes (dentries) que torna a resolução quase sempre independente do disco ([kernel: path-lookup](https://www.kernel.org/doc/Documentation/filesystems/path-lookup.md)). No nosso modelo, sem essa cache, cada nível custa uma leitura (X4), e cada link simbólico recomeça a busca desde a raiz (X9). O Linux também limita a quantidade de links simbólicos seguidos (ELOOP), como nosso limite de 8.

**Tabelas de arquivos abertos (X5).** No Unix, cada processo tem uma tabela de descritores que aponta para "descrições de arquivo aberto" (`struct file`, com posição e modo), que apontam para o inode compartilhado. No Windows, a tabela de handles do processo aponta para objetos de arquivo, e um bloco de controle por arquivo é compartilhado. É a mesma divisão em dois níveis do X5. Diferença: no Unix, um `fork` faz pai e filho compartilharem a mesma posição, caso que o modelo não simula.

**Proteção (X10).** O Linux usa bits rwx e ACLs POSIX opcionais; NTFS e APFS usam ACLs ricas (o APFS suporta permissões Unix e ACLs NFSv4). Todos verificam no `open` e guardam o modo no descritor, como o modelo. O custo linear da ACL que medimos é real, por isso as ACLs costumam ser curtas e baseadas em grupos.

**Travas (X10).** No Windows, as travas de `LockFileEx` são mandatórias: um `ReadFile`/`WriteFile` de outro processo na região travada falha com `ERROR_LOCK_VIOLATION` ([Microsoft: Locking byte ranges](https://learn.microsoft.com/en-ie/Windows/Win32/fileio/locking-and-unlocking-byte-ranges-in-files)), exatamente o EBUSY do nosso modo mandatório. No Linux, as travas (`flock`, `fcntl`) são consultivas, e o suporte a travas mandatórias foi removido do kernel na versão 5.15 ([LKML: fcntl.2](https://lkml.iu.edu/hypermail/linux/kernel/2110.0/00378.html)). O macOS segue o modelo BSD, também consultivo. O X10 mostra o risco: no modo consultivo, 40 operações ignoraram a trava.

**Alocação (X11, X12).** A FAT (FAT32/exFAT) continua sendo o padrão de pendrives e cartões: SDHC usa FAT32, e SDXC usa exFAT por especificação ([SanDisk: SDHC × SDXC](https://www.sandisk.com/topics/memory-cards/sdhc-vs-sdxc-comparison)). É a simplicidade da tabela em memória que o X11 premiou. O ext2/ext3 usava blocos de índice diretos, indiretos e duplamente indiretos (até 1.024 ponteiros por bloco de 4 KiB, como no nosso índice), e o ext4 trocou isso por *extents*, intervalos contíguos de blocos, com até 4 extents dentro do próprio inode ([kernel: ext4 i_block](https://docs.kernel.org/filesystems/ext4/ifork.html)). Isso combina o melhor da contígua (sequência física, poucos metadados) com a flexibilidade da indexada, e evita o problema do X11, em que reler o índice quebrava a sequência. No NTFS, a MFT guarda os metadados e, para arquivos pequenos, os próprios dados (atributos residentes), economizando o acesso extra ([NTFS.com: MFT](https://www.ntfs.com/ntfs-mft.htm)). O APFS usa copy-on-write para os metadados e clones que compartilham blocos ([Wikipedia: APFS](https://en.wikipedia.org/wiki/Apple_File_System)): a mesma ideia da FTL do X13, nunca sobrescrever no lugar.

**Flash (X13, E1).** SSDs e celulares (UFS/eMMC) têm uma FTL como a nossa, com coleta de lixo, nivelamento de desgaste e área de reserva. O TRIM permite que o sistema avise quais páginas estão livres, reduzindo a amplificação medida no E1.

## 5. Limitações do simulador

- **Disco simplificado:** a busca é linear na distância, quando na prática é ~√d para distâncias curtas. A rotação é sempre meia volta (não depende da posição angular), e não há cache no disco nem NCQ. Em discos reais, o próprio dispositivo reordena requisições (NCQ), o que reduz a diferença entre escalonadores do X1/X7. A regra "rotação = 0 só no bloco imediatamente seguinte" exagera o ganho da sequência: explica os 590× do X1 sequencial e os 0,04 ms por nível do X4.
- **Sem cache no sistema de arquivos:** a camada de blocos não tem cache de diretórios, de inodes nem de índices. Isso infla o custo do `open` (X4), dos links (X9) e da indexada (X11). Na integração (X12), a cache LRU mostrou o quanto isso muda as conclusões.
- **Tempo nas cargas de arquivos:** os instantes das operações são fixos (0,5–2 ms entre operações) e não esperam o disco. No X12, isso cria filas irreais de segundos; um processo real esperaria o `read` terminar antes de emitir o próximo.
- **Write-back simplificado:** sincronização total a cada 5 s, sem limite de sujos nem gravação por idade, o que gera as tempestades do X6/E2 de forma mais intensa que no Linux ou no Windows. O fim da simulação não faz *flush*, por isso o X12 mostra 0 escritas físicas.
- **Regras literais:** "cursor da encadeada recomeça em i ≤ c" (X11) e "toda revisita é ciclo" (X9) diferem do que sistemas reais fazem. Os números refletem as regras, e discutimos o efeito em cada caso.
- **Escalonadores sem preempção:** não modelamos lotes (`fifo_batch`) nem a separação leitura/escrita do mq-deadline real.
- **CPU:** não há disputa de CPU nem o tempo das próprias decisões do escalonador.
- **Flash:** sem TRIM, sem separação quente/frio e sem paralelismo entre chips. O X13 oficial usa um dispositivo novo (amplificação de escrita = 1), e o estado estacionário só aparece no extra E1, com um dispositivo menor.
- **Cargas sintéticas:** Zipf, exponencial e concentrada 80/20 são aproximações. Uma única semente por carga não dá intervalo de confiança.

## 6. Conclusões

1. Num disco rígido, o escalonador importa quando há fila, e o maior ganho vem de preservar a sequência física, que elimina a rotação. Reduzir distância ajuda menos do que parece, porque a rotação e a busca mínima são custos fixos (X1).
2. SSTF e DEADLINE dão as menores médias. O DEADLINE limita a espera das leituras sem perder a média, e o LOOK/C-LOOK é o melhor compromisso de justiça. O SCAN/C-SCAN "puros" são ruins com carga leve por irem até a borda (X1, X7).
3. Polling compensa só em dispositivos de microssegundos, e o DMA só a partir de dois blocos. O buffer duplo dobra a vazão quando produtor e dispositivo têm a mesma velocidade, e um terceiro buffer não ajuda com taxas constantes (X2, X8).
4. A cache é a otimização dominante: tira o disco da saturação e faz diferenças de alocação desaparecerem. O write-back troca durabilidade e picos de latência (tempestades no sync) por menos escritas (X6, X12, E2).
5. Metadados custam caro: a profundidade do caminho, os links simbólicos e os blocos de índice somam acessos. Como esses acessos caem, ou não, em sequência física, a contagem de acessos sozinha não prevê o tempo (X4, X9, X11).
6. Um SSD novo esconde o custo da coleta de lixo. Em uso contínuo, a amplificação de escrita chega a ~8 com 7% de reserva e cai com mais overprovisioning (X13, E1).

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
- Microsoft — Locking and Unlocking Byte Ranges in Files: https://learn.microsoft.com/en-ie/Windows/Win32/fileio/locking-and-unlocking-byte-ranges-in-files
- Linux man-pages — remoção das travas mandatórias no Linux 5.15: https://lkml.iu.edu/hypermail/linux/kernel/2110.0/00378.html
- Linux kernel — ext4, The Contents of inode.i_block (blocos indiretos e extents): https://docs.kernel.org/filesystems/ext4/ifork.html
- NTFS.com — NTFS Master File Table: https://www.ntfs.com/ntfs-mft.htm
- Wikipedia — Apple File System: https://en.wikipedia.org/wiki/Apple_File_System
- SanDisk — SDHC vs SDXC (FAT32 × exFAT): https://www.sandisk.com/topics/memory-cards/sdhc-vs-sdxc-comparison

## 8. Declaração de autoria e uso de IA

**Quem fez o quê:** [descrever a divisão do trabalho entre os integrantes — por exemplo, quem revisou e testou cada módulo, quem conferiu cada experimento e quem revisou cada seção do relatório]

**Uso de ferramentas de IA:**

| Ferramenta | Finalidade | Onde foi usada (arquivos ou seções) |
|---|---|---|
| Claude (Anthropic), Opus 5.5 | Implementação do código a partir das regras do enunciado e dos comentários dos arquivos; testes adicionais; script de experimentos extras; gráficos adicionais; redação inicial do relatório (hipóteses formuladas a partir do Silberschatz, análises e pesquisa sobre sistemas reais) | `simes/es/escalonadores.py`, `simes/es/cpu.py`, `simes/es/cache.py`, `simes/es/buffering.py`, `simes/arquivos/sistema.py`, `simes/arquivos/alocacao.py`, `simes/integracao.py`, `simes/disco.py` (DiscoFlash), `testes/test_grupo.py`, `extras/experimentos_extras.py`, `graficos.py` (gráficos acrescentados), seções 0 a 7 deste relatório |
