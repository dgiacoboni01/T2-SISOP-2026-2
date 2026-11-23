# -*- coding: utf-8 -*-
"""
Experimento EXTRA E3 — custo REAL de abrir um arquivo conforme a profundidade (complementa o X4).

Não é simulação: mede o sistema operacional da máquina em que roda. Por isso os números variam
de máquina para máquina e NÃO substituem os resultados oficiais (resultados/*.csv). Não altera
parametros.py, as cargas nem experimentos.py; grava em resultados/extras/ e graficos/extras/.

Para cada volume informado, cria uma cadeia de d diretórios (d = 1..10) com um arquivo no fim,
como no X4, e mede `os.open` + `os.close` desse arquivo muitas vezes. As primeiras aberturas
são descartadas (aquecimento), então o que se mede é a resolução de caminho com os metadados
já na cache do sistema (dcache no Linux, cache do NTFS/gerenciador de objetos no Windows).
Medir com a cache fria exigiria privilégio de administrador (ex.: drop_caches no Linux), o
que o trabalho não permite.

Uso:  python3 extras/e3_open_real.py [ROTULO=DIRETORIO ...]
      sem argumentos, usa o diretório temporário do sistema com o rótulo "padrao".
      Ex. (Windows com SSD em C: e HDD em D:):
      python extras/e3_open_real.py "SSD (C:)=C:/Temp" "HDD (D:)=D:/"
"""
import csv
import os
import shutil
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
SAIDA_RES = os.path.join(RAIZ, "resultados", "extras")
SAIDA_GRAF = os.path.join(RAIZ, "graficos", "extras")

PROFUNDIDADES = range(1, 11)
AQUECIMENTO = 200
REPETICOES = 5000
RODADAS = 5                  # rodadas intercaladas por profundidade, para diluir ruído do SO


def percentil(valores, p):
    v = sorted(valores)
    k = (len(v) - 1) * p / 100.0
    i = int(k)
    return v[i] if i + 1 >= len(v) else v[i] + (v[i + 1] - v[i]) * (k - i)


def criar_cadeia(base, d):
    caminho = base
    for i in range(1, d + 1):
        caminho = os.path.join(caminho, "n%d" % i)
    os.makedirs(caminho, exist_ok=True)
    arquivo = os.path.join(caminho, "f.txt")
    with open(arquivo, "wb") as f:
        f.write(b"x" * 4096)
    return arquivo


def medir(arquivo, n):
    amostras = []
    abrir, fechar, relogio = os.open, os.close, time.perf_counter_ns
    for _ in range(n):
        t0 = relogio()
        fd = abrir(arquivo, os.O_RDONLY)
        fechar(fd)
        amostras.append((relogio() - t0) / 1000.0)     # microssegundos
    return amostras


def tempos_simulados():
    """Tempo por open do X4 oficial (ms), para pôr lado a lado com a medição real."""
    sim = {}
    try:
        with open(os.path.join(RAIZ, "resultados", "arquivos_por_operacao.csv"), encoding="utf-8") as f:
            for l in csv.DictReader(f):
                if l["experimento"] == "X4" and l["operacao"] == "open":
                    sim[int(l["carga"].rsplit("_", 1)[1])] = float(l["tempo_medio_ms"])
    except (OSError, KeyError, ValueError):
        pass
    return sim


def medir_volume(rotulo, diretorio):
    base = tempfile.mkdtemp(prefix="simes_e3_", dir=diretorio)
    try:
        arquivos = {d: criar_cadeia(os.path.join(base, "d%02d" % d), d) for d in PROFUNDIDADES}
        for arq in arquivos.values():
            medir(arq, AQUECIMENTO)
        amostras = {d: [] for d in PROFUNDIDADES}
        for _ in range(RODADAS):
            for d in PROFUNDIDADES:
                amostras[d].extend(medir(arquivos[d], REPETICOES // RODADAS))
        return amostras
    finally:
        shutil.rmtree(base, ignore_errors=True)


def graficos(linhas):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib ausente: gráfico do E3 não gerado (use o CSV numa planilha)")
        return
    os.makedirs(SAIDA_GRAF, exist_ok=True)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    sim = sorted({(l["profundidade"], l["simulado_ms"]) for l in linhas if l["simulado_ms"] != ""})
    if sim:
        a1.plot([s[0] for s in sim], [s[1] for s in sim], "o-", color="#555555")
    a1.set_title("Simulador (X4 oficial): HDD modelado, sem cache")
    a1.set_xlabel("profundidade (diretórios)")
    a1.set_ylabel("tempo por open (ms)")
    a1.set_ylim(bottom=0)
    a1.grid(alpha=0.3)
    for rotulo in dict.fromkeys(l["volume"] for l in linhas):
        ls = [l for l in linhas if l["volume"] == rotulo]
        a2.plot([l["profundidade"] for l in ls], [l["mediana_us"] for l in ls], "o-", label=rotulo)
    a2.set_title("Máquina real (E3): metadados na cache")
    a2.set_xlabel("profundidade (diretórios)")
    a2.set_ylabel("tempo por open+close, mediana (µs)")
    a2.set_ylim(bottom=0)
    a2.grid(alpha=0.3)
    a2.legend()
    fig.suptitle("E3 — open por profundidade: 1 ms = 1.000 µs")
    fig.tight_layout()
    fig.savefig(os.path.join(SAIDA_GRAF, "E3_open_real.png"), dpi=130)
    plt.close(fig)


def main(argv):
    volumes = []
    for a in argv or ["padrao=" + tempfile.gettempdir()]:
        rotulo, _, diretorio = a.partition("=")
        volumes.append((rotulo, diretorio or tempfile.gettempdir()))
    sim = tempos_simulados()
    linhas = []
    for rotulo, diretorio in volumes:
        print("medindo %s ..." % rotulo)
        amostras = medir_volume(rotulo, diretorio)
        for d in PROFUNDIDADES:
            a = amostras[d]
            linhas.append({
                "volume": rotulo,
                "profundidade": d,
                "repeticoes": len(a),
                "mediana_us": round(percentil(a, 50), 2),
                "p95_us": round(percentil(a, 95), 2),
                "simulado_ms": sim.get(d, ""),
            })
    os.makedirs(SAIDA_RES, exist_ok=True)
    with open(os.path.join(SAIDA_RES, "E3_open_real.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    graficos(linhas)
    for l in linhas:
        print("%-10s d=%2d  mediana=%8.2f us  p95=%8.2f us  simulado=%s ms"
              % (l["volume"], l["profundidade"], l["mediana_us"], l["p95_us"], l["simulado_ms"]))


if __name__ == "__main__":
    main(sys.argv[1:])
