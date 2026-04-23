import os
from threading import Thread

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Discord timer bot web service is running!"


def run():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def server_on():
    thread = Thread(target=run, daemon=True)
    thread.start()
