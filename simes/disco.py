# -*- coding: utf-8 -*-
"""
Modelos de dispositivo de armazenamento.

DiscoHDD  — FORNECIDO. Implementa o modelo de custo descrito em parametros.py.
DiscoFlash — BÔNUS. Armazenamento flash (SSD, celulares): implementem se quiserem o bônus.

Todo dispositivo oferece:
    atender(bloco, n_blocos, tipo) -> (duracao_ms, distancia_cilindros)
    mover(cilindro)                -> (duracao_ms, distancia_cilindros)
    cilindro_atual                 (atributo)
"""
from simes import parametros as P


def cilindro_do_bloco(bloco):
    return bloco // P.BLOCOS_POR_CILINDRO


class DiscoHDD:
    nome = "hdd"

    def __init__(self, busca_minima_ms=P.BUSCA_MINIMA_MS,
                 busca_por_cilindro_ms=P.BUSCA_POR_CILINDRO_MS,
                 latencia_rotacional_ms=P.LATENCIA_ROTACIONAL_MS,
                 transferencia_ms_por_bloco=P.TRANSFERENCIA_MS_POR_BLOCO,
                 cilindros=P.CILINDROS, cilindro_inicial=0, nome=None):
        self.busca_minima_ms = busca_minima_ms
        self.busca_por_cilindro_ms = busca_por_cilindro_ms
        self.latencia_rotacional_ms = latencia_rotacional_ms
        self.transferencia_ms_por_bloco = transferencia_ms_por_bloco
        self.cilindros = cilindros
        self.cilindro_atual = cilindro_inicial
        self.ultimo_bloco = None          # último bloco transferido (para detectar sequência)
        self.deslocamento_total = 0       # cilindros percorridos pelo braço
        if nome:
            self.nome = nome

    def tempo_busca(self, distancia):
        if distancia == 0:
            return 0.0
        return self.busca_minima_ms + distancia * self.busca_por_cilindro_ms

    def mover(self, cilindro):
        """Leva o braço até um cilindro sem transferir dados (usado pelo SCAN e pelo C-SCAN)."""
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        self.cilindro_atual = cilindro
        self.ultimo_bloco = None
        self.deslocamento_total += distancia
        return duracao, distancia

    def atender(self, bloco, n_blocos=1, tipo="L"):
        cilindro = cilindro_do_bloco(bloco)
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        em_sequencia = self.ultimo_bloco is not None and bloco == self.ultimo_bloco + 1
        if not em_sequencia:
            duracao += self.latencia_rotacional_ms
        duracao += n_blocos * self.transferencia_ms_por_bloco
        ultimo = bloco + n_blocos - 1
        self.cilindro_atual = cilindro_do_bloco(ultimo)
        self.ultimo_bloco = ultimo
        self.deslocamento_total += distancia
        return duracao, distancia


def criar_disco(nome):
    """Fábrica usada pelos simuladores e pelos experimentos."""
    if nome == "hdd":
        return DiscoHDD()
    if nome == "rapido":
        return DiscoHDD(nome="rapido", **P.DISPOSITIVO_RAPIDO)
    if nome == "flash":
        return DiscoFlash()
    raise ValueError("dispositivo desconhecido: %s" % nome)


# ======================================================================
# BÔNUS — armazenamento flash
# ======================================================================
class DiscoFlash:
    """
    Simula um SSD com uma camada de tradução simples (FTL) do tipo log:

    - uma página flash = um bloco lógico de 4 KiB;
    - páginas são agrupadas em blocos apagáveis de FLASH_PAGINAS_POR_BLOCO_APAGAVEL páginas;
    - uma página só pode ser gravada se estiver apagada; apagar só é possível por bloco inteiro;
    - regravar um bloco lógico grava em uma página livre nova e invalida a antiga;
    - quando faltam páginas livres, a coleta de lixo escolhe um bloco apagável, copia as
      páginas ainda válidas para outro lugar e apaga o bloco.

    Não há busca nem latência rotacional. Tempos em parametros.py (FLASH_*).
    Indicadores que o dispositivo deve expor (atributos):
        paginas_gravadas_host      páginas pedidas pelo sistema
        paginas_programadas        páginas efetivamente gravadas (inclui cópias da coleta de lixo)
        apagamentos                blocos apagados
    Amplificação de escrita = paginas_programadas / paginas_gravadas_host.
    """
    nome = "flash"
    RESERVA_GC = 2          # blocos apagados mantidos de reserva para a coleta de lixo

    def __init__(self, total_blocos_logicos=P.TOTAL_BLOCOS, area_reserva=P.FLASH_AREA_RESERVA,
                 paginas_por_bloco=P.FLASH_PAGINAS_POR_BLOCO_APAGAVEL):
        self.cilindro_atual = 0
        self.cilindros = P.CILINDROS          # o simulador de E/S consulta (sem efeito no flash)
        self.deslocamento_total = 0
        self.paginas_gravadas_host = 0
        self.paginas_programadas = 0
        self.apagamentos = 0
        self.paginas_lidas = 0
        self.paginas_copiadas_gc = 0
        self.coletas = 0
        self.total_logico = total_blocos_logicos
        self.ppb = paginas_por_bloco
        fisicas = int(total_blocos_logicos * (1.0 + area_reserva) + 0.999999)
        self.n_blocos = max(self.RESERVA_GC + 2, -(-fisicas // self.ppb))
        n_paginas = self.n_blocos * self.ppb
        # FTL: mapa lógico -> físico (esparso) e o reverso, por página física (-1 = sem dono)
        self.mapa = {}
        self.dono = [-1] * n_paginas
        self.validas = [0] * self.n_blocos          # páginas válidas por bloco apagável
        self.desgaste = [0] * self.n_blocos          # apagamentos por bloco (desgaste)
        self.livres = list(range(self.n_blocos - 1, -1, -1))   # blocos apagados (pilha)
        self.ativo = self.livres.pop()               # bloco que recebe as gravações (log)
        self.proxima = 0                             # próxima página livre no bloco ativo

    # ------------------------------------------------------------------ FTL
    def _pagina_livre(self, coletando=False):
        """Próxima página apagada do log. Troca de bloco ativo quando ele enche; se faltarem
        blocos apagados, roda a coleta de lixo antes (nunca durante a própria coleta)."""
        custo = 0.0
        if self.proxima == self.ppb:
            if not coletando:
                while len(self.livres) <= self.RESERVA_GC:
                    custo += self._coletar()
            self.ativo = self.livres.pop()
            self.proxima = 0
        pagina = self.ativo * self.ppb + self.proxima
        self.proxima += 1
        return pagina, custo

    def _programar(self, logico, coletando=False):
        pagina, custo = self._pagina_livre(coletando)
        antiga = self.mapa.get(logico)
        if antiga is not None:                       # regravação: invalida a página antiga
            self.dono[antiga] = -1
            self.validas[antiga // self.ppb] -= 1
        self.mapa[logico] = pagina
        self.dono[pagina] = logico
        self.validas[pagina // self.ppb] += 1
        self.paginas_programadas += 1
        return custo + P.FLASH_PROGRAMACAO_MS

    def _coletar(self):
        """Coleta de lixo gulosa: escolhe o bloco cheio com menos páginas válidas, copia as
        válidas para o log e apaga o bloco. Devolve o tempo gasto."""
        candidatos = set(range(self.n_blocos)) - set(self.livres) - {self.ativo}
        vitima = min(candidatos, key=lambda b: (self.validas[b], self.desgaste[b], b))
        if self.validas[vitima] >= self.ppb:
            raise RuntimeError("flash cheio: nenhuma página inválida para recuperar")
        self.coletas += 1
        custo = 0.0
        base = vitima * self.ppb
        for pagina in range(base, base + self.ppb):
            logico = self.dono[pagina]
            if logico >= 0:
                custo += P.FLASH_LEITURA_MS
                custo += self._programar(logico, coletando=True)
                self.paginas_copiadas_gc += 1
        self.apagamentos += 1
        self.desgaste[vitima] += 1
        self.livres.insert(0, vitima)    # vai para o fundo da pilha (nivela o desgaste)
        return custo + P.FLASH_APAGAMENTO_MS

    # ------------------------------------------------------------ interface
    def mover(self, cilindro):
        return 0.0, 0

    def atender(self, bloco, n_blocos=1, tipo="L"):
        if bloco < 0 or bloco + n_blocos > self.total_logico:
            raise ValueError("bloco fora do dispositivo: %d" % bloco)
        if tipo == "L":
            self.paginas_lidas += n_blocos
            return n_blocos * P.FLASH_LEITURA_MS, 0
        duracao = 0.0
        for logico in range(bloco, bloco + n_blocos):
            self.paginas_gravadas_host += 1
            duracao += self._programar(logico)
        return duracao, 0

    def preencher(self, fracao=1.0, regravacoes=0, semente=1):
        """Leva o dispositivo a um estado "usado" sem contar tempo nem indicadores: grava
        `fracao` dos blocos lógicos em ordem e depois `regravacoes` blocos aleatórios.
        Usado só em experimentos EXTRAS (o X13 oficial usa o dispositivo novo)."""
        import random
        rng = random.Random(semente)
        n = int(self.total_logico * fracao)
        for logico in range(n):
            self._programar(logico)
        for _ in range(regravacoes):
            self._programar(rng.randrange(max(1, n)))
        self.paginas_gravadas_host = self.paginas_programadas = self.apagamentos = 0
        self.paginas_lidas = self.paginas_copiadas_gc = self.coletas = 0

    @property
    def amplificacao_escrita(self):
        return self.paginas_programadas / float(max(1, self.paginas_gravadas_host))
