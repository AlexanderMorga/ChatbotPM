# webhook_app.py
import os
import asyncio
from flask import Flask, request
from telegram import Update

from bot import build_application  # tu función que arma la Application

# Construimos la Application una sola vez
tg_app = build_application()

# Creamos y fijamos un event loop global para este worker de gunicorn
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Inicializamos y arrancamos la Application (job queue, handlers, etc.)
loop.run_until_complete(tg_app.initialize())
loop.run_until_complete(tg_app.start())

app = Flask(__name__)

@app.get("/")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    # Convertimos el JSON de Telegram a Update
    update = Update.de_json(request.get_json(force=True), tg_app.bot)
    # Inyectamos el update en la cola interna; la Application lo procesará
    tg_app.update_queue.put_nowait(update)
    return "ok", 200
