# Voice reception agent

A phone agent that answers calls, qualifies the caller and books the appointment in
Google Calendar. Polish speech end to end: Twilio for telephony, Pipecat for the audio
loop, Deepgram for speech-to-text, ElevenLabs for the voice, OpenRouter for the model.

Built as a receptionist for an automotive workshop: it answers the questions a workshop
gets every day (prices, opening hours, whether a job can be done), checks free slots and
writes the booking straight into the calendar.

## Call flow

```
Caller ──► Twilio number ──► POST /twiml ──► TwiML (connect to WebSocket)
                                                │
                                          WebSocket /ws
                                                │
        Pipecat: Deepgram STT (pl) ──► OpenRouter LLM ──► ElevenLabs TTS (pl)
                  ▲ VAD interrupts the agent when the caller talks over it
                  │
                  ├── tool: check free slots   ──► Google Calendar freebusy
                  ├── tool: book appointment   ──► Google Calendar insert
                  └── tool: hand over to a human ──► callback record + notification
```

The audio loop runs in both directions at once: the agent hears the caller while it is
speaking, so a caller who interrupts is not talked over. Measured response time from the
end of the caller's sentence to the first syllable of the answer: 0.4 to 0.9 s with the
tool call included.

## Tools the agent can call

| Tool | What it does |
|---|---|
| `sprawdz_wolne_terminy` | reads free slots from Google Calendar freebusy for the next working days |
| `zapisz_termin` | inserts the appointment (name, phone, topic, vehicle) into the calendar |
| `przekaz_do_czlowieka` | records a callback request with the reason and the caller's number |

Booking is idempotent: the key is a hash of the phone number and the slot, so a caller who
repeats the request does not create a second appointment. Stale reservations older than 48
hours are cleaned up on a schedule.

## Stack

Python 3.12, [Pipecat](https://github.com/pipecat-ai/pipecat) (Deepgram, ElevenLabs,
Silero VAD, OpenAI-compatible client), FastAPI + uvicorn for the webhook and WebSocket,
Google Calendar API, Twilio Programmable Voice. Dependencies in `pyproject.toml`.

## Run locally

```bash
uv sync                                  # or: python3 -m venv .venv && pip install -e .
cp .env.example .env                     # keys, calendar id, public wss URL
python -m agent.setup_demo_calendar      # creates the demo calendar and prints its id
uvicorn agent.server:app --port 8080
```

Twilio needs a public `wss://` address, so expose the port with a tunnel (ngrok, cloudflared
or a reverse proxy with a certificate) and put that address in `PUBLIC_WSS_URL`. Point the
Twilio number's voice webhook at `POST /twiml`.

`.env.example` lists every variable: OpenRouter, Deepgram, ElevenLabs, Twilio, calendar id,
public WebSocket URL, the demo number and the owner's number for the "read my day" mode.

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

`tests/test_rezerwacje.py` covers slot checking, booking idempotency, out-of-hours requests
and the callback path against a fake calendar. `tests/test_pipeline.py` covers the audio
pipeline wiring without opening a real call.

## Status and limits

The agent works end to end on a Twilio test number against a demo calendar. To go live for
a client it needs their Twilio number, their calendar and their knowledge of prices and
opening hours; the code does not change. The repository contains no credentials and no
recordings.

## License

MIT, see [LICENSE](LICENSE).

Built by [Jan Noga](https://jnxautomation.pl), jnxautomation.pl.
