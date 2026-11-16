"""Kalendarz Google dla agenta: wolne terminy warsztatu i rezerwacja.

Zasady, które ten moduł egzekwuje:
- godziny pracy warsztatu: pon-pt 8:00-17:00, sob 8:00-13:00 (Europe/Warsaw),
- slot ma 60 minut, ostatni slot kończy się najpóźniej o godzinie zamknięcia,
- nie proponujemy terminu bliższego niż MIN_LEAD_HOURS od teraz,
- rezerwacja jest idempotentna: ten sam rozmówca + ten sam slot = jedno zdarzenie,
  nawet jeśli agent wywoła narzędzie dwa razy w jednej rozmowie.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Warsaw")

# pon=0 ... sob=5; (godzina otwarcia, godzina zamknięcia)
BUSINESS_HOURS: dict[int, tuple[int, int]] = {0: (8, 17), 1: (8, 17), 2: (8, 17), 3: (8, 17), 4: (8, 17), 5: (8, 13)}
SLOT_MINUTES = 60
# Slot startuje co pół godziny. Przy siatce co godzinę 45-minutowa przerwa w kalendarzu
# jest nie do wykorzystania i agent proponuje terminy późniejsze, niż naprawdę są wolne.
SLOT_STEP_MINUTES = 30
MIN_LEAD_HOURS = 2
HORIZON_DAYS = 7
DEFAULT_CALENDAR = os.getenv("VOICE_CALENDAR_ID", "primary")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "/opt/data/google_token.json")

SCOPES = ["https://www.googleapis.com/auth/calendar"]


@dataclass(frozen=True)
class Slot:
    start: dt.datetime
    end: dt.datetime

    @property
    def iso(self) -> str:
        return self.start.isoformat()

    def human(self) -> str:
        dni = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
        return f"{dni[self.start.weekday()]} {self.start.strftime('%d.%m')} o {self.start.strftime('%H:%M')}"

    def relative(self, now: dt.datetime | None = None) -> str:
        """Etykieta 'jutro' / 'pojutrze'. Bez niej model gubi się, gdy klient mówi 'jutro z rana',
        a narzędzie zwraca datę bezwzględną — i odmawia terminu, który właśnie proponuje."""
        now = (now or dt.datetime.now(TZ)).astimezone(TZ)
        roznica = (self.start.date() - now.date()).days
        return {0: "dzisiaj", 1: "jutro", 2: "pojutrze"}.get(roznica, "")


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def candidate_slots(now: dt.datetime | None = None) -> list[Slot]:
    """Wszystkie sloty w godzinach pracy w horyzoncie HORIZON_DAYS, bez sprawdzania zajętości."""
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    earliest = now + dt.timedelta(hours=MIN_LEAD_HOURS)
    out: list[Slot] = []
    for day_offset in range(HORIZON_DAYS + 1):
        day = (now + dt.timedelta(days=day_offset)).date()
        hours = BUSINESS_HOURS.get(day.weekday())
        if not hours:
            continue
        open_h, close_h = hours
        cursor = dt.datetime.combine(day, dt.time(open_h, 0), tzinfo=TZ)
        while cursor + dt.timedelta(minutes=SLOT_MINUTES) <= dt.datetime.combine(day, dt.time(close_h, 0), tzinfo=TZ):
            if cursor >= earliest:
                out.append(Slot(cursor, cursor + dt.timedelta(minutes=SLOT_MINUTES)))
            cursor += dt.timedelta(minutes=SLOT_STEP_MINUTES)
    return out


def busy_ranges(service, start: dt.datetime, end: dt.datetime, calendar: str = DEFAULT_CALENDAR) -> list[tuple[dt.datetime, dt.datetime]]:
    body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": calendar}]}
    result = service.freebusy().query(body=body).execute()
    busy = result["calendars"].get(calendar, {}).get("busy", [])
    return [(_parse(b["start"]), _parse(b["end"])) for b in busy]


def _parse(value: str) -> dt.datetime:
    # Google zwraca 'Z' dla UTC albo offset dla strefy kalendarza.
    value = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def free_slots(limit: int = 5, now: dt.datetime | None = None, service=None) -> list[Slot]:
    """Najbliższe wolne terminy, posortowane, bez kolizji z kalendarzem."""
    slots = candidate_slots(now)
    if not slots:
        return []
    service = service or _service()
    busy = busy_ranges(service, slots[0].start, slots[-1].end)
    free = [s for s in slots if not any(s.start < b_end and b_start < s.end for b_start, b_end in busy)]
    return free[:limit]


def booking_key(caller_phone: str, slot_iso: str) -> str:
    raw = f"{caller_phone.strip()}|{slot_iso}"
    return "va" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def find_booking(service, key: str, slot_start: dt.datetime, calendar: str = DEFAULT_CALENDAR) -> dict | None:
    """Szuka zdarzenia po kluczu rezerwacji w oknie ±1 dzień wokół terminu.

    Filtruję w Pythonie, a nie zapytaniem `privateExtendedProperty`: składnia tego zapytania
    w Google Calendar API jest kapryśna, a pomyłka w niej cicho psuje idempotencję
    (drugie wywołanie narzędzia tworzyłoby drugą wizytę).
    """
    okno_start = slot_start - dt.timedelta(days=1)
    okno_koniec = slot_start + dt.timedelta(days=1)
    result = (
        service.events()
        .list(calendarId=calendar, timeMin=okno_start.isoformat(), timeMax=okno_koniec.isoformat(),
              singleEvents=True, maxResults=50)
        .execute()
    )
    for ev in result.get("items", []):
        if ev.get("extendedProperties", {}).get("private", {}).get("voiceBookingKey") == key:
            return ev
    return None


def book(
    caller_phone: str,
    slot_iso: str,
    name: str,
    topic: str,
    vehicle: str = "",
    service=None,
    calendar: str = DEFAULT_CALENDAR,
) -> dict:
    """Rezerwuje termin. Zwraca {'created': bool, 'key': str, 'event': {...}, 'link': str}."""
    service = service or _service()
    start = dt.datetime.fromisoformat(slot_iso).astimezone(TZ)
    end = start + dt.timedelta(minutes=SLOT_MINUTES)
    key = booking_key(caller_phone, start.isoformat())

    existing = find_booking(service, key, start, calendar)
    if existing:
        return {"created": False, "key": key, "event_id": existing["id"], "link": existing.get("htmlLink", ""),
                "slot": start.isoformat(), "reason": "termin był już zapisany w tej rozmowie"}

    zajete = [s for s in candidate_slots() if s.start == start]
    if not zajete:
        raise ValueError(f"Termin {start.isoformat()} nie jest w godzinach pracy warsztatu")
    busy = busy_ranges(service, start, end, calendar)
    if busy:
        raise ValueError(f"Termin {start.isoformat()} jest już zajęty w kalendarzu")

    opis = [f"Rezerwacja od asystenta głosowego.", f"Klient: {name}", f"Telefon: {caller_phone}"]
    if vehicle:
        opis.append(f"Pojazd: {vehicle}")
    opis.append(f"Sprawa: {topic}")
    body = {
        "summary": f"{name} — {topic}" + (f" ({vehicle})" if vehicle else ""),
        "description": "\n".join(opis),
        "start": {"dateTime": start.isoformat(), "timeZone": str(TZ)},
        "end": {"dateTime": end.isoformat(), "timeZone": str(TZ)},
        "extendedProperties": {"private": {"voiceBookingKey": key, "callerPhone": caller_phone}},
    }
    created = service.events().insert(calendarId=calendar, body=body, sendUpdates="none").execute()
    return {"created": True, "key": key, "event_id": created["id"], "link": created.get("htmlLink", ""),
            "slot": start.isoformat(), "human": zajete[0].human()}


def cancel(event_id: str, service=None, calendar: str = DEFAULT_CALENDAR) -> None:
    service = service or _service()
    service.events().delete(calendarId=calendar, eventId=event_id, sendUpdates="none").execute()


if __name__ == "__main__":  # szybki podgląd: python -m agent.calendar_client
    print(json.dumps([f"{s.human()}  {s.iso}" for s in free_slots(limit=8)], ensure_ascii=False, indent=2))
