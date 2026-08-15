# Voice Assistant Skeleton

A minimal, runnable starting point for the project: a shared "brain" backend
plus a transparent desktop overlay client that can listen, think, and speak.

```
voice-assistant/
├── brain/          # FastAPI service - shared logic for all future clients
│   ├── main.py
│   └── requirements.txt
└── desktop/         # Electron overlay - transparent, always-on-top HUD
    ├── main.js
    ├── preload.js
    ├── index.html
    └── package.json
```

Mobile isn't included yet on purpose -- get the desktop loop working first,
then we add a React Native client that talks to the same brain service.

## What this does right now

1. You hold the mic button in the overlay.
2. Chrome/Electron's built-in speech recognition transcribes what you say.
3. The transcript is sent to the FastAPI brain over HTTP.
4. The brain does very simple keyword-based "intent" matching and replies.
5. The reply is spoken back out loud and shown in the HUD.

It's deliberately dumb (no real LLM, no real APIs) so you have something
that *runs end-to-end* before adding complexity.

## Prerequisites

You'll need two things installed on your machine:

- **Python 3.10+** — https://www.python.org/downloads/
- **Node.js 18+** (includes npm) — https://nodejs.org/

Check both are installed by running in a terminal:
```
python3 --version
node --version
```

## 1. Run the brain service

```bash
cd brain
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Leave this running. Visit http://127.0.0.1:8000/health in a browser --
you should see `{"status":"ok"}`.

## 2. Run the desktop overlay

Open a **new** terminal window (keep the brain running in the first one):

```bash
cd desktop
npm install
npm start
```

A small transparent panel should appear in the top-left of your screen.
Click and hold "Hold to talk," say something like "hello" or "order me a
pizza," and release. You should see the transcript, hear a spoken reply,
and see the reply text appear.

## Troubleshooting

- **"couldn't reach brain service"** — make sure step 1's terminal is still
  running and shows no errors.
- **Mic button is disabled** — your Electron/Chromium build doesn't support
  `SpeechRecognition`. This is rare but can happen on Linux; we can swap in
  a cloud STT API if so.
- **Nothing happens on mousedown** — check View > Toggle Developer Tools
  (or uncomment the `openDevTools` line in `main.js`) for console errors.

## Suggested next steps, in order

1. **Get this running as-is.** Don't add anything until the loop above
   works end-to-end.
2. **Replace keyword matching with a real LLM call** in `brain/main.py`'s
   `handle_message()` -- send the user's text to an LLM with a system
   prompt listing available actions, parse a structured response.
3. **Pick one real integration** (e.g. a notifications API) and wire it in
   as its own module under `brain/`, called from `handle_message()`.
4. **Decide the mobile overlay strategy** -- true floating overlays are
   Android-only; iOS will need a different UX (e.g. a widget or in-app
   HUD). This affects how much UI code you can actually share with
   desktop.
5. **Add a persistent "wake word"** listener if you want hands-free
   activation instead of holding a button.
