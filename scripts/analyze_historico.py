"""
Relatorio de performance — le historico.csv e envia analise via Telegram.

Roda toda segunda-feira as 09h BRT via GitHub Actions, ou manualmente.
Metricas: taxa geral, por liga, por faixa de score, por faixa de odd.
"""

import csv
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from core.database import Database
from alerts.telegram_bot import TelegramBot

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
HISTORICO_PATH = os.path.join(DATA_DIR, "historico.csv")


def _taxa(acertos: int, erros: int) -> str:
    if (acertos + erros) == 0:
        return "N/A"
    return f"{round(acertos / (acertos + erros) * 100, 1)}%"


def main() -> None:
    logger.info("=== Relatorio de Performance iniciado ===")

    db = Database()
    db.initialize()
    telegram = TelegramBot(db=db)

    if not os.path.exists(HISTORICO_PATH):
        telegram.send("Relatorio: historico.csv ainda nao existe. Aguarde o primeiro ciclo completo.", tipo_alerta="SISTEMA")
        return

    with open(HISTORICO_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        telegram.send("Relatorio: historico.csv vazio — nenhum dado coletado ainda.", tipo_alerta="SISTEMA")
        return

    # Geral
    total = len(rows)
    acertos = sum(1 for r in rows if r.get("favorito_ganhou") == "True")
    erros = sum(1 for r in rows if r.get("favorito_ganhou") == "False")
    pendentes = total - acertos - erros

    # Periodo coberto
    datas = sorted(r.get("data", "") for r in rows if r.get("data"))
    periodo = f"{datas[0]} a {datas[-1]}" if datas else "N/A"

    # Por liga
    por_liga: dict[str, dict] = defaultdict(lambda: {"a": 0, "e": 0})
    for r in rows:
        liga = r.get("competition", "?")
        if r.get("favorito_ganhou") == "True":
            por_liga[liga]["a"] += 1
        elif r.get("favorito_ganhou") == "False":
            por_liga[liga]["e"] += 1

    linhas_liga = []
    for liga, s in sorted(por_liga.items(), key=lambda x: -(x[1]["a"] + x[1]["e"])):
        a, e = s["a"], s["e"]
        if (a + e) > 0:
            linhas_liga.append(f"  {liga}: {_taxa(a, e)} ({a}W/{e}L)")

    # Por faixa de score
    faixas_score = {"50-64": {"a": 0, "e": 0}, "65-79": {"a": 0, "e": 0}, "80-100": {"a": 0, "e": 0}}
    for r in rows:
        try:
            sc = int(float(r.get("score", 0) or 0))
        except (ValueError, TypeError):
            continue
        if 50 <= sc < 65:
            k = "50-64"
        elif 65 <= sc < 80:
            k = "65-79"
        elif sc >= 80:
            k = "80-100"
        else:
            continue
        if r.get("favorito_ganhou") == "True":
            faixas_score[k]["a"] += 1
        elif r.get("favorito_ganhou") == "False":
            faixas_score[k]["e"] += 1

    linhas_score = []
    for k in ("50-64", "65-79", "80-100"):
        a, e = faixas_score[k]["a"], faixas_score[k]["e"]
        if (a + e) > 0:
            linhas_score.append(f"  Score {k}: {_taxa(a, e)} ({a}W/{e}L)")

    # Por faixa de odd
    faixas_odd = {"1.35-1.44": {"a": 0, "e": 0}, "1.45-1.55": {"a": 0, "e": 0}, "1.56-1.60": {"a": 0, "e": 0}}
    for r in rows:
        try:
            odd = float(r.get("odd_favorito", 0) or 0)
        except (ValueError, TypeError):
            continue
        if 1.35 <= odd < 1.45:
            k = "1.35-1.44"
        elif 1.45 <= odd <= 1.55:
            k = "1.45-1.55"
        elif 1.55 < odd <= 1.60:
            k = "1.56-1.60"
        else:
            continue
        if r.get("favorito_ganhou") == "True":
            faixas_odd[k]["a"] += 1
        elif r.get("favorito_ganhou") == "False":
            faixas_odd[k]["e"] += 1

    linhas_odd = []
    for k in ("1.35-1.44", "1.45-1.55", "1.56-1.60"):
        a, e = faixas_odd[k]["a"], faixas_odd[k]["e"]
        if (a + e) > 0:
            linhas_odd.append(f"  Odd {k}: {_taxa(a, e)} ({a}W/{e}L)")

    # Recomendacao automatica (qual faixa tem maior taxa com >= 5 amostras)
    melhor_score_k = max(
        (k for k in faixas_score if (faixas_score[k]["a"] + faixas_score[k]["e"]) >= 5),
        key=lambda k: faixas_score[k]["a"] / max(faixas_score[k]["a"] + faixas_score[k]["e"], 1),
        default=None,
    )
    melhor_odd_k = max(
        (k for k in faixas_odd if (faixas_odd[k]["a"] + faixas_odd[k]["e"]) >= 5),
        key=lambda k: faixas_odd[k]["a"] / max(faixas_odd[k]["a"] + faixas_odd[k]["e"], 1),
        default=None,
    )

    recomendacoes = []
    if melhor_score_k:
        recomendacoes.append(f"  Melhor score: {melhor_score_k} ({_taxa(faixas_score[melhor_score_k]['a'], faixas_score[melhor_score_k]['e'])})")
    if melhor_odd_k:
        recomendacoes.append(f"  Melhor odd: {melhor_odd_k} ({_taxa(faixas_odd[melhor_odd_k]['a'], faixas_odd[melhor_odd_k]['e'])})")

    msg = (
        f"RELATORIO DE PERFORMANCE\n"
        f"Periodo: {periodo}\n\n"
        f"Total selecoes: {total}\n"
        f"Acertos: {acertos} | Erros: {erros} | Pendentes: {pendentes}\n"
        f"Taxa geral: {_taxa(acertos, erros)}\n\n"
        + (f"Por liga:\n" + "\n".join(linhas_liga) + "\n\n" if linhas_liga else "")
        + (f"Por faixa de score:\n" + "\n".join(linhas_score) + "\n\n" if linhas_score else "")
        + (f"Por faixa de odd:\n" + "\n".join(linhas_odd) + "\n\n" if linhas_odd else "")
        + (f"Insights:\n" + "\n".join(recomendacoes) if recomendacoes else "  Dados insuficientes para insights (minimo 5 amostras por faixa)")
    )

    telegram.send(msg, tipo_alerta="RELATORIO")
    logger.info("Relatorio enviado.")
    print(msg)


if __name__ == "__main__":
    main()
