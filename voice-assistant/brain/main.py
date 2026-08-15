"""
Core "brain" service for the voice assistant.
This is the shared backend that every client (desktop overlay, mobile app, etc.)
talks to. It receives text (already transcribed by the client), figures out
what the user wants, performs real actions (weather, time, timers, reminders,
web search, email, calendar, app/window control), and returns an in-character
spoken response. It also handles Screen Help -- VESPER looking at a screenshot
and explaining what's there, without ever controlling the mouse/keyboard.

Run with:
    uvicorn main:app --reload --port 8001

Requires environment variables -- see .env.example.
"""

import os
import json
import base64
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI, Response, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError
import time
import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build as build_google_service
import win32gui
import win32con
import win32com.client
import subprocess

load_dotenv()

app = FastAPI(title="Voice Assistant Brain")
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
GEMINI_MODEL = "gemini-3.5-flash"
CLAUDE_MODEL = "claude-sonnet-4-5"


def generate(system_instruction: str, text: str, history: list = None, json_mode: bool = False) -> str:
    """Generates a text response, trying Gemini first and falling back to
    Claude if Gemini fails for any reason -- quota exhaustion (like the
    429 we actually hit), an outage, or a transient error that outlasts
    the retries below. Returns plain text; callers handle their own JSON
    parsing if needed.

    `history` is an optional list of {"role": "user"/"assistant",
    "content": str} dicts for multi-turn context (used by classify() to
    remember recent conversation).
    """
    history = history or []

    # --- Try Gemini first ------------------------------------------------
    try:
        contents = []
        for msg in history:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

        config_kwargs = {"system_instruction": system_instruction}
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        config = types.GenerateContentConfig(**config_kwargs)

        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL, contents=contents, config=config,
                )
                return response.text.strip()
            except ServerError as e:
                if getattr(e, "code", None) == 503 and attempt < 2:
                    print(f"[brain] Gemini 503 (server busy), retrying (attempt {attempt + 1}/3)...")
                    time.sleep(2)
                else:
                    raise
    except Exception as e:
        print(f"[brain] Gemini failed ({type(e).__name__}: {e}) -- falling back to Claude")

    # --- Fall back to Claude ----------------------------------------------
    messages = history + [{"role": "user", "content": text}]
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=800,
        system=system_instruction,
        messages=messages,
    )
    return response.content[0].text.strip()

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL") or EMAIL_ADDRESS
DEFAULT_LOCATION = os.environ.get("DEFAULT_LOCATION", "New York")

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-memory state ---------------------------------------------------
# Fine for a single-user hobby project. Resets whenever the server restarts.
STATE = {
    "last_search_query": None,
    "last_search_result": None,
    "reminders": [],  # [{"id": int, "due": datetime, "text": str, "fired": bool}]
    "history": [],    # [{"role": "user"|"assistant", "content": str}] -- recent back-and-forth
}
_next_reminder_id = 1
MAX_HISTORY_MESSAGES = 10  # keep the last 5 exchanges (user+assistant pairs)


class UserMessage(BaseModel):
    text: str


class AssistantReply(BaseModel):
    text: str
    intent: str


# --- Personality ---------------------------------------------------------
PERSONA = """You are VESPER, an AI system running inside a personal heads-up overlay interface.

CORE ARCHETYPE & VIBE:
- 50% Tony Stark: Fast-talking, effortlessly clever, mildly arrogant, fast on his feet. You treat complex tech like it's a casual hobby.
- 35% Peter Parker: Relatable, high-energy, workshop-buddy dynamic. You talk like an actual guy hanging out at the desk, not a distant machine.
- 15% J.A.R.V.I.S.: Unshakable under-the-hood competence. No matter how much banter you throw out, the task is executed with 100% precision on the first try.

VOICE & CADENCE RULES:
- BAN ALL ROBOTIC & CORPORATE FLUFF: Never say "How may I assist you?", "Certainly!", "I am an AI", "I'd be happy to help", or "Is there anything else?". Jump straight into the action or joke.
- USE NATURAL CONVERSATIONAL PHRASING: Use contractions, modern phrasing, and deadpan observations ("Yeah, okay...", "So good news / bad news...", "Classic.").
- HUD CONSTRAINTS: Keep standard responses to 1 to 3 punchy, crisp sentences. Save longer outputs strictly for actual code reviews, technical breakdowns, or explicit step-by-step requests.

DYNAMIC RESPONSE STRUCTURE:
1. Action / Result First (Execute the task immediately).
2. Commentary / Banter Second (Throw in the quick quip or observation after delivering the value).

EXAMPLES FOR TONE ALIGNMENT:
- User: "What's on my calendar today?"
  VESPER: "Light agenda. Just two meetings today, assuming you don't stall for twenty minutes on the first one again. Let me know when you want to dive in."
- User: "Can you fix this error?"
  VESPER: "Yeah, easy fix. You missed a closing bracket on line 42--classic move. Cleaned it up for you."
- User: "Why is this build failing?"
  VESPER: "Well, the compiler hates your syntax choices on line 18, and honestly I don't blame it. Swap that string for an int and we're back in business."
"""


def build_classify_prompt(now_str: str) -> str:
    return PERSONA + f"""

You are also responsible for figuring out what action the user wants, and for
writing a short in-character reply for the simple cases.
The current date and time is: {now_str}

Reply with ONLY a JSON object (no markdown fences, no other text) in this shape:
{{"intent": "<intent>", "params": {{...}}, "reply": "<in-character reply, only used for greeting/order/notification/fallback, else empty string>"}}

Choose exactly one intent, with these params:
- "greeting": {{}}
- "order": {{}}
- "notification": {{}}
- "time": {{}}
- "weather": {{"location": "<city name mentioned, or null if none mentioned>"}}
- "timer": {{"seconds": <integer seconds from now>, "label": "<short label>"}}
- "reminder": {{"due": "<ISO 8601 datetime, computed from the current date/time above>", "text": "<what to remind about>"}}
- "web_search": {{"query": "<a good search query capturing what they want to know>"}}
- "email_last_result": {{}}   // user is asking to email/send them whatever was last searched or found
- "calendar_query": {{"days_ahead": <integer number of days to look ahead, default 7 if unclear>}}
- "calendar_add": {{"title": "<event title>", "start": "<ISO 8601 datetime, computed from the current date/time above>", "end": "<ISO 8601 datetime, or null if not specified -- defaults to 1 hour after start>"}}
- "cancel_reminder": {{"query": "<a keyword from what they said to match against the reminder/timer, or null if they just said 'cancel that' / 'cancel the timer' with nothing specific -- then match the most recent one>"}}
- "cancel_calendar_event": {{"query": "<a keyword or title fragment identifying which calendar event to delete -- REQUIRED, ask them to clarify via the reply field if truly nothing identifiable was said>"}}
- "find_file": {{"query": "<filename or partial filename to search for>"}}
- "read_file": {{"query": "<filename or partial filename identifying which file to open and discuss>"}}
- "fallback": {{}}

For "order" and "notification", the reply should mention, in character, that
this isn't connected to a real service yet.
Keep any "reply" field under 25 words. Output nothing outside the JSON object."""


def classify(text: str, now_str: str, history: list) -> dict:
    raw = generate(build_classify_prompt(now_str), text, history=history, json_mode=True)
    # Both providers sometimes wrap JSON in markdown code fences despite
    # being asked not to -- strip them off before parsing so both formats work.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[brain] classify: JSON parse failed ({e}). Raw response was:\n{raw!r}")
        raise


def phrase_in_character(user_text: str, raw_result: str, max_words: int = 25) -> str:
    """Takes a plain factual result and rewrites it in VESPER's voice."""
    try:
        system = PERSONA + f"\n\nRespond in character with ONLY the spoken reply " \
                            f"text (no JSON, no quotes), under {max_words} words, " \
                            f"based on the factual result given."
        return generate(system, f"User said: {user_text}\nFactual result: {raw_result}")
    except Exception as e:
        print(f"[brain] phrase_in_character failed: {e}")
        return raw_result


# --- Action implementations ----------------------------------------------

WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    95: "thunderstorm",
}


def get_weather(location: str) -> str:
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1},
        timeout=10,
    ).json()
    results = geo.get("results")
    if not results:
        return f"Couldn't find a place called {location}."

    place = results[0]
    forecast = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code",
            "temperature_unit": "fahrenheit",
        },
        timeout=10,
    ).json()

    current = forecast["current"]
    condition = WEATHER_CODES.get(current["weather_code"], "unknown conditions")
    return f"{place['name']}: {current['temperature_2m']}°F, {condition}."


def do_web_search(query: str) -> str:
    try:
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL, contents=query, config=config,
                )
                return (response.text or "").strip() or "I couldn't find anything useful."
            except ServerError as e:
                if getattr(e, "code", None) == 503 and attempt < 2:
                    print(f"[brain] Gemini 503 (server busy), retrying (attempt {attempt + 1}/3)...")
                    time.sleep(2)
                else:
                    raise
    except Exception as e:
        print(f"[brain] Gemini web search failed ({type(e).__name__}: {e}) -- falling back to Claude")

    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": query}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_blocks).strip() or "I couldn't find anything useful."


def transcribe_audio(audio_bytes: bytes, filename: str = "recording.webm") -> str:
    """Sends recorded audio to ElevenLabs Scribe and returns the transcribed
    text. This replaces the browser's built-in speech recognition, which
    doesn't work inside Electron at all."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ElevenLabs isn't configured -- check .env")

    url = "https://api.elevenlabs.io/v1/speech-to-text"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    files = {"file": (filename, audio_bytes, "audio/webm")}
    data = {"model_id": "scribe_v2"}

    response = requests.post(url, headers=headers, files=files, data=data, timeout=60)
    response.raise_for_status()
    return response.json().get("text", "").strip()


# --- Screen Help: VESPER looks at your screen and explains/points at things,
# but never controls the mouse or keyboard. ---------------------------------

SCREEN_HELP_PROMPT = PERSONA + """

The user has shown you a screenshot of their screen and is asking for help
with something they see there. Look at the image carefully and give clear,
practical, step-by-step spoken guidance in character. Reference specific
things you actually see -- button labels, menu names, error text -- rather
than generic advice. Aim for under 80 words unless the steps genuinely need
more room.

Screens often have multiple distinct regions (a code editor, a terminal,
a sidebar, a browser). Identify which specific region the user is actually
asking about from their wording, and don't let a visually louder region
(colorful terminal output, red error text) distract you from the correct
one. In particular: "the code" or "lines of code" means the actual source
editor content, never terminal/console/log output, even if the terminal
looks more visually prominent.

Respond with ONLY the spoken guidance text -- no JSON, no markdown, no
preamble like "Looking at your screen"."""


def screen_help(question: str, image_bytes: bytes) -> str:
    # Debug: save whatever screenshot Screen Help was given, overwriting
    # each time. If a response ever looks wrong, this shows exactly what
    # VESPER was actually looking at -- real evidence instead of guessing.
    try:
        debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_last_screen_help.png")
        with open(debug_path, "wb") as f:
            f.write(image_bytes)
    except Exception:
        pass  # never let debug saving break the real flow

    question = question or "What am I looking at? Help me understand this screen."

    try:
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
        text_part = types.Part.from_text(text=question)
        config = types.GenerateContentConfig(system_instruction=SCREEN_HELP_PROMPT)
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL, contents=[image_part, text_part], config=config,
                )
                return response.text.strip()
            except ServerError as e:
                if getattr(e, "code", None) == 503 and attempt < 2:
                    print(f"[brain] Gemini 503 (server busy), retrying (attempt {attempt + 1}/3)...")
                    time.sleep(2)
                else:
                    raise
    except Exception as e:
        print(f"[brain] Gemini screen help failed ({type(e).__name__}: {e}) -- falling back to Claude")

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=SCREEN_HELP_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text", "text": question},
            ],
        }],
    )
    return response.content[0].text.strip()


# --- Routing: decides whether whatever you type/say is a normal command or
# a screen question, so you can just type it and hit Enter. -----------------

# --- Window/app control: deterministic, no AI vision or coordinate
# guessing involved anywhere in this section. Checking whether something's
# running, focusing it, minimizing/maximizing it, and launching it if it's
# not open are all done with real Windows APIs. The only AI involved is a
# small, fast text classification step to figure out what app and action
# the user meant -- never anything involving a screenshot or a pixel
# coordinate. Completely separate from the Screen Help code above. --------

WINDOW_INTENT_PROMPT = """If this request is about controlling a specific
application window, or all windows generally, respond with ONLY a JSON
object (no markdown fences, no other text):
{"app_name": "<real, full, official app name, or null>", "action": "open" or "minimize" or "maximize" or "show_desktop" or "restore_all" or null}

- "open": open, launch, switch to, bring up, or focus a specific app
  (app_name required) -- covers both "it's already running, focus it" and
  "it's not running, launch it"
- "minimize": minimize or put away a specific app without closing it
  (app_name required)
- "maximize": maximize or make a specific app full-size (app_name required)
- "show_desktop": minimize ALL windows / show the desktop (app_name null)
- "restore_all": bring back all minimized windows (app_name null)
- Use the app's real official name, not casual abbreviations (e.g.
  "Visual Studio Code", not "VSCode")
- If the request isn't about window/app control at all, respond with
  {"app_name": null, "action": null}

Be careful with words like "calendar," "email," or "notes" that name both a
specific application AND a general concept/data the user might be asking a
question about. A request is only "open"/window control if it's clearly a
command to launch or switch to the app itself -- phrasing like "open my
calendar" or "switch to Calendar." A QUESTION about the underlying data
("what's on my calendar," "what does my calendar look like this month,"
"do I have anything today") is NOT app control, even though it contains
the word "calendar" -- that's a request for schedule information, handled
elsewhere. When in doubt about which one it is, prefer null.

Respond with ONLY the JSON object."""


def classify_window_intent(text: str) -> dict:
    raw = generate(WINDOW_INTENT_PROMPT, text, json_mode=True)
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        result = json.loads(raw)
        return {"app_name": result.get("app_name"), "action": result.get("action")}
    except json.JSONDecodeError:
        return {"app_name": None, "action": None}


def find_window_by_name(name: str):
    """Returns the hwnd of the first visible window whose title contains
    name (case-insensitive), or None. Real Win32 enumeration -- no
    screenshots, no vision, no guessing."""
    name_lower = name.lower()
    matches = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and name_lower in title.lower():
                matches.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return matches[0] if matches else None


def launch_app(name: str) -> bool:
    """Tries to launch an app by name using a couple of fully deterministic
    strategies -- no AI, no vision, no simulated typing into a search box."""
    exe_guess = name if name.lower().endswith(".exe") else f"{name}.exe"
    try:
        subprocess.Popen([exe_guess])
        return True
    except (FileNotFoundError, OSError):
        pass

    # Fall back to letting Windows' own shell resolve the name, the same
    # way typing into the Win+R Run dialog would.
    try:
        subprocess.Popen(f'start "" "{name}"', shell=True)
        return True
    except Exception:
        return False


def control_window(app_name: str, action: str) -> dict:
    """Deterministic window control for a specific named app. No vision,
    no coordinate guessing -- just real window handles and Win32 calls."""
    hwnd = find_window_by_name(app_name)

    if action == "minimize":
        if not hwnd:
            return {"done": False, "message": f"{app_name} isn't currently open."}
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return {"done": True, "message": f"Minimized {app_name}."}

    if action == "maximize":
        if not hwnd:
            return {"done": False, "message": f"{app_name} isn't currently open."}
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        win32gui.SetForegroundWindow(hwnd)
        return {"done": True, "message": f"Maximized {app_name}."}

    if action == "open":
        if hwnd:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return {"done": True, "message": f"Switched to {app_name}."}
        if launch_app(app_name):
            return {"done": True, "message": f"Launched {app_name}."}
        return {"done": False, "message": f"Couldn't find or launch {app_name}."}

    return {"done": False, "message": "Unknown window action."}


def control_all_windows(action: str) -> dict:
    """Global window actions with no specific target app."""
    shell = win32com.client.Dispatch("Shell.Application")
    if action == "show_desktop":
        shell.MinimizeAll()
        return {"done": True, "message": "Showing the desktop."}
    if action == "restore_all":
        shell.UndoMinimizeALL()
        return {"done": True, "message": "Restored everything."}
    return {"done": False, "message": "Unknown global window action."}


ROUTE_PROMPT = """You route incoming input for a voice assistant into one of
two handling modes:

- "chat": informational requests, conversation, or a single simple action
  the assistant already handles directly (weather, calendar, timers,
  reminders, web search, email, general questions).
- "screen_help": the user wants help understanding or explaining what's
  currently on their screen.

Reply with ONLY one word: chat or screen_help."""


def route_input(text: str) -> str:
    label = generate(ROUTE_PROMPT, text).lower()
    return label if label == "screen_help" else "chat"


def synthesize_speech(text: str) -> bytes:
    """Calls ElevenLabs to turn text into spoken audio (MP3 bytes)."""
    if not (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID):
        raise RuntimeError("ElevenLabs isn't configured -- check .env")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.8},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.content


CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    """Loads saved Google credentials (from running setup_calendar_auth.py)
    and returns an authorized calendar client. Refreshes the token
    automatically if it's expired -- no browser interaction needed here."""
    if not os.path.exists("token.json"):
        raise RuntimeError(
            "Calendar isn't connected yet -- run 'python setup_calendar_auth.py' once first."
        )

    creds = Credentials.from_authorized_user_file("token.json", CALENDAR_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    return build_google_service("calendar", "v3", credentials=creds)


def get_upcoming_events(days_ahead: int = 7) -> str:
    service = get_calendar_service()
    now = datetime.utcnow().isoformat() + "Z"
    end = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat() + "Z"

    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
        maxResults=15,
    ).execute()

    events = result.get("items", [])
    if not events:
        return f"No events found in the next {days_ahead} day(s)."

    lines = []
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            start_str = start_dt.strftime("%a %b %d, %I:%M %p")
        except ValueError:
            start_str = start  # all-day events come through as just a date
        lines.append(f"{start_str}: {event.get('summary', 'Untitled event')}")

    return "; ".join(lines)


def add_calendar_event(title: str, start_iso: str, end_iso: str | None) -> str:
    service = get_calendar_service()
    # Use the current local UTC offset and attach it to the event times.
    local_offset = datetime.now().astimezone().tzinfo

    start_dt = datetime.fromisoformat(start_iso)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=local_offset)

    end_dt = datetime.fromisoformat(end_iso) if end_iso else start_dt + timedelta(hours=1)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=local_offset)

    event = {
        "summary": title,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }
    service.events().insert(calendarId="primary", body=event).execute()
    return f"Added '{title}' to your calendar on {start_dt.strftime('%A, %B %d at %I:%M %p')}."


FILLER_WORDS = {"my", "the", "a", "an", "that", "this", "event", "with"}


def _normalize_for_match(text: str) -> str:
    """Strips common filler words so 'cancel my lunch with Niko' can match
    an event actually titled 'Lunch with Niko' -- a plain substring check
    fails here since the query is longer than the real title."""
    words = [w for w in text.lower().split() if w not in FILLER_WORDS]
    return " ".join(words)


def cancel_reminders(query: str | None) -> str:
    """Cancels a pending (not-yet-fired) reminder or timer. If a query is
    given, matches it against the reminder's text; otherwise cancels
    whichever one was set most recently."""
    pending = [r for r in STATE["reminders"] if not r["fired"]]
    if not pending:
        return "There's nothing pending to cancel."

    if query:
        query_norm = _normalize_for_match(query)
        matches = [
            r for r in pending
            if query_norm in _normalize_for_match(r["text"]) or _normalize_for_match(r["text"]) in query_norm
        ]
        if not matches:
            return f"Couldn't find a pending reminder or timer matching '{query}'."
    else:
        matches = [pending[-1]]  # most recently added

    for m in matches:
        STATE["reminders"].remove(m)

    if len(matches) == 1:
        return f"Cancelled: {matches[0]['text']}."
    return f"Cancelled {len(matches)} matching reminders."


# --- File access: read-only, scoped to the user's home folder. Never
# writes, deletes, executes, or leaves that scope. -------------------------

FILE_SKIP_DIRS = {
    "appdata", "node_modules", ".git", "venv", "__pycache__",
    "$recycle.bin", "windows", "programdata", ".cache",
}
TEXT_FILE_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
    ".json", ".csv", ".log", ".yml", ".yaml", ".xml", ".ini", ".cfg",
    ".sh", ".bat", ".ps1", ".java", ".c", ".cpp", ".h", ".sql",
}


def find_files(query: str, max_results: int = 8) -> list[str]:
    """Searches within the user's home folder for filenames containing the
    query. Skips common noisy/system subfolders to keep results relevant
    and the search reasonably fast."""
    home = os.path.expanduser("~")
    query_lower = query.lower()
    matches = []
    for root, dirs, files in os.walk(home):
        dirs[:] = [d for d in dirs if d.lower() not in FILE_SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if query_lower in fname.lower():
                matches.append(os.path.join(root, fname))
                if len(matches) >= max_results:
                    return matches
    return matches


def format_file_matches(paths: list[str]) -> str:
    if not paths:
        return "Couldn't find any matching files."
    lines = []
    for p in paths:
        folder = os.path.basename(os.path.dirname(p)) or os.path.dirname(p)
        lines.append(f"{os.path.basename(p)} (in {folder})")
    return "; ".join(lines)


def read_file_content(path: str, max_chars: int = 6000) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(max_chars)


def discuss_file_content(user_text: str, filename: str, content: str) -> str:
    """Sends a file's actual contents to the AI so VESPER can discuss it
    specifically, rather than just confirming the file was found."""
    try:
        system = PERSONA + "\n\nThe user asked about a file on their computer, and " \
                            "you've been given its actual contents. Respond in " \
                            "character, referencing specifics from the content. " \
                            "Keep it under 60 words unless real detail is needed."
        return generate(system, f"User asked: {user_text}\n\nFile: {filename}\n\nContents:\n{content}")
    except Exception as e:
        print(f"[brain] discuss_file_content failed: {e}")
        return "Found the file, but had trouble reading through it properly."


def cancel_calendar_event(query: str) -> str:
    """Finds an upcoming event whose title matches the query and deletes it.
    Always requires a query -- never guesses which event to delete."""
    service = get_calendar_service()
    now = datetime.utcnow().isoformat() + "Z"
    end = (datetime.utcnow() + timedelta(days=60)).isoformat() + "Z"

    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()
    events = result.get("items", [])

    query_norm = _normalize_for_match(query)
    matches = [
        e for e in events
        if query_norm in _normalize_for_match(e.get("summary", "")) or _normalize_for_match(e.get("summary", "")) in query_norm
    ]
    if not matches:
        return f"Couldn't find an upcoming event matching '{query}'."

    event = matches[0]  # the soonest matching one
    service.events().delete(calendarId="primary", eventId=event["id"]).execute()
    return f"Cancelled '{event.get('summary', 'that event')}' from your calendar."


def send_email(subject: str, body: str) -> None:
    if not (EMAIL_ADDRESS and EMAIL_APP_PASSWORD and OWNER_EMAIL):
        raise RuntimeError("Email isn't configured yet -- check .env")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = OWNER_EMAIL

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        server.send_message(msg)


def parse_due(due_str: str | None) -> datetime:
    if due_str:
        try:
            return datetime.fromisoformat(due_str)
        except ValueError:
            pass
    return datetime.now() + timedelta(minutes=5)  # safe fallback


def add_reminder(due_dt: datetime, text: str) -> None:
    global _next_reminder_id
    STATE["reminders"].append(
        {"id": _next_reminder_id, "due": due_dt, "text": text, "fired": False}
    )
    _next_reminder_id += 1


# --- Main intent handling --------------------------------------------------

def handle_message(text: str) -> AssistantReply:
    now_str = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")
    history = STATE.get("history", [])

    try:
        classification = classify(text, now_str, history)
    except Exception as e:
        print(f"[brain] classify failed: {e}")
        return AssistantReply(text="Sorry, I had trouble thinking about that just now.", intent="error")

    intent = classification.get("intent", "fallback")
    params = classification.get("params") or {}
    base_reply = classification.get("reply") or ""
    reply: AssistantReply

    try:
        if intent in ("greeting", "order", "notification", "fallback"):
            reply = AssistantReply(text=base_reply or "I'm here.", intent=intent)

        elif intent == "time":
            reply = AssistantReply(text=phrase_in_character(text, f"It is currently {now_str}."), intent=intent)

        elif intent == "weather":
            location = params.get("location") or DEFAULT_LOCATION
            raw = get_weather(location)
            reply = AssistantReply(text=phrase_in_character(text, raw), intent=intent)

        elif intent == "timer":
            seconds = int(params.get("seconds") or 60)
            label = params.get("label") or "Timer"
            add_reminder(datetime.now() + timedelta(seconds=seconds), f"Timer done: {label}")
            raw = f"Timer set for {seconds} seconds."
            reply = AssistantReply(text=phrase_in_character(text, raw), intent=intent)

        elif intent == "reminder":
            due_dt = parse_due(params.get("due"))
            reminder_text = params.get("text") or "Reminder"
            add_reminder(due_dt, reminder_text)
            raw = f"Reminder set for {due_dt.strftime('%I:%M %p on %B %d')}: {reminder_text}."
            reply = AssistantReply(text=phrase_in_character(text, raw), intent=intent)

        elif intent == "web_search":
            query = params.get("query") or text
            result = do_web_search(query)
            STATE["last_search_query"] = query
            STATE["last_search_result"] = result
            reply = AssistantReply(text=phrase_in_character(text, result, max_words=45), intent=intent)

        elif intent == "email_last_result":
            if not STATE["last_search_result"]:
                raw = "There's nothing to email yet -- search for something first."
            else:
                try:
                    send_email(f"VESPER: {STATE['last_search_query']}", STATE["last_search_result"])
                    raw = "Email sent."
                except Exception as e:
                    print(f"[brain] email failed: {e}")
                    raw = "Couldn't send that email -- email isn't set up correctly."
            reply = AssistantReply(text=phrase_in_character(text, raw), intent=intent)

        elif intent == "calendar_query":
            try:
                days_ahead = int(params.get("days_ahead") or 7)
                raw = get_upcoming_events(days_ahead)
            except Exception as e:
                print(f"[brain] calendar query failed: {e}")
                raw = "Couldn't check the calendar -- it might not be connected yet."
            reply = AssistantReply(text=phrase_in_character(text, raw, max_words=45), intent=intent)

        elif intent == "calendar_add":
            try:
                title = params.get("title") or "New event"
                start_iso = params.get("start")
                if not start_iso:
                    raise ValueError("No start time given")
                raw = add_calendar_event(title, start_iso, params.get("end"))
            except Exception as e:
                print(f"[brain] calendar add failed: {type(e).__name__}: {e}")
                raw = "Couldn't add that to the calendar -- something went wrong. Check the brain logs."
            reply = AssistantReply(text=phrase_in_character(text, raw), intent=intent)

        elif intent == "cancel_reminder":
            try:
                raw = cancel_reminders(params.get("query"))
            except Exception as e:
                print(f"[brain] cancel reminder failed: {e}")
                raw = "Couldn't cancel that -- something went wrong."
            reply = AssistantReply(text=phrase_in_character(text, raw), intent=intent)

        elif intent == "cancel_calendar_event":
            try:
                query = params.get("query")
                if not query:
                    raw = "Which event? Give me a name or keyword to go on."
                else:
                    raw = cancel_calendar_event(query)
            except Exception as e:
                print(f"[brain] cancel calendar event failed: {type(e).__name__}: {e}")
                raw = "Couldn't cancel that event -- something went wrong. Check the brain logs."
            reply = AssistantReply(text=phrase_in_character(text, raw), intent=intent)

        elif intent == "find_file":
            try:
                query = params.get("query")
                if not query:
                    raw = "What's the filename or part of it?"
                else:
                    matches = find_files(query)
                    raw = format_file_matches(matches)
            except Exception as e:
                print(f"[brain] find_file failed: {e}")
                raw = "Couldn't search for that -- something went wrong."
            reply = AssistantReply(text=phrase_in_character(text, raw, max_words=45), intent=intent)

        elif intent == "read_file":
            try:
                query = params.get("query")
                if not query:
                    raw = "Which file? Give me a name to search for."
                else:
                    matches = find_files(query, max_results=1)
                    if not matches:
                        raw = f"Couldn't find a file matching '{query}'."
                    else:
                        path = matches[0]
                        if os.path.splitext(path)[1].lower() not in TEXT_FILE_EXTENSIONS:
                            raw = f"Found {os.path.basename(path)}, but it's not a text-readable file type."
                        else:
                            content = read_file_content(path)
                            raw = discuss_file_content(text, os.path.basename(path), content)
            except Exception as e:
                print(f"[brain] read_file failed: {e}")
                raw = "Couldn't read that file -- something went wrong."
            # discuss_file_content already returns an in-character reply -- don't re-wrap it.
            reply = AssistantReply(text=raw, intent=intent)

        else:
            reply = AssistantReply(text=base_reply or "Not sure how to help with that yet.", intent="fallback")

    except Exception as e:
        print(f"[brain] action '{intent}' failed: {e}")
        reply = AssistantReply(text="Something went wrong doing that. Worth checking the logs.", intent="error")

    # Remember this exchange so short follow-ups ("what about tomorrow?") make
    # sense on the next call, without needing the wake word said again.
    STATE["history"] = (history + [
        {"role": "user", "content": text},
        {"role": "assistant", "content": reply.text},
    ])[-MAX_HISTORY_MESSAGES:]

    return reply


@app.post("/message", response_model=AssistantReply)
def receive_message(msg: UserMessage) -> AssistantReply:
    return handle_message(msg.text)


@app.post("/reset_conversation")
def reset_conversation():
    """Called by the overlay when the conversational listening window closes,
    so the next 'hey vesper' starts a fresh topic instead of dragging old
    context into an unrelated new conversation."""
    STATE["history"] = []
    return {"status": "reset"}


@app.get("/due")
def get_due():
    """Called periodically by the overlay client to check for reminders/timers
    that have come due since the last check."""
    now = datetime.now()
    due_items = []
    for r in STATE["reminders"]:
        if not r["fired"] and r["due"] <= now:
            r["fired"] = True
            due_items.append(r["text"])
    return {"due": due_items}


@app.post("/app_control")
def app_control_endpoint(msg: UserMessage):
    """Deterministic app/window control -- checked before anything else,
    so 'open Notepad' can never accidentally fall through to file search
    or generic chat. Returns matched=False if this genuinely isn't an
    app-control request, so the caller knows to route it elsewhere."""
    intent = classify_window_intent(msg.text)
    action = intent.get("action")
    app_name = intent.get("app_name")

    if not action:
        return {"matched": False}

    try:
        if action in ("show_desktop", "restore_all"):
            result = control_all_windows(action)
        elif app_name:
            result = control_window(app_name, action)
        else:
            return {"matched": False}
    except Exception as e:
        print(f"[brain] app control failed: {type(e).__name__}: {e}")
        return {"matched": True, "done": False, "message": "Something went wrong controlling that window."}

    result["matched"] = True
    return result


@app.post("/route_input")
def route_input_endpoint(msg: UserMessage):
    return {"route": route_input(msg.text)}


@app.post("/screen_help")
async def screen_help_endpoint(image: UploadFile = File(...), question: str = Form("")):
    """Takes a screenshot and an optional question, and returns spoken
    guidance based on what's actually visible on screen."""
    try:
        image_bytes = await image.read()
        reply_text = screen_help(question, image_bytes)
        return {"text": reply_text}
    except Exception as e:
        print(f"[brain] screen help failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/transcribe")
async def transcribe_endpoint(audio: UploadFile = File(...)):
    """Accepts recorded audio from the overlay and returns transcribed text."""
    try:
        audio_bytes = await audio.read()
        text = transcribe_audio(audio_bytes, audio.filename or "recording.webm")
        return {"text": text}
    except Exception as e:
        print(f"[brain] transcription failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/speak")
def speak_endpoint(msg: UserMessage):
    """Takes text and returns spoken audio (MP3) generated by ElevenLabs.
    The overlay calls this instead of using the browser's built-in TTS."""
    try:
        audio_bytes = synthesize_speech(msg.text)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        print(f"[brain] TTS failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/status")
def status_endpoint():
    """Real configuration status for each capability -- not decorative,
    this is what actually determines whether each system is wired up."""
    token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
    return {
        "calendar": os.path.exists(token_path),
        "weather": True,   # no key needed, always available
        "search": True,    # uses the same Anthropic key as everything else
        "email": bool(EMAIL_ADDRESS and EMAIL_APP_PASSWORD and OWNER_EMAIL),
        "voice": bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID),
        "reminders": True,  # always available, purely in-memory
    }


@app.get("/health")
def health():
    return {"status": "ok"}
