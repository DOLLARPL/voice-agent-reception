"""Zapis leadów: plik JSONL (zawsze) + webhook n8n (gdy skonfigurowany).

n8n dostaje dane już po rozmowie, nigdy w pętli audio — webhooki w środku rozmowy
dodałyby ponad sekundę opóźnienia na wypowiedź.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request
from zoneinfo import ZoneInfo

from .config import DANE_DIR, N8N_LEAD_WEBHOOK

LEADS_PATH = DANE_DIR / "leady.jsonl"
TZ = ZoneInfo("Europe/Warsaw")


def zapisz_lead(entry: dict) -> dict:
    record = {"ts": dt.datetime.now(TZ).isoformat(), **entry}
    with LEADS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    if N8N_LEAD_WEBHOOK:
        _wyslij_do_n8n(record)
    return record


def _wyslij_do_n8n(record: dict, timeout: float = 5.0) -> None:
    body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        N8N_LEAD_WEBHOOK, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception as exc:  # nie może wywalić rozmowy
        print(f"[n8n] nie udało się wysłać leada: {exc}")


def czytaj_leady(limit: int = 20) -> list[dict]:
    if not LEADS_PATH.exists():
        return []
    lines = LEADS_PATH.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines[-limit:]]
