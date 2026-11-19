# -*- coding: utf-8 -*-
"""
Módulo M1 — cache de blocos (buffer cache).

Referência: Silberschatz, 9ª ed., 13.4.3 (Caching) e 12.6 (Efficiency and Performance) — pesquisa.

Implementem CacheLRU e CacheFIFO com a interface abaixo. O simulador de E/S chama:

    ler(bloco) -> bool
        True se o bloco está na cache (acerto). Num acerto, a LRU atualiza a recência;
        a FIFO não muda nada.
    inserir(bloco) -> list[int]
        Chamado depois de uma falta de leitura, quando o bloco chega do disco. Se a cache
        estiver cheia, expulsa uma vítima (LRU: a usada há mais tempo; FIFO: a que entrou
        primeiro). Devolve os blocos SUJOS expulsos, que o simulador vai gravar no disco.
    escrever(bloco) -> list[int]
        Escrita do usuário. Coloca (ou atualiza) o bloco na cache e devolve a lista de blocos
        que precisam ser gravados no disco AGORA:
          write-through: o próprio bloco (+ sujos expulsos, que não existem nesta política);
          write-back:    só os blocos sujos expulsos; o bloco escrito fica marcado como sujo.
        Uma escrita conta como acesso para a LRU.
    sincronizar() -> list[int]
        Gravação periódica (a cada INTERVALO_SYNC_MS): devolve todos os blocos sujos e os
        marca como limpos.
    sujos() -> int
        Quantos blocos estão sujos agora (usado para medir a perda numa queda de energia).

Política de escrita: 'write-through' ou 'write-back'.
"""
from collections import OrderedDict


class Cache:
    nome = "?"

    def __init__(self, capacidade, escrita="write-back"):
        if escrita not in ("write-through", "write-back"):
            raise ValueError("escrita deve ser 'write-through' ou 'write-back'")
        self.capacidade = capacidade
        self.escrita = escrita

    def ler(self, bloco):
        raise NotImplementedError

    def inserir(self, bloco):
        raise NotImplementedError

    def escrever(self, bloco):
        raise NotImplementedError

    def sincronizar(self):
        raise NotImplementedError

    def sujos(self):
        raise NotImplementedError


class _CacheOrdenada(Cache):
    """Base comum: um OrderedDict bloco -> sujo, na ordem de expulsão (a vítima é o primeiro).
    A LRU move o bloco para o fim a cada acesso; a FIFO mantém a ordem de entrada."""
    atualiza_no_acesso = False

    def __init__(self, capacidade, escrita="write-back"):
        super().__init__(capacidade, escrita)
        if capacidade < 1:
            raise ValueError("capacidade deve ser >= 1")
        self.blocos = OrderedDict()
        self.expulsoes = 0

    def _tocar(self, bloco):
        if self.atualiza_no_acesso:
            self.blocos.move_to_end(bloco)

    def _abrir_espaco(self):
        """Expulsa vítimas até caber um bloco novo; devolve as sujas (a gravar)."""
        sujos = []
        while len(self.blocos) >= self.capacidade:
            vitima, suja = self.blocos.popitem(last=False)
            self.expulsoes += 1
            if suja:
                sujos.append(vitima)
        return sujos

    def ler(self, bloco):
        if bloco in self.blocos:
            self._tocar(bloco)
            return True
        return False

    def inserir(self, bloco):
        if bloco in self.blocos:          # chegou do disco mas já entrou por outra via
            self._tocar(bloco)
            return []
        sujos = self._abrir_espaco()
        self.blocos[bloco] = False
        return sujos

    def escrever(self, bloco):
        if bloco in self.blocos:
            self._tocar(bloco)
            sujos = []
        else:
            sujos = self._abrir_espaco()
        if self.escrita == "write-through":
            self.blocos[bloco] = False    # a cópia do disco fica igual: nunca há sujos
            return [bloco] + sujos
        self.blocos[bloco] = True         # write-back: só marca; grava na expulsão ou no sync
        return sujos

    def sincronizar(self):
        sujos = [b for b, s in self.blocos.items() if s]
        for b in sujos:
            self.blocos[b] = False
        return sujos

    def sujos(self):
        return sum(1 for s in self.blocos.values() if s)


class CacheLRU(_CacheOrdenada):
    nome = "LRU"
    atualiza_no_acesso = True


class CacheFIFO(_CacheOrdenada):
    nome = "FIFO"
    atualiza_no_acesso = False


CACHES = {"LRU": CacheLRU, "FIFO": CacheFIFO}


def criar_cache(nome, capacidade, escrita):
    try:
        return CACHES[nome](capacidade, escrita)
    except KeyError:
        raise ValueError("cache desconhecida: %s" % nome)
