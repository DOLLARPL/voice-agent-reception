"""Sprawdzenie warstwy mowy: klucze Deepgram i ElevenLabs + próba w obie strony.

Po co osobny skrypt: to jedyny sposób, żeby odróżnić „zmienna ustawiona” od „usługa działa”.
Klucz wklejony prawie dobrze (np. ID klucza zamiast klucza) przechodzi jako niepusty string,
a wywala się dopiero w środku rozmowy z klientem.

Robię próbę okrężną: ElevenLabs syntezuje polskie zdanie → Deepgram je transkrybuje →
porównuję słowa. To sprawdza jednocześnie oba klucze, poprawność formatu audio i to,
czy głos faktycznie mówi po polsku, a nie czyta polskiego tekstu z angielskim akcentem.

    .venv/bin/python -m agent.check_mowa
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import DEEPGRAM_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_MODEL, ELEVENLABS_VOICE_ID, NAGRANIA_DIR

# Zdanie celowo z tym, na czym STT najczęściej się przewraca: nazwisko, numer części, cyfry.
ZDANIE = ("Dzień dobry, dzwonię w sprawie wymiany klocków hamulcowych w BMW e90, "
          "numer części trzy cztery jeden jeden sześć siedem osiem cztery pięć sześć osiem. "
          "Nazywam się Marek Kowalczyk, proszę o termin w czwartek.")

# Głosy premade (wbudowane) — jedyne, które darmowy plan ElevenLabs wpuszcza przez API.
# Głosy z Voice Library (jak ELEVENLABS_VOICE_ID w .env) zwracają 402 paid_plan_required.
KANDYDACI_GLOSOW = {
    "Sarah (en, mature/confident)": "EXAVITQu4vr4xnSDxMaL",
    "George (en, british, storyteller)": "JBFqnCBsd6RMkjVDRZzb",
    "Daniel (en, british, broadcaster)": "onwK4e9ZLuTAKqWW03F9",
    "Bill (en, mature/balanced)": "pqHfZKP75CvOlQylNhV4",
}


def _zapytanie(url: str, *, dane: bytes | None = None, naglowki: dict | None = None, metoda: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=dane, headers=naglowki or {}, method=metoda)
    try:
        with urllib.request.urlopen(req, timeout=60) as odp:
            return odp.status, odp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def sprawdz_deepgram() -> dict:
    if not DEEPGRAM_API_KEY:
        return {"ok": False, "blad": "brak klucza"}
    kod, body = _zapytanie("https://api.deepgram.com/v1/projects", naglowki={"Authorization": f"Token {DEEPGRAM_API_KEY}"})
    if kod != 200:
        return {"ok": False, "http": kod, "odpowiedz": body.decode("utf-8", "ignore")[:200]}
    projekty = json.loads(body).get("projects", [])
    return {"ok": True, "http": kod, "projekt": projekty[0]["name"] if projekty else "?"}


def sprawdz_elevenlabs() -> dict:
    """Sprawdza klucz ElevenLabs.

    Uwaga: `/user/subscription` wymaga uprawnienia `user_read`, którego klucz może nie mieć
    (klucze tworzy się z wybranymi zakresami). Brak limitów traktuję jako informację, nie błąd —
    do rozmowy wystarczy uprawnienie do syntezy mowy i odczytu głosów. Klucz bez `user_read`
    jest nawet bezpieczniejszy, bo nie da się nim nic zmienić na koncie.
    """
    if not ELEVENLABS_API_KEY:
        return {"ok": False, "blad": "brak klucza"}
    if not ELEVENLABS_API_KEY.startswith("sk_"):
        return {"ok": False, "blad": "klucz nie zaczyna się od 'sk_' — to prawdopodobnie ID klucza, nie klucz"}
    naglowki = {"xi-api-key": ELEVENLABS_API_KEY}

    wynik: dict = {}
    kod, body = _zapytanie("https://api.elevenlabs.io/v1/user/subscription", naglowki=naglowki)
    if kod == 200:
        konto = json.loads(body)
        wynik.update({"plan": konto.get("tier"), "znaki_uzyte": konto.get("character_count"),
                      "znaki_limit": konto.get("character_limit")})
    else:
        wynik["limity"] = f"nie odczytane (HTTP {kod}) — klucz bez uprawnienia user_read"

    kod_glosu, body_glosu = _zapytanie(f"https://api.elevenlabs.io/v1/voices/{ELEVENLABS_VOICE_ID}", naglowki=naglowki)
    if kod_glosu == 200:
        glos = json.loads(body_glosu)
        etykiety = glos.get("labels", {}) or {}
        wynik.update({"ok": True, "glos": glos.get("name"), "glos_plec": etykiety.get("gender"),
                      "glos_jezyk": etykiety.get("language"), "glos_opis": etykiety.get("description"),
                      "glos_akcent": etykiety.get("accent")})
    else:
        wynik.update({"ok": False, "http_glos": kod_glosu,
                      "odpowiedz_glos": body_glosu.decode("utf-8", "ignore")[:200]})
    wynik["model"] = ELEVENLABS_MODEL
    return wynik


def syntezuj(tekst: str, format_dzwieku: str = "mp3_44100_128", voice_id: str | None = None) -> bytes | None:
    voice_id = voice_id or ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={format_dzwieku}"
    dane = json.dumps({"text": tekst, "model_id": ELEVENLABS_MODEL}).encode()
    kod, body = _zapytanie(url, dane=dane, metoda="POST",
                           naglowki={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"})
    if kod != 200:
        print(f"  synteza nie udała się: HTTP {kod} {body.decode('utf-8', 'ignore')[:200]}")
        return None
    return body


def transkrybuj(audio: bytes, typ: str = "audio/mpeg") -> str:
    url = "https://api.deepgram.com/v1/listen?model=nova-3&language=pl&smart_format=true&punctuate=true"
    kod, body = _zapytanie(url, dane=audio, metoda="POST",
                           naglowki={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": typ})
    if kod != 200:
        return f"[błąd STT HTTP {kod}] {body.decode('utf-8', 'ignore')[:200]}"
    wynik = json.loads(body)["results"]["channels"][0]["alternatives"][0]
    return wynik.get("transcript", "")


def _normalizuj(tekst: str) -> list[str]:
    return [s.strip(".,!?").lower() for s in tekst.split() if s.strip(".,!?")]


def main() -> None:
    print("=== Deepgram ===")
    print(json.dumps(sprawdz_deepgram(), ensure_ascii=False, indent=2))
    print("=== ElevenLabs (klucz i głos z .env) ===")
    stan = sprawdz_elevenlabs()
    print(json.dumps(stan, ensure_ascii=False, indent=2))

    print("\n=== Kandydaci na głos: premade, darmowy plan, model multilingual (tekst PL) ===")
    for nazwa, voice_id in KANDYDACI_GLOSOW.items():
        audio = syntezuj(ZDANIE, "mp3_44100_128", voice_id=voice_id)
        if audio is None:
            print(f"{nazwa}: synteza nie działa")
            continue
        plik = NAGRANIA_DIR / f"probka-glos-{voice_id}.mp3"
        plik.write_bytes(audio)
        transkrypt = transkrybuj(audio, "audio/mpeg")
        wpuszczone, uslyszane = _normalizuj(ZDANIE), _normalizuj(transkrypt)
        trafione = sum(1 for s in wpuszczone if s in uslyszane)
        procent = 100 * trafione / len(wpuszczone)
        print(f"\n{nazwa} ({voice_id}):")
        print(f"  usłyszane: {transkrypt}")
        print(f"  rozpoznane: {trafione}/{len(wpuszczone)} ({procent:.0f}%) — plik: {plik}")


if __name__ == "__main__":
    main()
