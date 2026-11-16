"""Serwer agenta: webhook TwiML dla Twilio i WebSocket z mediami rozmowy.

Uruchomienie lokalne:
    .venv/bin/python -m agent.server
Na VPS proces ten stoi za Caddy, które robi TLS (Twilio wymaga `wss://`, nie `ws://`).

Ścieżki:
- `POST /twiml`  — Twilio woła to przy odbieraniu połączenia; w odpowiedzi dostaje polecenie
                   połączenia audio z naszym WebSocketem.
- `WS /ws`       — media rozmowy (8 kHz µ-law), tu wchodzi Pipecat.
- `GET /health`  — co jest skonfigurowane, a czego brakuje.
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response

from .bot import run_bot
from .config import PORTFOLIO_NUMBER, TWILIO_ACCOUNT_SID, provider_status
from .prompt import system_prompt

app = FastAPI(title="Voice agent — recepcja warsztatu")

# Publiczny adres WebSocketu (Caddy z TLS). Bez tego Twilio nie ma się z czym połączyć.
PUBLIC_WSS_URL = os.getenv("PUBLIC_WSS_URL", "")


@app.get("/health")
async def health() -> dict:
    stan = provider_status()
    stan["public_wss_url"] = PUBLIC_WSS_URL or None
    return stan


@app.get("/prompt", response_class=PlainTextResponse)
async def prompt_podglad() -> str:
    """Podgląd promptu, którym mówi agent — do sprawdzenia na produkcji bez czytania kodu."""
    return system_prompt()


@app.post("/twiml")
async def twiml(request: Request) -> Response:
    """TwiML: połącz dźwięk rozmowy z naszym WebSocketem i przekaż numer dzwoniącego.

    Ciało czytam i rozbieram ręcznie (`parse_qs`), a nie przez `request.form()`: Twilio wysyła
    zwykły `application/x-www-form-urlencoded`, a `request.form()` wymagałoby dodatkowej paczki
    `python-multipart` — jedna zależność mniej na VPS.
    """
    from urllib.parse import parse_qs

    pola = parse_qs((await request.body()).decode("utf-8", "ignore"))
    dzwoniacy = (pola.get("From") or [""])[0]
    numer = (pola.get("To") or [PORTFOLIO_NUMBER])[0]
    if not PUBLIC_WSS_URL:
        # Bez adresu WSS Twilio dostałoby nienaprawialny TwiML. Lepiej powiedzieć to wprost.
        return PlainTextResponse("Brak PUBLIC_WSS_URL w konfiguracji", status_code=503)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f'    <Stream url="{PUBLIC_WSS_URL}">\n'
        f'      <Parameter name="from" value="{dzwoniacy}"/>\n'
        f'      <Parameter name="to" value="{numer}"/>\n'
        "    </Stream>\n"
        "  </Connect>\n"
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")


@app.websocket("/ws")
async def websocket_media(websocket: WebSocket) -> None:
    """Odbiera Media Stream z Twilio, uruchamia pętlę głosową, zapisuje nagranie i transkrypt."""
    await websocket.accept()
    stream_sid = ""
    call_sid = ""
    dzwoniacy = ""

    try:
        while True:
            dane = json.loads(await websocket.receive_text())
            zdarzenie = dane.get("event")
            if zdarzenie == "start":
                start = dane.get("start", {})
                stream_sid = start.get("streamSid") or dane.get("streamSid", "")
                call_sid = start.get("callSid", "")
                dzwoniacy = (start.get("customParameters") or {}).get("from", "")
                break
            if zdarzenie == "stop":
                await websocket.close()
                return
    except WebSocketDisconnect:
        return

    if not stream_sid or not TWILIO_ACCOUNT_SID:
        await websocket.close(code=1011)
        return

    try:
        await run_bot(websocket, stream_sid, call_sid, dzwoniacy)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("VOICE_HOST", "127.0.0.1"), port=int(os.getenv("VOICE_PORT", "8099")))
