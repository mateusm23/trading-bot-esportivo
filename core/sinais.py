"""
Detecta sinais em dados retornados pela API do Momentum Bet.
Rastreia estado anterior para identificar mudanças (gols, cartões).
"""

# Estado anterior por event_id
_placar_anterior  = {}   # event_id -> (gols_casa, gols_fora)
_cards_anteriores = {}   # event_id -> (amarelos_casa, amarelos_fora, vermelhos_casa, vermelhos_fora)
_alertas_enviados = set()


def verificar(event_id, status, dados):
    """
    Recebe:
      status = dict do /api/status[event_id]
      dados  = dict do /api/dados/<event_id>

    Retorna lista de strings com mensagens a enviar.
    """
    msgs      = []
    time_casa = status.get("time_casa", "Casa")
    time_fora = status.get("time_fora", "Fora")
    jogo      = f"<b>{time_casa} vs {time_fora}</b>"

    placar = dados.get("placar") or {}
    stats  = dados.get("stats") or {}

    # ── 1) Gol marcado ───────────────────────────────────────────────────────
    if placar:
        gc     = placar.get("gols_casa") or 0
        gf     = placar.get("gols_fora") or 0
        minuto = placar.get("minuto", "?")

        if event_id in _placar_anterior:
            ac, af = _placar_anterior[event_id]
            if gc > ac:
                msgs.append(
                    f"⚽ <b>GOL DO {time_casa.upper()}!</b>\n"
                    f"{jogo}\n"
                    f"Placar: <b>{gc} : {gf}</b>  |  {minuto}'"
                )
            elif gf > af:
                msgs.append(
                    f"⚽ <b>GOL DO {time_fora.upper()}!</b>\n"
                    f"{jogo}\n"
                    f"Placar: <b>{gc} : {gf}</b>  |  {minuto}'"
                )
        _placar_anterior[event_id] = (gc, gf)

    # ── 2) Cartão vermelho ───────────────────────────────────────────────────
    if stats:
        vc_h = int(stats.get(10, {}).get("home", 0) or 0)
        vc_a = int(stats.get(10, {}).get("away", 0) or 0)

        if event_id in _cards_anteriores:
            _, _, pvc_h, pvc_a = _cards_anteriores[event_id]
            if vc_h > pvc_h:
                msgs.append(f"🔴 <b>CARTÃO VERMELHO — {time_casa.upper()}</b>\n{jogo}")
            if vc_a > pvc_a:
                msgs.append(f"🔴 <b>CARTÃO VERMELHO — {time_fora.upper()}</b>\n{jogo}")

        am_h = int(stats.get(9, {}).get("home", 0) or 0)
        am_a = int(stats.get(9, {}).get("away", 0) or 0)
        _cards_anteriores[event_id] = (am_h, am_a, vc_h, vc_a)

    # ── 3) Pressão intensa — 3 barras consecutivas ───────────────────────────
    momentum = dados.get("momentum") or []
    if len(momentum) >= 3:
        ultimos    = momentum[-3:]
        valores    = [p["valor"] for p in ultimos]
        minuto_ref = ultimos[-1]["minuto"]
        chave      = f"{event_id}_press_{minuto_ref}"

        if chave not in _alertas_enviados:
            xg_h = float(stats.get(45, {}).get("home", 0) or 0) if stats else 0
            xg_a = float(stats.get(45, {}).get("away", 0) or 0) if stats else 0

            if all(v > 20 for v in valores):
                _alertas_enviados.add(chave)
                atk = stats.get(32, {}).get("home", "?") if stats else "?"
                msgs.append(
                    f"🔵 <b>PRESSÃO INTENSA — {time_casa.upper()}</b>\n"
                    f"{jogo}\n"
                    f"3 barras consecutivas acima de +20\n"
                    f"Pressão: <b>{valores[-1]:+}</b>  |  {minuto_ref}'\n"
                    f"xG Casa: {xg_h:.2f}  |  At. Perigosos: {atk}"
                )
            elif all(v < -20 for v in valores):
                _alertas_enviados.add(chave)
                atk = stats.get(32, {}).get("away", "?") if stats else "?"
                msgs.append(
                    f"🟠 <b>PRESSÃO INTENSA — {time_fora.upper()}</b>\n"
                    f"{jogo}\n"
                    f"3 barras consecutivas abaixo de -20\n"
                    f"Pressão: <b>{valores[-1]:+}</b>  |  {minuto_ref}'\n"
                    f"xG Visitante: {xg_a:.2f}  |  At. Perigosos: {atk}"
                )

    return msgs
