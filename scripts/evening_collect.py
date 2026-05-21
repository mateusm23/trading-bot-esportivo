"""
Job Noite — roda uma vez por dia as 22h BRT.

1. Le data/selecoes_hoje.json (gerado pelo job da manha)
2. Busca resultados reais via football-data.org / API-Football
3. Verifica se o favorito ganhou e calcula CLV
4. Adiciona linha ao data/historico.csv
5. Atualiza apostas PENDENTES no DB local (se trading.db existir)
6. Envia resumo do dia via Telegram
"""

import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

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
from core.result_fetcher import (
    buscar_resultado, favorito_ganhou, calcular_clv, get_league_codes
)
from core.scheduler import _load_leagues
from alerts.telegram_bot import TelegramBot

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SELECOES_PATH  = os.path.join(DATA_DIR, "selecoes_hoje.json")
HISTORICO_PATH = os.path.join(DATA_DIR, "historico.csv")

HISTORICO_COLUNAS = [
    "data", "event", "competition", "favorito",
    "odd_favorito", "score", "num_bookmakers",
    "resultado_real", "favorito_ganhou", "clv_percent",
]


def _sport_key_to_codes(sport_key: str) -> tuple[str, int]:
    for lg in _load_leagues():
        if lg["odds_api_key"] == sport_key:
            return lg.get("football_data_code", ""), lg.get("api_football_id", 0)
    return "", 0


def _garantir_cabecalho() -> None:
    if not os.path.exists(HISTORICO_PATH):
        with open(HISTORICO_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=HISTORICO_COLUNAS).writeheader()


def _append_historico(linhas: list[dict]) -> None:
    _garantir_cabecalho()
    with open(HISTORICO_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORICO_COLUNAS, extrasaction="ignore")
        writer.writerows(linhas)


def _montar_resumo(data: str, linhas: list[dict]) -> str:
    total = len(linhas)
    acertos = sum(1 for l in linhas if l["favorito_ganhou"] is True)
    erros   = sum(1 for l in linhas if l["favorito_ganhou"] is False)
    pendentes = sum(1 for l in linhas if l["favorito_ganhou"] is None)
    taxa = round(acertos / (acertos + erros) * 100, 1) if (acertos + erros) > 0 else 0

    clvs = [l["clv_percent"] for l in linhas if l.get("clv_percent") is not None]
    clv_str = f" | CLV medio: {round(sum(clvs)/len(clvs), 2):+.2f}%" if clvs else ""

    linhas_jogos = []
    for l in linhas:
        icone = "OK" if l["favorito_ganhou"] else ("X" if l["favorito_ganhou"] is False else "?")
        clv_tag = f" | CLV {l['clv_percent']:+.1f}%" if l.get("clv_percent") is not None else ""
        linhas_jogos.append(
            f"[{icone}] {l['event']} | Fav: {l['favorito']} @ {l['odd_favorito']}"
            f" | Score: {l['score']}{clv_tag}"
        )

    return (
        f"RESUMO DO DIA - {data}\n\n"
        + "\n".join(linhas_jogos)
        + f"\n\nSelecoes: {total} | Acertos: {acertos} | Erros: {erros} | Pendentes: {pendentes}"
        + (f"\nTaxa de acerto: {taxa}%" if (acertos + erros) > 0 else "")
        + clv_str
    )


def _atualizar_db_local(db: Database, hoje: str, resultados: dict[str, dict]) -> int:
    """
    Atualiza apostas PENDENTES no DB local com resultados + CLV.
    resultados: {market_id: {resultado, ganhou, clv_percent}}
    Retorna qtde de apostas atualizadas.
    """
    pendentes = [
        b for b in db.get_bets(data_jogo=hoje)
        if b.get("resultado") == "PENDENTE" and b.get("market_id")
    ]
    updated = 0
    for bet in pendentes:
        mid = bet.get("market_id", "")
        res = resultados.get(mid)
        if not res:
            continue
        ganhou = res.get("ganhou")
        if ganhou is True:
            db_resultado = "WIN"
        elif ganhou is False:
            db_resultado = "LOSS"
        else:
            continue  # ainda pendente ou resultado desconhecido
        db.atualizar_resultado_aposta(bet["id"], db_resultado, clv_percent=res.get("clv_percent"))
        updated += 1
    return updated


def main() -> None:
    logger.info("=== Job Noite iniciado ===")
    os.makedirs(DATA_DIR, exist_ok=True)

    db = Database()
    db.initialize()
    telegram = TelegramBot(db=db)

    if not os.path.exists(SELECOES_PATH):
        logger.warning("selecoes_hoje.json nao encontrado — nada a coletar")
        telegram.send("Job noturno: nenhuma selecao encontrada para hoje.", tipo_alerta="SISTEMA")
        return

    with open(SELECOES_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    data = payload.get("data", "")
    selecoes = payload.get("selecoes", [])
    logger.info(f"Coletando resultados de {len(selecoes)} selecoes de {data}")

    linhas: list[dict] = []
    resultados_por_mid: dict[str, dict] = {}

    for sel in selecoes:
        competition_code, api_football_id = _sport_key_to_codes(sel.get("sport_key", ""))
        resultado = buscar_resultado(
            sel.get("home_team", ""),
            sel.get("away_team", ""),
            competition_code,
            data,
            api_football_id,
        )
        ganhou = favorito_ganhou(sel.get("favorito", ""), sel.get("home_team", ""), resultado)
        clv = calcular_clv(sel.get("market_id", ""), sel.get("odd_favorito", 0))

        market_id = sel.get("market_id", "")
        if market_id:
            resultados_por_mid[market_id] = {"ganhou": ganhou, "clv_percent": clv}

        linha = {
            "data": data,
            "event": sel.get("event", ""),
            "competition": sel.get("competition", ""),
            "favorito": sel.get("favorito", ""),
            "odd_favorito": sel.get("odd_favorito", ""),
            "score": sel.get("score", ""),
            "num_bookmakers": sel.get("num_bookmakers", ""),
            "resultado_real": resultado.get("resultado", "UNKNOWN"),
            "favorito_ganhou": ganhou,
            "clv_percent": clv,
        }
        linhas.append(linha)
        logger.info(
            f"{sel.get('event')} | {resultado.get('resultado')} | "
            f"ganhou={ganhou} | CLV={clv}"
        )

    _append_historico(linhas)
    logger.info(f"historico.csv atualizado com {len(linhas)} linhas")

    # Atualiza DB local se existir
    try:
        n = _atualizar_db_local(db, data, resultados_por_mid)
        if n > 0:
            logger.info(f"DB local: {n} apostas atualizadas automaticamente")
    except Exception as e:
        logger.warning(f"Nao foi possivel atualizar DB local: {e}")

    msg = _montar_resumo(data, linhas)
    telegram.send(msg, tipo_alerta="RESUMO")

    logger.info("=== Job Noite concluido ===")


if __name__ == "__main__":
    main()
