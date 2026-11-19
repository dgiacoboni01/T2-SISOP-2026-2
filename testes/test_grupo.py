# -*- coding: utf-8 -*-
"""Testes acrescentados pelo grupo: casos de borda das regras que os testes fornecidos não cobrem."""
import random
import unittest

from simes import parametros as P
from simes.arquivos.alocacao import AlocadorEncadeado, AlocadorFAT, AlocadorIndexado
from simes.arquivos.estruturas import FCB, ErroSA
from simes.arquivos.simulador import montar_sistema
from simes.disco import DiscoFlash
from simes.es.buffering import simular_buffering
from simes.es.cache import CacheFIFO, CacheLRU
from simes.es.cpu import custo_cpu
from simes.es.escalonadores import CLOOK, CSCAN, LOOK, SCAN, SSTF, Deadline, percorrer
from simes.es.requisicao import Requisicao
from simes.integracao import historico_para_requisicoes

FILA_LIVRO = [98, 183, 37, 122, 14, 124, 65, 67]


def req(i, cilindro, chegada=0.0, tipo="L"):
    return Requisicao(i, chegada, "P1", tipo, cilindro * P.BLOCOS_POR_CILINDRO)


class TesteEscalonadoresBorda(unittest.TestCase):
    def test_mesmo_cilindro_conta_como_a_frente(self):
        for esc in (SCAN(), LOOK(), CSCAN(), CLOOK()):
            i, _, passar = esc.escolher([req(0, 10), req(1, 50)], 50, -1 if esc.nome in ("SCAN", "LOOK") else +1, 0.0, 199)
            self.assertEqual((i, passar), (1, []), esc.nome)

    def test_empate_vence_a_mais_antiga(self):
        i, d, _ = SSTF().escolher([req(0, 60), req(1, 40)], 50, +1, 0.0, 199)
        self.assertEqual((i, d), (0, +1))
        i, d, _ = SSTF().escolher([req(0, 40), req(1, 60)], 50, +1, 0.0, 199)
        self.assertEqual((i, d), (0, -1))

    def test_scan_vai_ate_a_borda_e_inverte(self):
        i, d, passar = SCAN().escolher([req(0, 10)], 50, +1, 0.0, 199)
        self.assertEqual((i, d, passar), (0, -1, [199]))

    def test_cscan_volta_ao_zero(self):
        i, d, passar = CSCAN().escolher([req(0, 30), req(1, 10)], 50, +1, 0.0, 199)
        self.assertEqual((i, d, passar), (1, +1, [199, 0]))

    def test_look_e_clook_ordem_do_livro(self):
        self.assertEqual(percorrer(LOOK(), FILA_LIVRO, 53)[0], [65, 67, 98, 122, 124, 183, 37, 14])
        self.assertEqual(percorrer(CLOOK(), FILA_LIVRO, 53)[0], [65, 67, 98, 122, 124, 183, 14, 37])

    def test_deadline_mantem_direcao_e_desempata_pela_fila(self):
        a = Requisicao(0, 0.0, "P1", "L", 10 * P.BLOCOS_POR_CILINDRO)
        b = Requisicao(1, 0.0, "P1", "L", 90 * P.BLOCOS_POR_CILINDRO)
        i, d, _ = Deadline().escolher([b, a], 50, +1, 500.0, 199)   # mesmo prazo: o primeiro da fila
        self.assertEqual((i, d), (0, +1))


class TesteCPUeBuffering(unittest.TestCase):
    def test_dma_so_compensa_a_partir_de_2_blocos(self):
        self.assertLess(custo_cpu("interrupcao", 1, 5.0), custo_cpu("dma", 1, 5.0))
        self.assertGreater(custo_cpu("interrupcao", 2, 5.0), custo_cpu("dma", 2, 5.0))

    def test_bloqueio_do_produtor(self):
        r = simular_buffering(1, 2.0, 8.0, 3)
        self.assertAlmostEqual(r["bloqueio_produtor_ms"], 2 * 8.0)
        self.assertAlmostEqual(r["tempo_total_ms"], 30.0)
        r = simular_buffering(3, 8.0, 2.0, 10)                   # dispositivo rápido: sem bloqueio
        self.assertAlmostEqual(r["bloqueio_produtor_ms"], 0.0)
        self.assertAlmostEqual(r["tempo_total_ms"], 10 * 8.0 + 2.0)


class TesteCacheBorda(unittest.TestCase):
    def test_escrita_e_acesso_na_lru_mas_nao_na_fifo(self):
        lru, fifo = CacheLRU(2, "write-through"), CacheFIFO(2, "write-through")
        for c in (lru, fifo):
            c.inserir(1)
            c.inserir(2)
            c.escrever(1)
            c.inserir(3)
        self.assertTrue(lru.ler(1) and not lru.ler(2))
        self.assertTrue(fifo.ler(2) and not fifo.ler(1))

    def test_write_back_regravar_nao_expulsa(self):
        wb = CacheLRU(2, "write-back")
        self.assertEqual(wb.escrever(5), [])
        self.assertEqual(wb.escrever(5), [])
        self.assertEqual(wb.sujos(), 1)


class TesteAlocadoresBorda(unittest.TestCase):
    def test_encadeada_estender_grava_o_antigo_ultimo(self):
        a = AlocadorEncadeado(100)
        f = FCB(1, "f", "arquivo", 0)
        a.alocar(f, 3)
        self.assertEqual(a.estender(f, 2), [("E", f.blocos[2])])
        self.assertEqual(len(a.bloco_fisico(f, 2)[1]), 2)
        self.assertEqual(len(a.bloco_fisico(f, 1)[1]), 1)       # para trás: recomeça

    def test_fat_sem_extras(self):
        a = AlocadorFAT(100)
        f = FCB(1, "f", "arquivo", 0)
        a.alocar(f, 3)
        self.assertEqual(a.estender(f, 5), [])
        self.assertEqual(a.bloco_fisico(f, 7)[1], [])

    def test_indexada_passa_a_dois_niveis(self):
        a = AlocadorIndexado(5000)
        f = FCB(1, "f", "arquivo", 0)
        self.assertEqual(len(a.alocar(f, 0)), 1)                 # arquivo vazio também tem índice
        extras = a.estender(f, 1025)
        self.assertEqual(len(f.extra["indices"]), 3)             # 1 + ceil(1025/1024)
        self.assertEqual(len(extras), 3)
        self.assertEqual(len(a.bloco_fisico(f, 1024)[1]), 2)
        livres = a.bitmap.livres()
        a.liberar(f)
        self.assertEqual(a.bitmap.livres(), livres + 1025 + 3)

    def test_enospc_conta_falha(self):
        a = AlocadorIndexado(10)
        f = FCB(1, "f", "arquivo", 0)
        with self.assertRaises(ErroSA):
            a.alocar(f, 10)                                      # 10 dados + 1 índice > 10
        self.assertEqual(a.falhas, 1)


class TesteSistemaBorda(unittest.TestCase):
    def setUp(self):
        self.sa = montar_sistema("contigua", total_blocos=5000)
        self.sa.mkdir("P1", "/d")
        self.sa.create("P1", "/d/a", 8192)

    def test_link_simbolico_no_meio_do_caminho(self):
        self.sa.symlink("P1", "/d", "/atalho")
        c = self.sa.camada
        antes = c.leituras
        self.assertIs(self.sa.resolver("/atalho/a"), self.sa.resolver("/d/a"))
        # /atalho/a: lê "/" (acha o link), recomeça: lê "/" e "/d"  -> 3 leituras; /d/a -> 2
        self.assertEqual(c.leituras - antes, 3 + 2)

    def test_resolver_sem_seguir_o_ultimo(self):
        self.sa.symlink("P1", "/d/a", "/d/s")
        self.assertEqual(self.sa.resolver("/d/s", seguir_simbolico=False).tipo, "simbolico")

    def test_link_pendente_contado_no_open(self):
        self.sa.symlink("P1", "/d/nao_existe", "/d/p")
        with self.assertRaises(ErroSA):
            self.sa.open("P1", "/d/p", "r", "h")
        with self.assertRaises(ErroSA):
            self.sa.open("P1", "/d/nada", "r", "h")              # ENOENT comum: não é link pendente
        self.assertEqual(self.sa.contadores["links_pendentes"], 1)

    def test_unlink_de_diretorio_e_handle_repetido(self):
        with self.assertRaises(ErroSA) as ctx:
            self.sa.unlink("P1", "/d")
        self.assertEqual(ctx.exception.codigo, "EISDIR")
        self.sa.open("P1", "/d/a", "r", "h")
        with self.assertRaises(ErroSA) as ctx:
            self.sa.open("P1", "/d/a", "r", "h")
        self.assertEqual(ctx.exception.codigo, "EINVAL")

    def test_hard_link_mantem_os_blocos(self):
        self.sa.link("P1", "/d/a", "/b")
        livres = self.sa.alocador.bitmap.livres()
        self.sa.unlink("P1", "/d/a")
        self.assertEqual(self.sa.alocador.bitmap.livres(), livres)
        self.sa.unlink("P1", "/b")
        self.assertEqual(self.sa.alocador.bitmap.livres(), livres + 2)

    def test_close_libera_trava_e_consultivo_conta_violacao(self):
        self.sa.open("P1", "/d/a", "rw", "h")
        self.sa.open("P2", "/d/a", "rw", "h")
        self.sa.lock("P1", "h")
        self.sa.write("P2", "h", 10)                              # consultivo: passa
        self.assertEqual(self.sa.contadores["violacoes_consultivo"], 1)
        self.sa.close("P1", "h")
        self.sa.lock("P2", "h")                                   # trava foi liberada no close
        self.assertEqual(self.sa.contadores["travas_concedidas"], 2)

    def test_percorrer_conta_diretorios_e_arquivos(self):
        self.sa.mkdir("P1", "/d/e")
        self.sa.create("P1", "/d/e/x", 10)
        self.sa.percorrer("P1", "/")
        c = self.sa.contadores
        self.assertEqual((c["percorrer_diretorios"], c["percorrer_arquivos"], c["ciclos_detectados"]), (3, 2, 0))


class TesteFlashEIntegracao(unittest.TestCase):
    def test_flash_sem_coleta_quando_novo(self):
        d = DiscoFlash(total_blocos_logicos=6400)
        t, dist = d.atender(10, 4, "E")
        self.assertAlmostEqual(t, 4 * P.FLASH_PROGRAMACAO_MS)
        self.assertEqual((dist, d.amplificacao_escrita, d.apagamentos), (0, 1.0, 0))
        self.assertAlmostEqual(d.atender(10, 4, "L")[0], 4 * P.FLASH_LEITURA_MS)

    def test_flash_cheio_amplifica(self):
        d = DiscoFlash(total_blocos_logicos=6400)
        rng = random.Random(1)
        gravados = set()
        for _ in range(30000):
            b = rng.randrange(6400)
            gravados.add(b)
            d.atender(b, 1, "E")
        self.assertGreater(d.apagamentos, 0)
        self.assertGreater(d.amplificacao_escrita, 2.0)
        self.assertEqual(set(d.mapa), gravados)                    # nenhum dado perdido na coleta
        self.assertEqual(sum(d.validas), len(gravados))

    def test_historico_mesmo_instante_mantem_ordem(self):
        h = [(2.0, "P1", "L", 5, 1), (1.0, "P1", "L", 9, 1), (2.0, "P1", "E", 3, 1)]
        reqs = historico_para_requisicoes(h)
        self.assertEqual([r.bloco for r in reqs], [9, 5, 3])


if __name__ == "__main__":
    unittest.main()
