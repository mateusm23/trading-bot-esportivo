"""
Abre o portal web do Trading Bot em http://localhost:5000
Execute: python scripts/run_dashboard.py
"""

import os
import sys
import webbrowser
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from core.database import Database
from core.bankroll import Bankroll
from dashboard.app import app, init_dashboard

PORT = 5000


def main() -> None:
    db = Database()
    db.initialize()
    banca = float(os.getenv("BANCA_INICIAL", "1000"))
    bankroll = Bankroll(db=db, banca_inicial=banca)
    init_dashboard(db, bankroll)

    print(f"\nPortal aberto em: http://localhost:{PORT}")
    print("Pressione Ctrl+C para encerrar.\n")

    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
