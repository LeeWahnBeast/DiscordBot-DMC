"""
Web server nhỏ (Flask) để Render Web Service coi bot là "còn sống".
"""

import os
import threading

from flask import Flask

PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)


@app.route("/")
def health_check():
    return "Bot dang chay!", 200


def _run():
    app.run(host="0.0.0.0", port=PORT)


def keep_alive():
    threading.Thread(target=_run, daemon=True).start()
