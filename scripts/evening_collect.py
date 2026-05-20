"""
Job Noite — roda uma vez por dia as 22h BRT.

1. Le data/selecoes_hoje.json (gerado pelo job da manha)
2. Busca resultados reais via football-data.org (0 req Odds API)
3. Verifica se o favorito ganhou
4. Adiciona linha ao data/historico.csv
5. Envia resumo do dia via Telegram
"""

import csv
import json
import logging
import os
import sys
import requests
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
from core.form_checker import _name_match
from core.scheduler import _load_leagues
from alerts.telegram_bot import TelegramBot

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SELECOES_PATH = os.path.join(DATA_DIR, "selecoes_hoje.json")
HISTORICO_PATH = os.path.join(DATA_DIR, "historico.csv")
FD_BASE = "https://api.football-data.org/v4"

HISTORICO_COLUNAS = [
    "data", "event", "competition", "favorito",
    "odd_favorito", "score", "num_bookmakers",
    "resultado_real", "favorito_ganhou",
]


def _fd_headers() -> dict:
    return {"X-Auth-Token": os.getenv("FOOTBALL_DATA_KEY", "")}


def _sport_key_to_fd_code(sport_key: str) -> str:
    for lg in _load_leagues():
        if lg["odds_api_key"] == sport_key:
            return lg.get("football_data_code", "")
    return ""


def _buscar_resultado(home: str, away: str, competition_code: str) -> dict:
    """Busca resultado do jogo de hoje via football-data.org."""
    if not competition_code:
        return {"resultado": "UNKNOWN", "favorito_ganhou": None}

    hoje = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    url = f"{FD_BASE}/competitions/{competition_code}/matches"
    params = {"dateFrom": hoje, "dateTo": hoje}

    try:
        r = requests.get(url, headers=_fd_headers(), params=params, timeout=10)
        if r.status_code == 403:
            logger.warning(f"{competition_code} nao disponivel no plano gratuito")
            return {"resultado": "UNKNOWN", "favorito_ganhou": None}
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except requests.RequestException as e:
        logger.error(f"Erro ao buscar resultado: {e}")
        return {"resultado": "UNKNOWN", "favorito_ganhou": None}

    for m in matches:
        mhome = m["homeTeam"]["name"]
        maway = m["awayTeam"]["name"]
        if not (_name_match(home, mhome) and _name_match(away, maway)):
            continue

        status = m.get("status", "")
        if status not in ("FINISHED", "FT"):
            return {"resultado": "PENDENTE", "favorito_ganhou": None}

        score = m["score"]["fullTime"]
        hg = score.get("home") or 0
        ag = score.get("away") or 0

        if hg > ag:
            resultado = "HOME_WIN"
        elif ag > hg:
            resultado = "AWAY_WIN"
        else:
            resultado = "DRAW"

        return {"resultado": resultado, "home_goals": hg, "away_goals": ag}

    return {"resultado": "NAO_ENCONTRADO", "favorito_ganhou": None}


def _favorito_ganhou(selecao: dict, resultado: dict) -> bool | None:
    res = resultado.get("resultado", "UNKNOWN")
    if res in ("UNKNOWN", "PENDENTE", "NAO_ENCONTRADO"):
        return None

    favorito = selecao.get("favorito", "")
    home = selecao.get("home_team", "")
    away = selecao.get("away_team", "")

    fav_eh_home = _name_match(favorito, home)

    if res == "HOME_WIN":
        return fav_eh_home
    if res == "AWAY_WIN":
        return not fav_eh_home
    return False  # empate = favorito nao ganhou


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
    erros = sum(1 for l in linhas if l["favorito_ganhou"] is False)
    pendentes = sum(1 for l in linhas if l["favorito_ganhou"] is None)
    taxa = round(acertos / (acertos + erros) * 100, 1) if (acertos + erros) > 0 else 0

    linhas_jogos = []
    for l in linhas:
        icone = "OK" if l["favorito_ganhou"] else ("X" if l["favorito_ganhou"] is False else "?")
        linhas_jogos.append(
            f"[{icone}] {l['event']} | Fav: {l['favorito']} @ {l['odd_favorito']} "
            f"| Score: {l['score']} | {l['resultado_real']}"
        )

    return (
        f"RESUMO DO DIA - {data}\n\n"
        + "\n".join(linhas_jogos)
        + f"\n\nSelecoes: {total} | Acertos: {acertos} | Erros: {erros} | Pendentes: {pendentes}"
        + (f"\nTaxa de acerto: {taxa}%" if (acertos + erros) > 0 else "")
    )


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
    for sel in selecoes:
        competition_code = _sport_key_to_fd_code(sel.get("sport_key", ""))
        resultado = _buscar_resultado(
            sel.get("home_team", ""),
            sel.get("away_team", ""),
            competition_code,
        )
        ganhou = _favorito_ganhou(sel, resultado)

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
        }
        linhas.append(linha)
        logger.info(
            f"{sel.get('event')} | {resultado.get('resultado')} | "
            f"favorito_ganhou={ganhou}"
        )

    _append_historico(linhas)
    logger.info(f"historico.csv atualizado com {len(linhas)} linhas")

    msg = _montar_resumo(data, linhas)
    telegram.send(msg, tipo_alerta="RESUMO")

    logger.info("=== Job Noite concluido ===")


if __name__ == "__main__":
    main()
