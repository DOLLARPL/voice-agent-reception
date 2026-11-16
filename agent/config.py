"""Konfiguracja agenta: sekrety, model, persona warsztatu."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DANE_DIR = PROJECT_DIR / "dane"
NAGRANIA_DIR = PROJECT_DIR / "nagrania"
DANE_DIR.mkdir(exist_ok=True)
NAGRANIA_DIR.mkdir(exist_ok=True)


def load_env() -> None:
    """Czyta .env projektu, a brakujące klucze dobiera z .env Hermesa."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(PROJECT_DIR / ".env", override=False)
    load_dotenv("/opt/data/.env", override=False)


load_env()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# Model dobrany pod telefon: mały i szybki. Zmierzony czas odpowiedzi z narzędziem: 0,4-0,9 s.
LLM_MODEL = os.getenv("VOICE_LLM_MODEL", "google/gemini-3.1-flash-lite")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
N8N_LEAD_WEBHOOK = os.getenv("N8N_LEAD_WEBHOOK", "")

# Numer, spod którego dzwoni klient — po podłączeniu numeru Twilio trzeba go podmienić.
PORTFOLIO_NUMBER = os.getenv("VOICE_PORTFOLIO_NUMBER", "+48 XXX XXX XXX")

# Prywatny numer Jana: z niego agent zamiast recepcji czyta status dnia (etap 5).
OWNER_PHONE = os.getenv("VOICE_OWNER_PHONE", "")

PERSONA = {
    "firma": "Auto Serwis Kowalski",
    "miasto": "Warszawa",
    "miasto_w": "Warszawie",
    "adres": "ul. Wolska 112",
    "godziny": "od poniedziałku do piątku 8:00-17:00, w sobotę 8:00-13:00",
    "specjalizacja": "mechanika, hamulce, zawieszenie, rozrząd, elektryka, diagnostyka komputerowa",
}


def provider_status() -> dict[str, str | bool]:
    """Co jest gotowe do prawdziwego telefonu, a czego jeszcze brakuje."""
    return {
        "llm_openrouter": bool(OPENROUTER_API_KEY),
        "stt_deepgram": bool(DEEPGRAM_API_KEY),
        "tts_elevenlabs": bool(ELEVENLABS_API_KEY) and bool(ELEVENLABS_VOICE_ID),
        "telefonia_twilio": bool(TWILIO_ACCOUNT_SID) and bool(TWILIO_AUTH_TOKEN),
        "gotowe_do_telefonu": all([
            bool(OPENROUTER_API_KEY), bool(DEEPGRAM_API_KEY),
            bool(ELEVENLABS_API_KEY) and bool(ELEVENLABS_VOICE_ID),
            bool(TWILIO_ACCOUNT_SID) and bool(TWILIO_AUTH_TOKEN),
        ]),
    }


if __name__ == "__main__":
    import json

    print(json.dumps({**provider_status(), "model": LLM_MODEL}, indent=2, ensure_ascii=False))
