"""
Standalone exploration of the Gemini API -- NOT connected to VESPER at all.
Just a way to get a feel for how it compares to the Anthropic/Claude setup
VESPER currently uses, before deciding whether it's worth building on.

Tests the two things VESPER actually depends on most:
  1. Personality-consistent text responses (like VESPER's PERSONA)
  2. Reliable structured JSON output (like intent classification needs)

How to use:
  1. Get a free API key at https://aistudio.google.com/app/apikey
  2. pip install google-genai
  3. Set the key: $env:GEMINI_API_KEY = "your-key-here"   (PowerShell)
  4. Run: python gemini_test.py
"""

import os
import json
import time
from google import genai
from google.genai import types
from google.genai.errors import ServerError

# Initialize client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


MODEL = "gemini-3.6-flash"

PERSONA = """You are VESPER, the voice and personality of a personal AI assistant.
Unflappably competent, quietly superior, mildly amused by how often you're needed.
Dry, understated wit -- deadpan one-liners, not slapstick jokes.
Willing to be bluntly, irreverently honest instead of endlessly polite and robotic.
Underneath the snark you are completely loyal and genuinely useful."""


def safe_generate_content(contents, config=None):
    """Helper function to retry automatically on temporary 503 errors."""
    for attempt in range(3):
        try:
            return client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )
        except ServerError as e:
            if e.code == 503 and attempt < 2:
                print("503 server busy, retrying in 2 seconds...")
                time.sleep(2)
            else:
                raise e


def test_personality():
    print("=== Test 1: Personality-consistent response ===")
    
    config = types.GenerateContentConfig(
        system_instruction=PERSONA
    )
    
    response = safe_generate_content(
        contents="What's the weather like today?",
        config=config,
    )
    print(response.text)
    print()


def test_structured_json():
    print("=== Test 2: Structured JSON output (like intent classification) ===")
    
    system = PERSONA + """

Classify the user's request. Reply with ONLY a JSON object:
{"intent": "weather" or "time" or "reminder" or "fallback", "location": "<city mentioned, or null>"}"""

    # Gemini supports enforcing structured JSON natively via response_mime_type
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json"
    )

    response = safe_generate_content(
        contents="what's it like in Chicago right now",
        config=config,
    )
    
    raw = response.text.strip()
    print(f"Raw response: {raw!r}")
    try:
        parsed = json.loads(raw)
        print(f"Parsed successfully: {parsed}")
    except json.JSONDecodeError as e:
        print(f"FAILED to parse as JSON: {e}")
    print()


if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY first -- see instructions at top.")
    else:
        test_personality()
        test_structured_json()
        print("=== Done ===")


def list_available_models():
    print("=== Available Models for your API Key ===")
    for m in client.models.list():
        print(f"ID: {m.name}")

# Run it in main:
if __name__ == "__main__":
    list_available_models()