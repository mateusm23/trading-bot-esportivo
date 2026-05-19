from datetime import datetime


def entrada(market: dict, analysis: dict, stake: float) -> str:
    form = analysis.get("form", {})
    vitorias = form.get("vitorias_recentes", 0)
    jogos = form.get("jogos_analisados", 5)
    gols = form.get("media_gols_sofridos", 0.0)
    forma_str = f"{vitorias}/{jogos} vitorias | {gols} gols sofridos/jogo"

    start = market.get("start_time", "")
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        horario = dt.strftime("%d/%m %H:%M UTC")
    except Exception:
        horario = start

    return (
        f"OPORTUNIDADE\n"
        f"Liga   : {market.get('competition', '')}\n"
        f"Jogo   : {market.get('event', '')}\n"
        f"Horario: {horario}\n"
        f"Fav    : {market.get('favorito', '')} @ {market.get('odd_favorito', '')}\n"
        f"Empate : {market.get('odd_empate', '-')} | "
        f"Zebra: {market.get('odd_zebra', '-')}\n"
        f"Casas  : {market.get('num_bookmakers', '')} bookmakers\n"
        f"Score  : {analysis.get('score', 0)}/100\n"
        f"Forma  : {forma_str}\n"
        f"Stake  : R${stake:.2f}\n"
        f"Acao   : ENTRAR AGORA na Bolsa de Aposta"
    )


def saida_lucro(market: dict, odd_entrada: float, motivo: str) -> str:
    return (
        f"SAIDA COM LUCRO\n"
        f"Jogo   : {market.get('event', '')}\n"
        f"Motivo : {motivo}\n"
        f"Odd entrada: {odd_entrada}\n"
        f"Acao   : SAIR agora na melhor odd disponivel"
    )


def stop_loss(market: dict, odd_entrada: float, motivo: str) -> str:
    return (
        f"STOP LOSS - URGENTE\n"
        f"Jogo   : {market.get('event', '')}\n"
        f"Motivo : {motivo}\n"
        f"Odd entrada: {odd_entrada}\n"
        f"Acao   : SAIR IMEDIATAMENTE - aceite qualquer odd"
    )


def revisao_manual(trade: dict) -> str:
    return (
        f"REVISAO MANUAL\n"
        f"Jogo   : {trade.get('jogo', '')}\n"
        f"Odd entrada: {trade.get('odd_entrada', '')}\n"
        f"Motivo : Jogo no 2o tempo sem saida registrada\n"
        f"Acao   : Verifique a posicao manualmente"
    )
