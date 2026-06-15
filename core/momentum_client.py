"""
Cliente HTTP para a API do Momentum Bet (Flask local em localhost:8080).
O bot não importa nada do projeto Momentum Bet diretamente — tudo via HTTP.
"""

import os
import requests

API_URL = os.getenv("MOMENTUM_API_URL", "http://localhost:8080")
TIMEOUT = 10


def _get(path):
    r = requests.get(f"{API_URL}{path}", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(path, body=None):
    r = requests.post(f"{API_URL}{path}", json=body or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def buscar_grade():
    """Retorna lista de partidas disponíveis com status ao vivo e monitoramento."""
    return _get("/api/grade")


def buscar_status():
    """Retorna dict {event_id: status} de todos os jogos monitorados."""
    return _get("/api/status")


def buscar_dados(event_id):
    """Retorna momentum, odds, stats e placar de um jogo específico."""
    return _get(f"/api/dados/{event_id}")


def iniciar_jogo(event_id, market_id):
    """Inicia coleta de dados para um jogo."""
    return _post("/api/iniciar_direto", {"event_id": event_id, "market_id": market_id})


def parar_jogo(event_id=None):
    """Para coleta de um jogo específico ou de todos se event_id=None."""
    body = {"event_id": event_id} if event_id else {}
    return _post("/api/parar", body)


def api_online():
    """Verifica se o Momentum Bet está rodando."""
    try:
        requests.get(f"{API_URL}/api/status", timeout=3)
        return True
    except Exception:
        return False
