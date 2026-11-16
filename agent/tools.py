"""Narzędzia agenta: wolne terminy, zapis wizyty, przekazanie do człowieka.

Jedno źródło prawdy o narzędziach: `TOOL_SPECS` (opis i schemat parametrów).
Z tego samego miejsca korzysta i Pipecat (rozmowa głosowa), i próba tekstowa
(`agent/rehearsal.py`), więc nie da się ich rozjechać.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from . import calendar_client
from .leads import zapisz_lead

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "sprawdz_wolne_terminy": {
        "description": "Sprawdza najbliższe wolne terminy w warsztacie. Wywołaj, zanim cokolwiek zaproponujesz.",
        "properties": {
            "ile": {"type": "integer", "description": "Ile terminów zwrócić, domyślnie 3, maksymalnie 5."},
        },
        "required": [],
    },
    "zapisz_termin": {
        "description": "Zapisuje wizytę w kalendarzu warsztatu. Wywołaj dopiero, gdy masz termin, imię i nazwisko oraz telefon.",
        "properties": {
            "termin": {"type": "string", "description": "Termin w formacie ISO 8601, dokładnie taki, jaki zwróciło sprawdz_wolne_terminy."},
            "imie_nazwisko": {"type": "string", "description": "Imię i nazwisko klienta."},
            "telefon": {"type": "string", "description": "Numer telefonu klienta, cyframi."},
            "sprawa": {"type": "string", "description": "Krótko, czego dotyczy wizyta, np. 'wymiana klocków hamulcowych'."},
            "pojazd": {"type": "string", "description": "Marka, model i rocznik, np. 'BMW e90, 2008'. Puste, jeśli klient nie podał."},
        },
        "required": ["termin", "imie_nazwisko", "telefon", "sprawa"],
    },
    "przekaz_do_czlowieka": {
        "description": "Zapisuje sprawę do oddzwonienia przez mechanika. Użyj, gdy klient tego chce albo sprawa jest reklamacyjna.",
        "properties": {
            "powod": {"type": "string", "description": "Krótko, dlaczego człowiek ma oddzwonić."},
            "imie_nazwisko": {"type": "string", "description": "Imię i nazwisko, jeśli klient podał."},
            "telefon": {"type": "string", "description": "Numer telefonu, jeśli klient podał."},
        },
        "required": ["powod"],
    },
}


def _tekst(dane: Any) -> str:
    return json.dumps(dane, ensure_ascii=False)


def sprawdz_wolne_terminy(ile: int = 3) -> str:
    ile = max(1, min(int(ile or 3), 5))
    sloty = calendar_client.free_slots(limit=ile)
    if not sloty:
        return _tekst({"wolne_terminy": [], "komunikat": "Brak wolnych terminów w najbliższym tygodniu."})
    return _tekst({
        "wolne_terminy": [
            {"termin": s.iso, "opis": s.human(), "kiedy": s.relative() or "w tym tygodniu"} for s in sloty
        ],
        "podpowiedz": "Zaproponuj dwa lub trzy najbliższe, mówiąc opis słownie.",
    })


def zapisz_termin(termin: str, imie_nazwisko: str, telefon: str, sprawa: str, pojazd: str = "") -> str:
    if not imie_nazwisko.strip() or not telefon.strip():
        return _tekst({"ok": False, "blad": "Brak imienia i nazwiska albo telefonu. Dopytaj, zanim zapiszesz."})
    try:
        wynik = calendar_client.book(
            caller_phone=telefon.strip(),
            slot_iso=termin,
            name=imie_nazwisko.strip(),
            topic=sprawa.strip(),
            vehicle=(pojazd or "").strip(),
        )
    except ValueError as exc:
        return _tekst({"ok": False, "blad": str(exc), "wolne_terminy": json.loads(sprawdz_wolne_terminy())["wolne_terminy"]})
    zapisz_lead({
        "typ": "rezerwacja",
        "imie_nazwisko": imie_nazwisko.strip(),
        "telefon": telefon.strip(),
        "sprawa": sprawa.strip(),
        "pojazd": (pojazd or "").strip(),
        "termin": wynik["slot"],
        "klucz_rezerwacji": wynik["key"],
        "kalendarz_utworzony": wynik["created"],
        "kalendarz_link": wynik.get("link", ""),
    })
    return _tekst({"ok": True, "utworzono": wynik["created"], "termin": wynik["slot"], "klucz": wynik["key"]})


def przekaz_do_czlowieka(powod: str, imie_nazwisko: str = "", telefon: str = "") -> str:
    zapisz_lead({
        "typ": "oddzwonic",
        "powod": powod.strip(),
        "imie_nazwisko": imie_nazwisko.strip(),
        "telefon": telefon.strip(),
    })
    return _tekst({"ok": True, "komunikat": "Sprawa zapisana, mechanik oddzwoni w godzinach pracy warsztatu."})


HANDLERS = {
    "sprawdz_wolne_terminy": sprawdz_wolne_terminy,
    "zapisz_termin": zapisz_termin,
    "przekaz_do_czlowieka": przekaz_do_czlowieka,
}


def openai_tool_specs() -> list[dict]:
    """Schematy narzędzi w formacie OpenAI/OpenRouter (dla próby tekstowej)."""
    return [
        {"type": "function", "function": {"name": nazwa, "description": spec["description"],
                                          "parameters": {"type": "object", "properties": spec["properties"],
                                                         "required": spec["required"]}}}
        for nazwa, spec in TOOL_SPECS.items()
    ]


def pipecat_tools():
    """Te same narzędzia jako schemat Pipecatu, z podpiętymi obsługami."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    schematy = []
    for nazwa, spec in TOOL_SPECS.items():
        synchroniczna = HANDLERS[nazwa]

        async def handler(params, _fn=synchroniczna):
            args = dict(params.arguments or {})
            wynik = _fn(**args)
            await params.result_callback(wynik)

        schematy.append(FunctionSchema(name=nazwa, description=spec["description"],
                                       properties=spec["properties"], required=spec["required"],
                                       handler=handler))
    return ToolsSchema(standard_tools=schematy)


def wykonaj(nazwa: str, argumenty: dict) -> str:
    """Wywołanie narzędzia po nazwie — używane przez próbę tekstową i testy."""
    if nazwa not in HANDLERS:
        return _tekst({"ok": False, "blad": f"Nieznane narzędzie: {nazwa}"})
    try:
        return HANDLERS[nazwa](**argumenty)
    except TypeError as exc:
        return _tekst({"ok": False, "blad": f"Złe argumenty: {exc}"})


def posprzataj_stare_rezerwacje(godziny: int = 48) -> list[str]:
    """Usuwa zdarzenia utworzone przez agenta (te z kluczem rezerwacji) z ostatnich godzin.

    Używane po próbach, żeby kalendarz Jana nie zapełniał się wizytami testowymi.
    """
    granica = dt.datetime.now(calendar_client.TZ) - dt.timedelta(hours=godziny)
    service = calendar_client._service()
    wynik = service.events().list(
        calendarId=calendar_client.DEFAULT_CALENDAR, timeMin=granica.isoformat(),
        singleEvents=True, maxResults=100,
    ).execute()
    usuniete = []
    for ev in wynik.get("items", []):
        if ev.get("extendedProperties", {}).get("private", {}).get("voiceBookingKey"):
            service.events().delete(calendarId=calendar_client.DEFAULT_CALENDAR, eventId=ev["id"], sendUpdates="none").execute()
            usuniete.append(ev["id"])
    return usuniete


if __name__ == "__main__":
    print(sprawdz_wolne_terminy())
