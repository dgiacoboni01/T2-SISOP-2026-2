# -*- coding: utf-8 -*-
"""
Sistema de arquivos simulado (camada 3).

Referência: Silberschatz, 9ª ed., cap. 11 — operações, FCB, tabelas de arquivos abertos,
métodos de acesso, estrutura de diretórios, links e proteção.

O que já vem pronto (FORNECIDO): a criação da raiz, as tabelas e os métodos auxiliares
(nome começando com _). Implementem os métodos marcados com TODO.

=====================================================================================
REGRAS DE CUSTO (iguais para todos os grupos — sigam à risca)
=====================================================================================
Um acesso = um bloco lido ou gravado (use _ler, _escrever e _aplicar; eles contam tudo).

Diretórios: cada diretório ocupa 1 bloco (o bloco 0 do diretório, obtido com
_bloco_do_diretorio). Ler um diretório = 1 leitura desse bloco.

resolver("/a/b/c")   lê os diretórios "/", "/a" e "/a/b" (3 leituras) e devolve o FCB de c.
                     resolver("/") devolve a raiz sem ler nada.
Operações que criam, removem ou alteram uma entrada leem os diretórios do caminho até o
pai (inclusive), exatamente como resolver, e depois:

mkdir(p)             + aloca 1 bloco para o novo diretório, grava o bloco do novo diretório
                       (1 escrita) e o bloco do pai (1 escrita)
create(p, tamanho)   + aloca ceil(tamanho / TAMANHO_BLOCO) blocos (conteúdo não é gravado)
                       e grava o bloco do pai (1 escrita)
open(p, modo, h)     lê os diretórios como resolver; nenhuma escrita
read(h, n)           lê os blocos que contêm os bytes [posição, posição + n), limitados ao
                     tamanho do arquivo; avança a posição; devolve os bytes lidos
write(h, n)          se precisar, estende o arquivo (alocador.estender); grava os blocos que
                     contêm [posição, posição + n); avança a posição; atualiza o tamanho e
                     marca o FCB como sujo. No modo 'a', a escrita começa no fim do arquivo.
seek(h, posicao)     muda a posição; nenhum acesso
close(h)             se o FCB está sujo, grava o bloco do diretório pai (1 escrita) e limpa
                     o "sujo"; atualiza as tabelas
unlink(p)            + grava o bloco do pai (1 escrita). Se o arquivo ficar sem links e
                     ninguém o tiver aberto, libera os blocos; se estiver aberto, a liberação
                     acontece no último close.

Tabelas de arquivos abertos:
- self.tabela_global: dict fcb_id -> EntradaGlobal. Uma entrada por arquivo aberto; o
  contador soma as aberturas de todos os processos. A entrada sai quando o contador zera.
- self.tabelas_processo: dict processo -> {handle: EntradaProcesso}. Uma entrada por
  abertura, com a posição e o modo.

Modos de abertura: 'r' (só leitura), 'w' (só escrita), 'rw' (ambos), 'a' (acréscimo, só
escrita). Ler com um handle sem 'r', ou escrever sem 'w'/'a': ErroSA('EBADF').
Erros: lancem ErroSA com o código adequado (veja estruturas.py).

Ganchos dos módulos (já chamados pelas operações do núcleo, que vocês implementam):
- _verificar_permissao(processo, fcb, modo): no núcleo não faz nada; o M5 implementa.
- _verificar_trava(processo, fcb):            no núcleo não faz nada; o M5 implementa.
  O núcleo deve chamar _verificar_permissao no open e _verificar_trava no read e no write.
"""
import math
from collections import Counter

from simes import parametros as P
from simes.arquivos.estruturas import FCB, EntradaGlobal, EntradaProcesso, ErroSA

MAX_LINKS_SIMBOLICOS = 8


class SistemaArquivos:

    def __init__(self, camada, alocador, bloqueio="consultivo"):
        self.camada = camada
        self.alocador = alocador
        self.bloqueio = bloqueio            # 'consultivo' ou 'mandatorio' (M5)
        self.fcbs = {}
        self._proximo_id = 0
        self.agora_ms = 0.0                 # atualizado pelo simulador antes de cada operação
        self.tabela_global = {}             # fcb_id -> EntradaGlobal
        self.tabelas_processo = {}          # processo -> {handle: EntradaProcesso}
        self.usuarios = {}                  # processo -> nome do usuário (M5); padrão: 'root'
        self.contadores = Counter()         # indicadores extras dos módulos (vão para o CSV)
        self.raiz = self._novo_fcb("/", "diretorio", None)
        self.alocador.alocar(self.raiz, 1)  # a raiz já existe ao "formatar": sem custo

    # ================================================================ FORNECIDO
    def _novo_fcb(self, nome, tipo, pai, processo=None):
        """Cria um FCB (dono = usuário do processo) e o registra em self.fcbs. Não aloca blocos."""
        fcb = FCB(self._proximo_id, nome, tipo, pai, self.agora_ms, self.usuario_de(processo))
        self.fcbs[fcb.id] = fcb
        self._proximo_id += 1
        return fcb

    def usuario_de(self, processo):
        return self.usuarios.get(processo, "root")

    def _ler(self, blocos):
        self.camada.ler(blocos)

    def _escrever(self, blocos):
        self.camada.escrever(blocos)

    def _aplicar(self, extras):
        """Executa a lista de acessos extras devolvida pelo alocador."""
        for tipo, bloco in extras:
            if tipo == "L":
                self._ler([bloco])
            else:
                self._escrever([bloco])

    def _bloco_do_diretorio(self, diretorio):
        """Bloco físico onde está o conteúdo do diretório (já executa os extras do alocador)."""
        bloco, extras = self.alocador.bloco_fisico(diretorio, 0)
        self._aplicar(extras)
        return bloco

    def _ler_diretorio(self, diretorio):
        self._ler([self._bloco_do_diretorio(diretorio)])

    def _gravar_diretorio(self, diretorio):
        self._escrever([self._bloco_do_diretorio(diretorio)])

    def _blocos_do_intervalo(self, fcb, inicio, fim):
        """Blocos físicos que contêm os bytes [inicio, fim) do arquivo (executa os extras)."""
        if fim <= inicio:
            return []
        primeiro = inicio // P.TAMANHO_BLOCO
        ultimo = (fim - 1) // P.TAMANHO_BLOCO
        blocos = []
        for i in range(primeiro, ultimo + 1):
            bloco, extras = self.alocador.bloco_fisico(fcb, i)
            self._aplicar(extras)
            blocos.append(bloco)
        return blocos

    @staticmethod
    def _blocos_necessarios(tamanho):
        return int(math.ceil(tamanho / float(P.TAMANHO_BLOCO)))

    @staticmethod
    def _dividir(caminho):
        if not caminho.startswith("/"):
            raise ErroSA("EINVAL", "o caminho deve ser absoluto: %s" % caminho)
        return [p for p in caminho.split("/") if p]

    def _entrada(self, processo, handle):
        try:
            return self.tabelas_processo[processo][handle]
        except KeyError:
            raise ErroSA("EBADF", "%s não tem o handle %s" % (processo, handle))

    def _liberar_arquivo(self, fcb):
        self.alocador.liberar(fcb)
        del self.fcbs[fcb.id]

    def _verificar_permissao(self, processo, fcb, modo):
        """Gancho do M5. No núcleo, tudo é permitido."""
        return None

    def _verificar_trava(self, processo, fcb):
        """Gancho do M5. No núcleo, travas não existem."""
        return None

    # ================================================================ núcleo (N3)
    # ------------------------------------------------------------ auxiliares do grupo
    def _resolver_partes(self, partes, seguir_ultimo=True):
        """Percorre os componentes a partir da raiz. Antes de procurar cada nome, lê o
        diretório em que ele está (1 leitura). Links simbólicos (M4) são expandidos: o alvo
        (absoluto) substitui o componente e a busca recomeça da raiz, pagando de novo as
        leituras do caminho do alvo, como no namei() de um Unix sem dentry cache."""
        atual = self.raiz
        pendentes = list(partes)
        saltos = 0
        restantes_do_alvo = 0      # quantos dos próximos componentes vieram de um alvo de link
        while pendentes:
            nome = pendentes.pop(0)
            veio_de_alvo = restantes_do_alvo > 0
            if veio_de_alvo:
                restantes_do_alvo -= 1
            if atual.tipo != "diretorio":
                raise ErroSA("ENOTDIR", "%s não é diretório" % atual.nome)
            self._ler_diretorio(atual)
            fcb_id = atual.entradas.get(nome)
            if fcb_id is None:
                erro = ErroSA("ENOENT", nome)
                erro.pendente = veio_de_alvo          # alvo de link simbólico inexistente
                raise erro
            proximo = self.fcbs[fcb_id]
            if proximo.tipo == "simbolico" and (pendentes or seguir_ultimo):
                saltos += 1
                if saltos > MAX_LINKS_SIMBOLICOS:
                    raise ErroSA("ELOOP", "mais de %d links simbólicos" % MAX_LINKS_SIMBOLICOS)
                alvo = self._dividir(proximo.alvo)
                pendentes = alvo + pendentes
                restantes_do_alvo += len(alvo)
                atual = self.raiz
                continue
            atual = proximo
        return atual

    def _pai_e_nome(self, caminho):
        """Para operações que criam, removem ou alteram uma entrada: lê os diretórios do
        caminho até o pai (inclusive) e devolve (fcb_do_pai, nome)."""
        partes = self._dividir(caminho)
        if not partes:
            raise ErroSA("EEXIST", "a raiz já existe")
        pai = self._resolver_partes(partes[:-1], seguir_ultimo=True)
        if pai.tipo != "diretorio":
            raise ErroSA("ENOTDIR", "%s não é diretório" % pai.nome)
        self._ler_diretorio(pai)
        return pai, partes[-1]

    def _diretorio_dos_metadados(self, fcb):
        """Diretório cujo bloco guarda os metadados (a entrada) do FCB."""
        return self.fcbs.get(fcb.pai, self.raiz) if fcb.pai is not None else fcb

    def _criar_entrada(self, processo, pai, nome, tipo, n_blocos):
        if nome in pai.entradas:
            raise ErroSA("EEXIST", nome)
        fcb = self._novo_fcb(nome, tipo, pai.id, processo)
        try:
            extras = self.alocador.alocar(fcb, n_blocos)
        except ErroSA:
            del self.fcbs[fcb.id]
            raise
        self._aplicar(extras)
        pai.entradas[nome] = fcb.id
        return fcb

    def _modo_valido(self, modo):
        if modo not in ("r", "w", "rw", "a"):
            raise ErroSA("EINVAL", "modo inválido: %s" % modo)

    # -------------------------------------------------------------------- operações
    def resolver(self, caminho, seguir_simbolico=True):
        """Devolve o FCB do caminho absoluto, lendo os diretórios do caminho (ver regras).
        Erros: ENOENT, ENOTDIR. Com o M4: segue links simbólicos (inclusive no meio do
        caminho; o último componente só se seguir_simbolico=True); ELOOP depois de
        MAX_LINKS_SIMBOLICOS links."""
        return self._resolver_partes(self._dividir(caminho), seguir_simbolico)

    def mkdir(self, processo, caminho):
        pai, nome = self._pai_e_nome(caminho)
        novo = self._criar_entrada(processo, pai, nome, "diretorio", 1)
        self._gravar_diretorio(novo)          # bloco do novo diretório
        self._gravar_diretorio(pai)           # entrada no pai
        return novo

    def create(self, processo, caminho, tamanho):
        tamanho = int(tamanho)
        if tamanho < 0:
            raise ErroSA("EINVAL", "tamanho negativo")
        pai, nome = self._pai_e_nome(caminho)
        fcb = self._criar_entrada(processo, pai, nome, "arquivo", self._blocos_necessarios(tamanho))
        fcb.tamanho = tamanho                 # o conteúdo não é gravado
        self._gravar_diretorio(pai)
        return fcb

    def open(self, processo, caminho, modo, handle):
        self._modo_valido(modo)
        try:
            fcb = self.resolver(caminho)
        except ErroSA as e:
            if getattr(e, "pendente", False):
                self.contadores["links_pendentes"] += 1
            raise
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", caminho)
        tabela = self.tabelas_processo.get(processo, {})
        if handle in tabela:
            raise ErroSA("EINVAL", "%s já usa o handle %s" % (processo, handle))
        self._verificar_permissao(processo, fcb, modo)
        entrada_global = self.tabela_global.get(fcb.id)
        if entrada_global is None:
            entrada_global = self.tabela_global[fcb.id] = EntradaGlobal(fcb.id)
        entrada_global.contador += 1
        self.tabelas_processo.setdefault(processo, {})[handle] = EntradaProcesso(fcb.id, modo, 0)
        return handle

    def read(self, processo, handle, n):
        entrada = self._entrada(processo, handle)
        if "r" not in entrada.modo:
            raise ErroSA("EBADF", "%s não foi aberto para leitura" % handle)
        n = int(n)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[entrada.fcb_id]
        self._verificar_trava(processo, fcb)
        inicio = entrada.posicao
        fim = min(inicio + n, fcb.tamanho)
        if fim <= inicio:
            return 0                          # fim do arquivo
        self._ler(self._blocos_do_intervalo(fcb, inicio, fim))
        entrada.posicao = fim
        return fim - inicio

    def write(self, processo, handle, n):
        entrada = self._entrada(processo, handle)
        if "w" not in entrada.modo and entrada.modo != "a":
            raise ErroSA("EBADF", "%s não foi aberto para escrita" % handle)
        n = int(n)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[entrada.fcb_id]
        self._verificar_trava(processo, fcb)
        if n == 0:
            return 0
        if entrada.modo == "a":
            entrada.posicao = fcb.tamanho     # acréscimo: sempre no fim (como O_APPEND)
        inicio, fim = entrada.posicao, entrada.posicao + n
        faltam = self._blocos_necessarios(fim) - len(fcb.blocos)
        if faltam > 0:
            self._aplicar(self.alocador.estender(fcb, faltam))
        self._escrever(self._blocos_do_intervalo(fcb, inicio, fim))
        entrada.posicao = fim
        fcb.tamanho = max(fcb.tamanho, fim)
        fcb.modificado_ms = self.agora_ms
        fcb.sujo = True
        return n

    def seek(self, processo, handle, posicao):
        entrada = self._entrada(processo, handle)
        posicao = int(posicao)
        if posicao < 0:
            raise ErroSA("EINVAL", "posição negativa")
        entrada.posicao = posicao
        return posicao

    def close(self, processo, handle):
        entrada = self._entrada(processo, handle)
        fcb = self.fcbs[entrada.fcb_id]
        if fcb.sujo:
            if fcb.links > 0:                 # arquivo removido não tem mais entrada para gravar
                self._gravar_diretorio(self._diretorio_dos_metadados(fcb))
            fcb.sujo = False
        tabela = self.tabelas_processo[processo]
        del tabela[handle]
        if not tabela:
            del self.tabelas_processo[processo]
        entrada_global = self.tabela_global[fcb.id]
        entrada_global.contador -= 1
        if entrada_global.trava == processo:  # M5: o close libera a trava do dono
            entrada_global.trava = None
        if entrada_global.contador == 0:
            del self.tabela_global[fcb.id]
            if fcb.links == 0:                # unlink com o arquivo aberto: libera agora
                self._liberar_arquivo(fcb)

    def unlink(self, processo, caminho):
        pai, nome = self._pai_e_nome(caminho)
        fcb_id = pai.entradas.get(nome)
        if fcb_id is None:
            raise ErroSA("ENOENT", caminho)
        fcb = self.fcbs[fcb_id]
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", "unlink não remove diretórios: %s" % caminho)
        del pai.entradas[nome]
        self._gravar_diretorio(pai)
        fcb.links -= 1
        if fcb.links == 0 and fcb.id not in self.tabela_global:
            self._liberar_arquivo(fcb)

    # ================================================================ módulo M4
    def link(self, processo, existente, novo):
        """Hard link: lê os diretórios até `existente` (como resolver) e até o pai de `novo`;
        cria a entrada; links += 1; grava o bloco do pai de `novo` (1 escrita).
        Hard link para diretório: ErroSA('EPERM')."""
        fcb = self.resolver(existente, seguir_simbolico=False)     # como o link() do Linux
        if fcb.tipo == "diretorio":
            raise ErroSA("EPERM", "hard link para diretório: %s" % existente)
        pai, nome = self._pai_e_nome(novo)
        if nome in pai.entradas:
            raise ErroSA("EEXIST", novo)
        pai.entradas[nome] = fcb.id
        fcb.links += 1
        self._gravar_diretorio(pai)

    def symlink(self, processo, alvo, caminho):
        """Link simbólico: lê os diretórios até o pai de `caminho`; cria um FCB do tipo
        'simbolico' com fcb.alvo = alvo (sem blocos de dados); grava o bloco do pai (1 escrita).
        O alvo não precisa existir."""
        alvo = str(alvo)
        if not alvo.startswith("/"):
            raise ErroSA("EINVAL", "o alvo deve ser um caminho absoluto: %s" % alvo)
        pai, nome = self._pai_e_nome(caminho)
        if nome in pai.entradas:
            raise ErroSA("EEXIST", caminho)
        fcb = self._novo_fcb(nome, "simbolico", pai.id, processo)
        fcb.alvo = alvo
        pai.entradas[nome] = fcb.id
        self._gravar_diretorio(pai)
        return fcb

    def percorrer(self, processo, caminho):
        """Percorre a árvore a partir de `caminho` (como um `find`), lendo cada diretório
        visitado (1 leitura) e seguindo links simbólicos para diretórios. Não pode entrar em
        laço: ao chegar de novo a um diretório já visitado nesta travessia, conte
        self.contadores['ciclos_detectados'] e não desça. Contem também
        'percorrer_diretorios', 'percorrer_arquivos' e 'links_pendentes' (links simbólicos
        cujo alvo não existe; contem também os encontrados pelo open)."""
        inicio = self.resolver(caminho)
        visitados = set()
        c = self.contadores

        def alcancar(fcb):
            if fcb.tipo == "arquivo":
                c["percorrer_arquivos"] += 1
                return
            if fcb.id in visitados:           # já visitado nesta travessia: ciclo
                c["ciclos_detectados"] += 1
                return
            visitados.add(fcb.id)
            self._ler_diretorio(fcb)
            c["percorrer_diretorios"] += 1
            for nome in list(fcb.entradas):
                filho = self.fcbs[fcb.entradas[nome]]
                if filho.tipo == "simbolico":
                    try:
                        alvo = self.resolver(filho.alvo)       # segue o link (como find -L)
                    except ErroSA as e:
                        if e.codigo == "ENOENT":
                            c["links_pendentes"] += 1
                        elif e.codigo == "ELOOP":
                            c["links_em_laco"] += 1
                        continue
                    alcancar(alvo)
                else:
                    alcancar(filho)

        alcancar(inicio)

    # ================================================================ módulo M5
    # Os ganchos abaixo substituem (por virem depois na classe) as versões vazias da parte
    # FORNECIDA: com o M5, o open verifica permissões e o read/write verificam travas.
    _BITS = {"r": 4, "w": 2, "x": 1}
    _EXIGIDO = {"r": "r", "w": "w", "a": "w", "rw": "rw"}

    def _verificar_permissao(self, processo, fcb, modo):
        exigido = self._EXIGIDO[modo]
        usuario = self.usuario_de(processo)
        c = self.contadores
        if usuario == "root":
            c["verificacoes"] += 1
            return
        for posicao, (dono_acl, perms) in enumerate(fcb.acl, start=1):
            if dono_acl == usuario:                       # vale a primeira entrada do usuário
                c["verificacoes"] += posicao
                permitido = all(p in perms for p in exigido)
                break
        else:
            c["verificacoes"] += len(fcb.acl) + 1
            bits = (fcb.permissoes >> 6) & 7 if usuario == fcb.dono else fcb.permissoes & 7
            permitido = all(bits & self._BITS[p] for p in exigido)
        if not permitido:
            c["negados"] += 1
            raise ErroSA("EACCES", "%s não pode abrir %s em modo %s" % (usuario, fcb.nome, modo))

    def _verificar_trava(self, processo, fcb):
        entrada = self.tabela_global.get(fcb.id)
        if entrada is None or entrada.trava is None or entrada.trava == processo:
            return
        if self.bloqueio == "mandatorio":
            self.contadores["bloqueados_mandatorio"] += 1
            raise ErroSA("EBUSY", "%s travado por %s" % (fcb.nome, entrada.trava))
        self.contadores["violacoes_consultivo"] += 1      # consultiva: só quem pergunta respeita

    def _alterar_protecao(self, processo, caminho):
        fcb = self.resolver(caminho)
        usuario = self.usuario_de(processo)
        if usuario != "root" and usuario != fcb.dono:
            raise ErroSA("EPERM", "só o dono ou root alteram a proteção de %s" % caminho)
        return fcb

    def usuario(self, processo, nome):
        """Associa o processo a um usuário (nenhum acesso ao disco)."""
        self.usuarios[processo] = str(nome)

    def chmod(self, processo, caminho, permissoes):
        """Lê os diretórios até o arquivo; só o dono ou root (senão EPERM); grava o bloco do
        pai (1 escrita). permissoes em octal, como texto (ex.: '640')."""
        try:
            valor = int(str(permissoes), 8)   # o simulador converte '640' em 640: tratamos os dois
        except ValueError:
            raise ErroSA("EINVAL", "permissões inválidas: %s" % permissoes)
        if not 0 <= valor <= 0o777:
            raise ErroSA("EINVAL", "permissões inválidas: %s" % permissoes)
        fcb = self._alterar_protecao(processo, caminho)
        fcb.permissoes = valor
        self._gravar_diretorio(self._diretorio_dos_metadados(fcb))

    def setacl(self, processo, caminho, usuario, permissoes):
        """Como o chmod, mas acrescenta (ou substitui) a entrada (usuario, 'rwx') na ACL."""
        permissoes = str(permissoes)
        if len(permissoes) != 3 or any(ch not in (ok, "-") for ch, ok in zip(permissoes, "rwx")):
            raise ErroSA("EINVAL", "permissões de ACL inválidas: %s" % permissoes)
        fcb = self._alterar_protecao(processo, caminho)
        for k, (u, _) in enumerate(fcb.acl):
            if u == usuario:
                fcb.acl[k] = (usuario, permissoes)
                break
        else:
            fcb.acl.append((usuario, permissoes))
        self._gravar_diretorio(self._diretorio_dos_metadados(fcb))

    def lock(self, processo, handle):
        """Trava exclusiva do arquivo inteiro. Se outro processo detém a trava: EBUSY
        (conte 'travas_negadas'); senão conte 'travas_concedidas'."""
        entrada = self._entrada(processo, handle)
        global_ = self.tabela_global[entrada.fcb_id]
        if global_.trava is not None and global_.trava != processo:
            self.contadores["travas_negadas"] += 1
            raise ErroSA("EBUSY", "trava pertence a %s" % global_.trava)
        global_.trava = processo
        self.contadores["travas_concedidas"] += 1

    def unlock(self, processo, handle):
        entrada = self._entrada(processo, handle)
        global_ = self.tabela_global[entrada.fcb_id]
        if global_.trava == processo:
            global_.trava = None
        # destravar sem ser o dono não faz nada (como o fcntl F_UNLCK do POSIX)
