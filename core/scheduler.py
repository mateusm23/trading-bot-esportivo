import schedule
import time
import logging
import threading
import json
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

LEAGUES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "leagues.json")


def _load_leagues() -> list[dict]:
    with open(LEAGUES_PATH, encoding="utf-8") as f:
        return json.load(f)["leagues"]


def _sport_key_to_league(sport_key: str) -> str:
    for lg in _load_leagues():
        if lg["odds_api_key"] == sport_key:
            return lg["name"]
    return sport_key


def _formatar_horario(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        # Converte UTC para BRT (UTC-3)
        dt_brt = dt - timedelta(hours=3)
        return dt_brt.strftime("%H:%M")
    except Exception:
        return "--:--"


def _montar_grade(markets_todos: list[dict]) -> str:
    """Agrupa todos os mercados por liga e formata a mensagem da grade."""
    hoje = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%d/%m/%Y")

    # Agrupa por liga
    por_liga: dict[str, list[dict]] = {}
    for m in markets_todos:
        liga = m.get("competition", m.get("sport_key", "Outra"))
        por_liga.setdefault(liga, []).append(m)

    if not por_liga:
        return f"GRADE DO DIA - {hoje}\n\nNenhum jogo encontrado nas ligas monitoradas."

    linhas = [f"GRADE DO DIA - {hoje}\n"]
    total = 0
    for liga, jogos in sorted(por_liga.items()):
        jogos_ord = sorted(jogos, key=lambda j: j.get("start_time") or "")
        linhas.append(f"\n{liga.upper()}")
        for j in jogos_ord:
            horario = _formatar_horario(j.get("start_time", ""))
            linhas.append(f"  {horario}  {j.get('event', '')}")
            total += 1

    linhas.append(f"\nTotal: {total} jogos hoje")
    return "\n".join(linhas)


def _montar_grade_filtrados(markets_aprovados: list[dict]) -> str:
    """Formata mensagem com os jogos que passaram nos filtros."""
    hoje = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%d/%m/%Y")

    if not markets_aprovados:
        return f"JOGOS FILTRADOS - {hoje}\n\nNenhum jogo aprovado pelos criterios hoje."

    linhas = [f"JOGOS FILTRADOS - {hoje}\n"]
    for m in sorted(markets_aprovados, key=lambda j: j.get("score", 0), reverse=True):
        horario = _formatar_horario(m.get("start_time", ""))
        form = m.get("form", {})
        jogos_analisados = form.get("jogos_analisados", m.get("form_jogos_analisados", 0))
        if jogos_analisados and jogos_analisados > 0:
            vitorias = form.get("vitorias_recentes", m.get("form_vitorias", "?"))
            gols = form.get("media_gols_sofridos", m.get("form_media_gols_sofridos", "?"))
            linha_forma = f"  Forma: {vitorias}/5 vitorias | Media gols sofridos: {gols}"
        else:
            linha_forma = "  Forma: sem dados (liga fora do plano gratuito)"
        linhas.append(
            f"{horario}  {m.get('event', '')}\n"
            f"  Fav: {m.get('favorito', '')} @ {m.get('odd_favorito', '')}"
            f"  |  Score: {m.get('score', 0)}/100"
            f"  |  Casas: {m.get('num_bookmakers', '')}\n"
            + linha_forma
        )
    return "\n\n".join(linhas)


class DailyScheduler:
    def __init__(self, odds_client, filter_engine_fn, telegram) -> None:
        self._odds_client = odds_client
        self._analyze = filter_engine_fn
        self._telegram = telegram
        self._thread: threading.Thread = None
        self._running = False

    def start(self) -> None:
        schedule.every().day.at("08:00").do(self._enviar_grade_manha)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler iniciado — grade diaria agendada para 08:00 BRT")

    def stop(self) -> None:
        self._running = False
        schedule.clear()

    def _loop(self) -> None:
        while self._running:
            schedule.run_pending()
            time.sleep(30)

    def _enviar_grade_manha(self) -> None:
        logger.info("Executando grade matinal...")
        try:
            leagues = _load_leagues()
            sport_keys = [lg["odds_api_key"] for lg in leagues if lg.get("active")]

            # Busca TODOS os jogos do dia (sem filtro de odd)
            todos: list[dict] = []
            aprovados: list[dict] = []

            for sport_key in sport_keys:
                raw = self._odds_client._fetch_odds(sport_key)
                liga_nome = _sport_key_to_league(sport_key)
                for match in raw:
                    match["competition"] = liga_nome
                    match["start_time"] = match.get("commence_time", "")
                    match["event"] = f"{match.get('home_team', '')} vs {match.get('away_team', '')}"
                    todos.append(match)

                    # Aplica filtros para identificar aprovados
                    parsed = self._odds_client._parse_match(match)
                    if parsed:
                        result = self._analyze(parsed)
                        if result["status"] == "APROVADO":
                            parsed["score"] = result["score"]
                            aprovados.append(parsed)

            # Envia grade completa
            msg_grade = _montar_grade(todos)
            self._telegram.send(msg_grade, tipo_alerta="GRADE")

            # Envia filtrados (se houver)
            if aprovados:
                msg_filtrados = _montar_grade_filtrados(aprovados)
                self._telegram.send(msg_filtrados, tipo_alerta="FILTRADOS")
            else:
                self._telegram.send(
                    "Nenhum jogo aprovado pelos criterios hoje.",
                    tipo_alerta="FILTRADOS",
                )

            logger.info(f"Grade enviada: {len(todos)} jogos | {len(aprovados)} aprovados")
        except Exception as e:
            logger.error(f"Erro ao enviar grade matinal: {e}", exc_info=True)
