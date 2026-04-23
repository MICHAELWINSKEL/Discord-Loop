import os
from threading import Thread

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Discord timer bot web service is running!"


def run_bot() -> None:
    from main import run_bot

    run_bot()


def start_bot_thread() -> None:
    thread = Thread(target=run_bot, daemon=True)
    thread.start()


if __name__ == "__main__":
    start_bot_thread()
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
