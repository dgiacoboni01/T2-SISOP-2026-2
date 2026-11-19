# -*- coding: utf-8 -*-
"""
Experimentos EXTRAS do grupo (fora dos oficiais de experimentos.py).

Não alteram parametros.py, as cargas nem experimentos.py: rodam em configuração separada e
gravam em resultados/extras/ e graficos/extras/. Estão declarados no relatório.

E1 — Flash em estado estacionário (complementa o X13). No X13 oficial o dispositivo flash
     começa vazio e as cargas gravam poucos milhares de páginas, então a coleta de lixo
     nunca roda (amplificação = 1). Aqui um flash menor (32.000 páginas lógicas) é antes
     preenchido e "envelhecido" e depois recebe gravações aleatórias de 1 página, com
     diferentes áreas de reserva (overprovisioning).
E2 — Tempestade de gravação do write-back (complementa o X6). Mede o tempo de resposta das
     requisições que chegam logo depois de cada sincronização periódica e varia o intervalo
     de sincronização (o padrão é 5.000 ms).

Uso:  python3 extras/experimentos_extras.py
"""
import csv
import os
import random
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

from simes import parametros as P                      # noqa: E402
from simes.cargas import ler_carga_es                   # noqa: E402
from simes.disco import DiscoFlash, DiscoHDD            # noqa: E402
from simes.es.cache import criar_cache                  # noqa: E402
from simes.es.escalonadores import criar_escalonador    # noqa: E402
from simes.es.simulador import simular_es               # noqa: E402
from simes.metricas import media, percentil             # noqa: E402

SAIDA_RES = os.path.join(RAIZ, "resultados", "extras")
SAIDA_GRAF = os.path.join(RAIZ, "graficos", "extras")


def gravar(nome, linhas):
    os.makedirs(SAIDA_RES, exist_ok=True)
    with open(os.path.join(SAIDA_RES, nome), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        for l in linhas:
            w.writerow({k: ("%.4f" % v if isinstance(v, float) else v) for k, v in l.items()})


def e1_flash():
    linhas = []
    logicas = 32000
    for reserva in (0.07, 0.15, 0.28):
        for ocupacao in (0.5, 1.0):
            d = DiscoFlash(total_blocos_logicos=logicas, area_reserva=reserva)
            d.preencher(fracao=1.0, regravacoes=2 * logicas, semente=3)
            rng = random.Random(42)
            alcance = int(logicas * ocupacao)       # parte do espaço lógico que é regravada
            tempos = []
            for _ in range(60000):
                t, _ = d.atender(rng.randrange(alcance), 1, "E")
                tempos.append(t)
            linhas.append({
                "area_reserva": reserva, "fracao_regravada": ocupacao,
                "paginas_gravadas_host": d.paginas_gravadas_host,
                "paginas_programadas": d.paginas_programadas,
                "amplificacao_escrita": d.amplificacao_escrita,
                "apagamentos": d.apagamentos,
                "escrita_media_ms": media(tempos),
                "escrita_p99_ms": percentil(tempos, 99),
                "escrita_max_ms": max(tempos),
            })
    gravar("E1_flash_estado_estacionario.csv", linhas)
    return linhas


def e2_tempestade():
    reqs = ler_carga_es(os.path.join(RAIZ, "cargas", "es_reuso.txt"))
    original = P.INTERVALO_SYNC_MS
    linhas = []
    try:
        for intervalo in (1000.0, 5000.0, 30000.0):
            P.INTERVALO_SYNC_MS = intervalo
            for esc in ("FCFS", "SSTF"):
                cache = criar_cache("LRU", 512, "write-back")
                res = simular_es(reqs, criar_escalonador(esc), DiscoHDD(), "interrupcao", cache=cache)
                fim = res["tempo_total_ms"]
                syncs = [k * intervalo for k in range(1, int(fim // intervalo) + 1)]
                perto, longe = [], []
                for r in res["concluidas"]:
                    if r.tipo != "L":
                        continue
                    depois = [r.chegada - s for s in syncs if 0 <= r.chegada - s < 1000.0]
                    (perto if depois else longe).append(r.resposta)
                linhas.append({
                    "intervalo_sync_ms": intervalo, "escalonador": esc,
                    "resposta_media_ms": res["resposta_media_ms"],
                    "resposta_p95_ms": res["resposta_p95_ms"],
                    "leituras_ate_1s_apos_sync": len(perto),
                    "resposta_media_leitura_apos_sync_ms": media(perto),
                    "resposta_media_leitura_demais_ms": media(longe),
                    "escritas_fisicas": res["escritas_fisicas"],
                })
    finally:
        P.INTERVALO_SYNC_MS = original
    gravar("E2_tempestade_write_back.csv", linhas)
    return linhas


def graficos(l1, l2):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib ausente: gráficos extras não gerados")
        return
    os.makedirs(SAIDA_GRAF, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 4))
    for frac in (0.5, 1.0):
        pts = [(l["area_reserva"] * 100, l["amplificacao_escrita"]) for l in l1 if l["fracao_regravada"] == frac]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o",
                label="regravando %d%% do espaço lógico" % (frac * 100))
    ax.set_xlabel("área de reserva (overprovisioning), %")
    ax.set_ylabel("amplificação de escrita (págs. programadas / págs. do host)")
    ax.set_title("E1 — Flash em estado estacionário")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(SAIDA_GRAF, "E1_flash_amplificacao.png"), dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    rot = ["%d s\n%s" % (l["intervalo_sync_ms"] / 1000, l["escalonador"]) for l in l2]
    xs = range(len(l2))
    ax.bar([x - 0.2 for x in xs], [l["resposta_media_leitura_apos_sync_ms"] for l in l2], 0.4,
           label="leituras até 1 s após um sync")
    ax.bar([x + 0.2 for x in xs], [l["resposta_media_leitura_demais_ms"] for l in l2], 0.4,
           label="demais leituras")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(rot, fontsize=8)
    ax.set_ylabel("resposta média (ms)")
    ax.set_title("E2 — Write-back (LRU 512): leituras logo após a sincronização")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(SAIDA_GRAF, "E2_tempestade_write_back.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    l1 = e1_flash()
    for l in l1:
        print("E1", l)
    l2 = e2_tempestade()
    for l in l2:
        print("E2", l)
    graficos(l1, l2)
    print("Resultados extras em", SAIDA_RES)
