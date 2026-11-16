"""Testy budowy pętli głosowej — bez łączenia się z Twilio.

Po co: warstwa głosu wymaga kluczy Deepgram, ElevenLabs i numeru Twilio, których jeszcze nie ma.
Ten test sprawdza, że sama konstrukcja usług, schematu narzędzi i pipeline'u jest poprawna,
więc gdy klucze dojadą, zostaje przejść do testu z prawdziwym telefonem, a nie szukać literówek
w nazwach pól.

Uwaga: testy ustawiają atrapowe klucze, żeby konstruktory nie wołały `SystemExit`. Nie łączą się z API.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DEEPGRAM_API_KEY", "test")
os.environ.setdefault("ELEVENLABS_API_KEY", "test")
os.environ.setdefault("ELEVENLABS_VOICE_ID", "test")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_uslugi_i_narzedzia_buduja_sie_poprawnie():
    from agent.bot import zbuduj_uslugi
    from agent.tools import TOOL_SPECS

    stt, tts, llm, narzedzia = zbuduj_uslugi()

    assert stt is not None and tts is not None and llm is not None
    nazwy = {t.name if hasattr(t, "name") else t for t in narzedzia.standard_tools}
    assert nazwy == set(TOOL_SPECS), f"schemat narzędzi nie zgadza się z TOOL_SPECS: {nazwy}"
    for nazwa in TOOL_SPECS:
        assert llm.has_function(nazwa), f"brak podpiętej obsługi narzędzia {nazwa}"


def test_obsluga_narzedzia_zwraca_tekst_wyniku():
    import asyncio

    from agent.bot import _handler
    from agent import tools

    zebrane: list[str] = []

    class Parametry:
        arguments = {"ile": 1}

        async def result_callback(self, wynik):
            zebrane.append(wynik)

    asyncio.run(_handler("sprawdz_wolne_terminy")(Parametry()))
    assert zebrane and "wolne_terminy" in zebrane[0]
    assert tools.TOOL_SPECS["sprawdz_wolne_terminy"]["required"] == []


def test_serwer_odpowiada_twiml_i_zdrowiem(monkeypatch=None):
    """TwiML musi zawierać adres WSS i numer dzwoniącego, inaczej Twilio nie połączy audio."""
    from fastapi.testclient import TestClient

    import agent.server as serwer

    serwer.PUBLIC_WSS_URL = "wss://voice.example.test/ws"
    klient = TestClient(serwer.app)

    odpowiedz = klient.post("/twiml", data={"From": "+48600100200", "To": "+48221234567"})
    assert odpowiedz.status_code == 200
    assert "<Connect>" in odpowiedz.text and 'url="wss://voice.example.test/ws"' in odpowiedz.text
    assert '+48600100200' in odpowiedz.text

    zdrowie = klient.get("/health").json()
    assert set(zdrowie) >= {"llm_openrouter", "stt_deepgram", "tts_elevenlabs", "telefonia_twilio"}


def test_bez_adresu_wss_zwracamy_blad_zamiast_cichego_twiml():
    from fastapi.testclient import TestClient

    import agent.server as serwer

    serwer.PUBLIC_WSS_URL = ""
    klient = TestClient(serwer.app)
    odpowiedz = klient.post("/twiml", data={"From": "+48600100200"})
    assert odpowiedz.status_code == 503
