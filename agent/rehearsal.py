"""Próba rozmowy bez telefonu: ta sama persona, ten sam prompt i te same narzędzia co w pętli głosowej.

Po co: warstwa głosu (STT i TTS) wymaga kluczy i numeru, ale logika rozmowy — kwalifikacja leada,
potwierdzanie danych i zapis do kalendarza — jest od niej niezależna. Ten skrypt sprawdza ją
naprawdę: prawdziwy model przez OpenRouter, prawdziwe narzędzia, prawdziwy wpis w Kalendarzu Google.

Uruchomienie:
    .venv/bin/python -m agent.rehearsal              # rozmowa + zapis transkryptu
    .venv/bin/python -m agent.rehearsal --cleanup    # usuwa wizyty testowe z kalendarza
    .venv/bin/python -m agent.rehearsal --pokaz      # wypisuje transkrypt na ekran
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

from .config import LLM_MODEL, NAGRANIA_DIR, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, provider_status
from .prompt import system_prompt
from . import tools

TZ = ZoneInfo("Europe/Warsaw")

# Scenariusz rozmowy: klient z konkretną sprawą, z potknięciem i poprawką — jak w prawdziwym telefonie.
SCENARIUSZ = [
    "Dzień dobry, dzwonię w sprawie BMW, coś mi stuka z przodu przy hamowaniu.",
    "E90, rocznik dwa tysiące ósmy. Chodzi o przednie klocki i tarcze, klocki mam swoje, zamienniki Textara.",
    "Mam też numer oryginału, zaraz sprawdzę... 34 11 6 7 8 4 5 6 7, nie, przepraszam, końcówka 5 6 8.",
    "Może być jutro z rana, najlepiej jako pierwszy.",
    "Marek Kowalczyk, przez C. Telefon sześć sześć trzy, osiem zero dziewięć, dwa siedem cztery.",
    "Tak, zgadza się. Dziękuję, do widzenia.",
]


def _klient():
    if not OPENROUTER_API_KEY:
        raise SystemExit("Brak OPENROUTER_API_KEY — sprawdź .env projektu albo /opt/data/.env")
    from openai import OpenAI

    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)


def _pierwsze_tokeny(stream, t_start: float) -> tuple[str, float | None, float, list[dict]]:
    """Zbiera odpowiedź modelu i liczy czas do pierwszego tokenu treści oraz czas całej odpowiedzi.

    `t_start` musi być zmierzone PRZED wywołaniem `create()`: w tej wersji biblioteki OpenAI
    `create(stream=True)` wraca dopiero, gdy przyjdzie pierwszy fragment odpowiedzi, więc pomiar
    zaczęty po nim pokazuje 0,00 s i jest bezwartościowy.
    """
    ttft: float | None = None
    tekst = ""
    wywolania: dict[int, dict] = {}
    for chunk in stream:
        for wybor in chunk.choices:
            delta = wybor.delta
            if delta.content:
                if ttft is None:
                    ttft = time.time() - t_start
                tekst += delta.content
            for tc in delta.tool_calls or []:
                slot = wywolania.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
    return tekst, ttft, time.time() - t_start, [wywolania[k] for k in sorted(wywolania)]


def rozmowa(model: str = LLM_MODEL) -> dict:
    klient = _klient()
    wiadomosci: list[dict] = [{"role": "system", "content": system_prompt()}]
    schematy = tools.openai_tool_specs()
    przebieg: list[dict] = []
    czasy: list[float] = []
    czasy_calkowite: list[float] = []

    for wypowiedz in SCENARIUSZ:
        wiadomosci.append({"role": "user", "content": wypowiedz})
        przebieg.append({"kto": "klient", "tekst": wypowiedz})

        while True:
            t_start = time.time()
            strumien = klient.chat.completions.create(
                model=model, messages=wiadomosci, tools=schematy, stream=True,
                temperature=0.3, max_tokens=400,
            )
            tekst, ttft, czas_calkowity, wywolania = _pierwsze_tokeny(strumien, t_start)
            czasy_calkowite.append(czas_calkowity)
            if ttft is not None:
                czasy.append(ttft)

            if wywolania:
                wiadomosci.append({
                    "role": "assistant", "content": tekst or None,
                    "tool_calls": [{"id": w["id"], "type": "function",
                                    "function": {"name": w["name"], "arguments": w["arguments"]}} for w in wywolania],
                })
                for w in wywolania:
                    argumenty = json.loads(w["arguments"] or "{}")
                    wynik = tools.wykonaj(w["name"], argumenty)
                    przebieg.append({"kto": "narzedzie", "tekst": f"{w['name']}({json.dumps(argumenty, ensure_ascii=False)}) -> {wynik}"})
                    wiadomosci.append({"role": "tool", "tool_call_id": w["id"], "content": wynik})
                continue

            if tekst.strip():
                przebieg.append({"kto": "agent", "tekst": tekst.strip()})
                wiadomosci.append({"role": "assistant", "content": tekst.strip()})
            break

    return {"przebieg": przebieg, "model": model, "czasy_pierwszego_tokenu": czasy,
            "czasy_calkowite": czasy_calkowite}


def zapisz_transkrypt(wynik: dict) -> str:
    teraz = dt.datetime.now(TZ)
    sciezka = NAGRANIA_DIR / f"transkrypt-{teraz.strftime('%Y-%m-%d-%H%M')}.md"
    linie = [
        "# Transkrypt rozmowy testowej — agent recepcji",
        "",
        f"- Data: {teraz.strftime('%Y-%m-%d %H:%M')} (Europe/Warsaw)",
        f"- Model: `{wynik['model']}` przez OpenRouter",
        "- Warstwa głosu: **nieaktywna w tej próbie** (klient wpisany tekstem, brak STT/TTS)",
        "",
    ]
    czasy = wynik["czasy_pierwszego_tokenu"]
    wszystkie = wynik.get("czasy_calkowite") or []
    if czasy:
        linie += [f"- Czas do pierwszej wypowiedzianej frazy: średnio {sum(czasy)/len(czasy):.2f} s "
                  f"({len(czasy)} odpowiedzi, min {min(czasy):.2f} s, maks {max(czasy):.2f} s)"]
    if wszystkie:
        linie += [f"- Czas całej odpowiedzi modelu: średnio {sum(wszystkie)/len(wszystkie):.2f} s "
                  f"({len(wszystkie)} wywołań, maks {max(wszystkie):.2f} s)"]
    linie += ["", "## Rozmowa", ""]
    for krok in wynik["przebieg"]:
        if krok["kto"] == "klient":
            linie += [f"**Klient:** {krok['tekst']}", ""]
        elif krok["kto"] == "agent":
            linie += [f"**Agent:** {krok['tekst']}", ""]
        else:
            linie += [f"> narzędzie: `{krok['tekst']}`", ""]
    sciezka.write_text("\n".join(linie) + "\n", encoding="utf-8")
    return str(sciezka)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=LLM_MODEL)
    parser.add_argument("--pokaz", action="store_true", help="wypisz przebieg rozmowy")
    parser.add_argument("--cleanup", action="store_true", help="usuń wizyty testowe z kalendarza")
    args = parser.parse_args()

    if args.cleanup:
        usuniete = tools.posprzataj_stare_rezerwacje(godziny=72)
        print(f"usunięte wizyty testowe: {len(usuniete)} {usuniete}")
        return

    stan = provider_status()
    print(f"Warstwa głosu gotowa: {stan['gotowe_do_telefonu']} | {stan}")
    wynik = rozmowa(args.model)
    for krok in wynik["przebieg"]:
        if args.pokaz:
            print(f"[{krok['kto']}] {krok['tekst']}")
    sciezka = zapisz_transkrypt(wynik)
    print(f"transkrypt: {sciezka}")


if __name__ == "__main__":
    main()
