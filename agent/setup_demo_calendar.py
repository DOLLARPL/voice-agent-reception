"""Kalendarz demo warsztatu — osobny, żeby agent nie wpisywał wizyt testowych do kalendarza Jana.

Tworzy kalendarz "Auto Serwis Kowalski — demo", zapełnia go kilkoma przykładowymi wizytami
innych klientów (żeby terminy wyglądały realistycznie, a nie jak pusty kalendarz), wypisuje
identyfikator do wklejenia w `.env` jako `VOICE_CALENDAR_ID`.

    .venv/bin/python -m agent.setup_demo_calendar            # utwórz albo odśwież
    .venv/bin/python -m agent.setup_demo_calendar --usun     # usuń kalendarz demo
"""

from __future__ import annotations

import argparse
import datetime as dt

from . import calendar_client as cc

NAZWA = "Auto Serwis Kowalski — demo warsztatu"

# Wizyty innych klientów: pokazują, że kalendarz żyje. Bez klucza rezerwacji, więc są po prostu zajęte.
PRZYKLADOWE_WIZYTY = [
    (1, 9, 0, 90, "Nowak — rozrząd, Passat 2.0 TDI"),
    (1, 13, 0, 60, "Wiśniewska — diagnostyka, kontrolka silnika"),
    (2, 10, 30, 90, "Zieliński — zawieszenie tył, Octavia"),
    (3, 8, 0, 60, "Lewandowski — przegląd, wymiana oleju"),
    (4, 12, 0, 120, "Dąbrowska — sprzęgło, Golf VII"),
]


def znajdz_lub_utworz(service) -> str:
    for kal in service.calendarList().list().execute().get("items", []):
        if kal.get("summary") == NAZWA:
            return kal["id"]
    utworzony = service.calendars().insert(body={"summary": NAZWA, "timeZone": str(cc.TZ)}).execute()
    return utworzony["id"]


def uzupelnij(service, kalendarz: str, wyczysc: bool = True) -> int:
    teraz = dt.datetime.now(cc.TZ)
    if wyczysc:
        istniejace = service.events().list(
            calendarId=kalendarz, timeMin=teraz.isoformat(),
            timeMax=(teraz + dt.timedelta(days=30)).isoformat(), singleEvents=True, maxResults=250,
        ).execute().get("items", [])
        for ev in istniejace:
            service.events().delete(calendarId=kalendarz, eventId=ev["id"], sendUpdates="none").execute()

    dodane = 0
    for dni, godzina, minuta, dlugosc, opis in PRZYKLADOWE_WIZYTY:
        start = (teraz + dt.timedelta(days=dni)).replace(hour=godzina, minute=minuta, second=0, microsecond=0)
        if start < teraz:
            continue
        end = start + dt.timedelta(minutes=dlugosc)
        service.events().insert(calendarId=kalendarz, sendUpdates="none", body={
            "summary": opis,
            "description": "Przykładowa wizyta w kalendarzu demonstracyjnym.",
            "start": {"dateTime": start.isoformat(), "timeZone": str(cc.TZ)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(cc.TZ)},
        }).execute()
        dodane += 1
    return dodane


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usun", action="store_true", help="usuń kalendarz demo")
    args = parser.parse_args()

    service = cc._service()
    if args.usun:
        for kal in service.calendarList().list().execute().get("items", []):
            if kal.get("summary") == NAZWA:
                service.calendars().delete(calendarId=kal["id"]).execute()
                print(f"usunięty kalendarz: {kal['id']}")
        return

    kalendarz = znajdz_lub_utworz(service)
    ile = uzupelnij(service, kalendarz)
    print(f"VOICE_CALENDAR_ID={kalendarz}")
    print(f"przykładowych wizyt w kalendarzu demo: {ile}")


if __name__ == "__main__":
    main()
