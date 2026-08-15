# AI Secretary

A personal voice assistant: a Python "brain" service plus an Electron desktop
overlay that listens, thinks, and speaks.

The project lives in [`voice-assistant/`](voice-assistant/) — see
[`voice-assistant/README.md`](voice-assistant/README.md) for architecture,
setup, and how to run it.

## Quick start

```bash
# 1. Brain (FastAPI)
cd voice-assistant/brain
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # then fill in your API keys
uvicorn main:app --reload --port 8000

# 2. Desktop overlay (Electron), in a second terminal
cd voice-assistant/desktop
npm install
npm start
```

## Configuration

`voice-assistant/brain/.env` holds all API keys and is gitignored. Start from
[`.env.example`](voice-assistant/brain/.env.example). Google Calendar auth also
needs a `credentials.json` / `token.json` pair in `brain/` — both gitignored.
