# webhook_app.py
import os
from flask import Flask, request
from telegram import Update
from bot import build_application  # importamos la función que construye la app PTB

# Construimos la Application una sola vez
tg_app = build_application()

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return "ok", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), tg_app.bot)
    # Procesa sincrónicamente; PTB v20 maneja internamente la cola/async
    tg_app.process_update(update)
    return "ok", 200
