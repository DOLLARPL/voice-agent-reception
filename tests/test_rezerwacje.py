"""Testy logiki terminów i rezerwacji. Nie dotykają prawdziwego kalendarza — używają atrapy Google API.

Po co atrapa: idempotencja rezerwacji to jedyna rzecz w tym projekcie, której błąd oznacza
dwie wizyty w kalendarzu klienta. Da się ją sprawdzić bez sieci i bez zaśmiecania kalendarza.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import calendar_client as cc  # noqa: E402
from agent import tools  # noqa: E402


class AtrapaWydarzen:
    def __init__(self, rodzica):
        self.rodzic = rodzica

    def list(self, **kwargs):
        return self

    def insert(self, **kwargs):
        self.rodzic.wstawione.append(kwargs["body"])
        self.rodzic.utworzone = {"id": "ev-nowe", "htmlLink": "https://example.test/ev-nowe",
                                 "start": kwargs["body"]["start"]}
        return self

    def delete(self, **kwargs):
        return self

    def get(self, **kwargs):
        return self

    def execute(self):
        if self.rodzic.utworzone is not None:
            wynik, self.rodzic.utworzone = self.rodzic.utworzone, None
            return wynik
        return {"items": list(self.rodzic.zdarzenia)}


class AtrapaKalendarza:
    def __init__(self, zajete=None, zdarzenia=None):
        self.zajete = zajete or []
        self.zdarzenia = zdarzenia or []
        self.wstawione: list[dict] = []
        self.utworzone: dict | None = None

    def freebusy(self):
        return self

    def events(self):
        return AtrapaWydarzen(self)

    def query(self, body=None):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        return {"calendars": {"primary": {"busy": self.zajete}}}


def test_sloty_w_godzinach_pracy():
    poniedzialek = dt.datetime(2026, 9, 21, 7, 0, tzinfo=cc.TZ)  # poniedziałek, przed otwarciem
    sloty = cc.candidate_slots(poniedzialek)
    assert sloty, "brak jakichkolwiek slotów"
    for slot in sloty:
        dzien = slot.start.weekday()
        assert dzien in cc.BUSINESS_HOURS, f"slot w dniu bez pracy: {slot.start}"
        open_h, close_h = cc.BUSINESS_HOURS[dzien]
        assert slot.start.hour >= open_h
        assert slot.end.hour <= close_h or (slot.end.hour == close_h and slot.end.minute == 0)


def test_niedziela_nie_ma_slotow():
    niedziela = dt.datetime(2026, 9, 27, 9, 0, tzinfo=cc.TZ)
    dni = {s.start.weekday() for s in cc.candidate_slots(niedziela)}
    assert 6 not in dni


def test_sloty_co_pol_godziny():
    teraz = dt.datetime(2026, 9, 21, 7, 0, tzinfo=cc.TZ)
    jutro = [s for s in cc.candidate_slots(teraz) if s.start.date() == dt.date(2026, 9, 22)]
    assert jutro[0].start.strftime("%H:%M") == "08:00"
    assert jutro[1].start.strftime("%H:%M") == "08:30"
    assert len(jutro) == 17  # 8:00-17:00 co pół godziny: 8:00 ... 16:00


def test_nie_proponujemy_terminu_na_za_chwile():
    teraz = dt.datetime(2026, 9, 21, 10, 0, tzinfo=cc.TZ)
    sloty = cc.candidate_slots(teraz)
    najblizszy_dopuszczalny = teraz + dt.timedelta(hours=cc.MIN_LEAD_HOURS)
    assert all(s.start >= najblizszy_dopuszczalny for s in sloty)


def test_wolne_terminy_pomijaja_zajete():
    teraz = dt.datetime(2026, 9, 21, 7, 0, tzinfo=cc.TZ)
    # Zajęte okno w środku dnia następnego dnia: agent nie może go zaproponować.
    zajete = [{"start": "2026-09-22T09:00:00+02:00", "end": "2026-09-22T10:00:00+02:00"}]
    kalendarz = AtrapaKalendarza(zajete=zajete)
    sloty = cc.free_slots(limit=200, now=teraz, service=kalendarz)
    jutro = [s.iso for s in sloty if s.start.date() == dt.date(2026, 9, 22)]
    assert "2026-09-22T09:00:00+02:00" not in jutro
    assert jutro[0] == "2026-09-22T08:00:00+02:00"
    assert "2026-09-22T10:00:00+02:00" in jutro


def test_klucz_rezerwacji_jest_staly_i_rozroznia_dane():
    a = cc.booking_key("600123456", "2026-09-22T08:00:00+02:00")
    b = cc.booking_key("600123456", "2026-09-22T08:00:00+02:00")
    c = cc.booking_key("500100200", "2026-09-22T08:00:00+02:00")
    d = cc.booking_key("600123456", "2026-09-22T09:00:00+02:00")
    assert a == b and len(a) == 26
    assert len({a, c, d}) == 3


def test_drugie_wezwanie_narzedzia_nie_tworzy_drugiej_wizyty():
    teraz = dt.datetime.now(cc.TZ)
    slot = next(s for s in cc.candidate_slots(teraz) if s.start.hour >= 9)
    kalendarz = AtrapaKalendarza()

    pierwszy = cc.book(caller_phone="600123456", slot_iso=slot.iso, name="Marek Kowalczyk",
                       topic="klocki", service=kalendarz)
    # Zdarzenie trafia do atrapy tak, jak zrobiłby to Google: kolejny odczyt je widzi.
    kalendarz.zdarzenia.append({
        "id": "ev1", "htmlLink": "https://example.test/ev1",
        "start": {"dateTime": slot.iso},
        "extendedProperties": {"private": {"voiceBookingKey": pierwszy["key"]}},
    })
    drugi = cc.book(caller_phone="600123456", slot_iso=slot.iso, name="Marek Kowalczyk",
                    topic="klocki", service=kalendarz)

    assert pierwszy["created"] is True
    assert drugi["created"] is False, "drugie wywołanie utworzyło kolejną wizytę"
    assert drugi["event_id"] == "ev1"
    assert len(kalendarz.wstawione) == 1


def test_zapis_terminu_bez_nazwiska_lub_telefonu_jest_odrzucany():
    wynik = tools.zapisz_termin(termin="2026-09-22T08:00:00+02:00", imie_nazwisko="", telefon="", sprawa="klocki")
    assert '"ok": false' in wynik


def test_poza_godzinami_pracy_rezerwacja_jest_odrzucana():
    noc = dt.datetime(2026, 9, 22, 3, 0, tzinfo=cc.TZ)
    kalendarz = AtrapaKalendarza()
    try:
        cc.book(caller_phone="600123456", slot_iso=noc.isoformat(), name="Test Test", topic="klocki",
                service=kalendarz)
    except ValueError as exc:
        assert "godzinach pracy" in str(exc)
    else:
        raise AssertionError("rezerwacja o 3:00 nie została odrzucona")


def test_prompt_zawiera_informacje_o_ai_i_dzisiejsza_date():
    from agent.prompt import system_prompt

    tekst = system_prompt(dt.datetime(2026, 9, 21, 14, 0, tzinfo=cc.TZ))
    assert "asystent" in tekst.lower()
    assert "nagrywana" in tekst.lower()
    assert "poniedziałek, 21 września 2026" in tekst
