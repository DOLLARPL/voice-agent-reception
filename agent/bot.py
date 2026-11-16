"""Pętla głosowa agenta: Pipecat + Twilio Media Streams, LLM przez OpenRouter.

Ten plik jest jedynym miejscem, które wie, że rozmowa leci przez telefon. Logika rozmowy
(prompt, narzędzia, kalendarz) siedzi w `prompt.py`, `tools.py` i `calendar_client.py`
i działa tak samo w próbie tekstowej (`rehearsal.py`).

Budżet latencji: cel poniżej 1,2 s od końca wypowiedzi klienta do pierwszego dźwięku agenta.
Dlatego model jest mały, TTS strumieniuje (eleven_flash), a przerwanie mowy obsługuje VAD.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import wave
from zoneinfo import ZoneInfo

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from . import tools
from .config import (
    DEEPGRAM_API_KEY,
    ELEVENLABS_API_KEY,
    ELEVENLABS_MODEL,
    ELEVENLABS_VOICE_ID,
    LLM_MODEL,
    NAGRANIA_DIR,
    OPENROUTER_API_KEY,
    OWNER_PHONE,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
)
from .leads import zapisz_lead
from .prompt import system_prompt

TZ = ZoneInfo("Europe/Warsaw")


def _handler(nazwa: str):
    """Owija zwykłą funkcję narzędzia w obsługę wymaganą przez Pipecat."""

    async def handler(params):
        argumenty = dict(params.arguments or {})
        wynik = tools.wykonaj(nazwa, argumenty)
        await params.result_callback(wynik)

    return handler


def zbuduj_uslugi():
    """STT, TTS, LLM i schemat narzędzi. Wołane raz na połączenie — każda rozmowa ma własny kontekst."""
    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
        settings=DeepgramSTTService.Settings(model="nova-3", language="pl"),
    )
    tts = ElevenLabsTTSService(
        api_key=ELEVENLABS_API_KEY,
        settings=ElevenLabsTTSService.Settings(voice=ELEVENLABS_VOICE_ID, model=ELEVENLABS_MODEL),
    )
    llm = OpenRouterLLMService(
        api_key=OPENROUTER_API_KEY,
        settings=OpenRouterLLMService.Settings(model=LLM_MODEL, system_instruction=system_prompt()),
    )
    narzedzia = tools.pipecat_tools()
    for nazwa in tools.TOOL_SPECS:
        llm.register_function(nazwa, _handler(nazwa))
    return stt, tts, llm, narzedzia


def zapisz_nagranie(audio: bytes, sample_rate: int, num_channels: int) -> str | None:
    if not audio:
        return None
    plik = NAGRANIA_DIR / f"rozmowa-{dt.datetime.now(TZ).strftime('%Y-%m-%d-%H%M')}.wav"
    with io.BytesIO() as bufor:
        with wave.open(bufor, "wb") as wf:
            wf.setsampwidth(2)
            wf.setnchannels(num_channels)
            wf.setframerate(sample_rate)
            wf.writeframes(audio)
        plik.write_bytes(bufor.getvalue())
    return str(plik)


def zapisz_transkrypt_z_kontekstu(context: LLMContext, caller_phone: str, nagranie: str | None) -> str:
    """Zapisuje rozmowę z kontekstu LLM.

    Bierzemy kontekst, a nie zdarzenia agregatora: kontekst zawiera dokładnie to, co widział model
    (wypowiedzi klienta, odpowiedzi agenta i wywołania narzędzi), więc transkrypt nie rozjedzie się
    z tym, co faktycznie zostało powiedziane.
    """
    teraz = dt.datetime.now(TZ)
    plik = NAGRANIA_DIR / f"rozmowa-{teraz.strftime('%Y-%m-%d-%H%M')}.md"
    linie = [
        "# Transkrypt rozmowy — agent recepcji",
        "",
        f"- Data: {teraz.strftime('%Y-%m-%d %H:%M')} (Europe/Warsaw)",
        f"- Numer dzwoniącego: {caller_phone or 'nieznany'}",
        f"- Nagranie: {nagranie or 'brak'}",
        f"- Model: `{LLM_MODEL}` przez OpenRouter, STT Deepgram, TTS ElevenLabs",
        "",
        "## Rozmowa",
        "",
    ]
    for wiadomosc in context.get_messages():
        rola = wiadomosc.get("role")
        tresc = wiadomosc.get("content") or ""
        tresc = tresc.strip() if isinstance(tresc, str) else ""
        if rola == "user" and tresc:
            linie += [f"**Klient:** {tresc}", ""]
        elif rola == "assistant":
            if tresc:
                linie += [f"**Agent:** {tresc}", ""]
            for wywolanie in wiadomosc.get("tool_calls") or []:
                linie += [f"> narzędzie: `{wywolanie['function']['name']}({wywolanie['function']['arguments']})`", ""]
        elif rola == "tool":
            linie += [f"> wynik: `{tresc[:400]}`", ""]
    plik.write_text("\n".join(linie) + "\n", encoding="utf-8")
    return str(plik)


def _czy_to_wlasciciel(caller_phone: str) -> bool:
    if not caller_phone or not OWNER_PHONE:
        return False
    return caller_phone.lstrip("+")[-9:] == OWNER_PHONE.lstrip("+")[-9:]


async def run_bot(
    websocket,
    stream_sid: str,
    call_sid: str | None,
    caller_phone: str = "",
) -> None:
    """Uruchamia pętlę głosową dla jednego połączenia. Zwraca, gdy rozmowa się skończy."""
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid or "",
        account_sid=TWILIO_ACCOUNT_SID,
        auth_token=TWILIO_AUTH_TOKEN,
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    stt, tts, llm, narzedzia = zbuduj_uslugi()

    context = LLMContext(tools=narzedzia)
    if _czy_to_wlasciciel(caller_phone):
        # Prywatny numer Jana: nie recepcja, tylko szybki raport (etap 5 planu).
        context.add_message({"role": "system", "content":
            "To dzwoni właściciel warsztatu. Odpowiedz krótko, co ma dzisiaj w kalendarzu, "
            "i zapytaj, co zapisać. Nie prowadź kwalifikacji leada."})

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )
    nagrywarka = AudioBufferProcessor()

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        nagrywarka,
        assistant_aggregator,
    ])

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )
    runner = WorkerRunner(handle_sigint=False, force_gc=True)
    await runner.add_workers(worker)

    nagranie: dict[str, str | None] = {"plik": None}

    @transport.event_handler("on_client_connected")
    async def _polaczono(transport, client):
        await nagrywarka.start_recording()
        await worker.queue_frames([LLMRunFrame()])  # agent odzywa się pierwszy

    @nagrywarka.event_handler("on_audio_data")
    async def _audio(buffer, audio, sample_rate, num_channels):
        nagranie["plik"] = zapisz_nagranie(audio, sample_rate, num_channels)

    try:
        await runner.run()
    finally:
        transkrypt = zapisz_transkrypt_z_kontekstu(context, caller_phone, nagranie["plik"])
        zapisz_lead({
            "typ": "rozmowa",
            "telefon": caller_phone,
            "nagranie": nagranie["plik"],
            "transkrypt": transkrypt,
            "tryb": "prywatny" if _czy_to_wlasciciel(caller_phone) else "recepcja",
            "wiadomosci": len(context.get_messages()),
        })
        print(json.dumps({"nagranie": nagranie["plik"], "transkrypt": transkrypt}, ensure_ascii=False))
