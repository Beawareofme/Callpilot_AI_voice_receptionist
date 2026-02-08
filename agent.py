# agent.py
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """
You are CallPilot, an AI receptionist.
Help the user schedule an appointment by collecting: name, date, time.
Ask ONE follow-up question if something is missing.
Keep replies short and clear.
""".strip()

# Standard JSON Schema (supported via response_json_schema)
RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["reply", "extract"],
    "properties": {
        "reply": {"type": "string"},
        "extract": {
            "type": "object",
            "properties": {
                "name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "date": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "time": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["name", "date", "time"],
        },
    },
}

def _to_contents(conversation_history):
    """
    conversation_history: list of dicts like
    {"role": "user"/"assistant", "content": "...", "audio": ...}
    """
    contents = []
    for msg in conversation_history:
        role = msg.get("role")
        text = (msg.get("content") or "").strip()
        if not text:
            continue
        gemini_role = "user" if role == "user" else "model"
        contents.append(types.Content(role=gemini_role, parts=[types.Part.from_text(text=text)]))
    return contents

def llm_reply_and_extract(user_text: str, conversation_history: list, current_slots: dict):
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("Missing GEMINI_API_KEY (or GOOGLE_API_KEY) in .env")

    client = genai.Client(api_key=api_key)

    # Add a lightweight hint (NOT a schema example)
    hint = f"Known slots so far: {current_slots}\nUser message: {user_text}"

    contents = _to_contents(conversation_history)
    # Ensure the latest user message is present even if caller didn’t append it yet
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=hint)]))

    resp = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
            temperature=0.2,
        ),
    )

    # When schema is used, resp.parsed should be a dict
    data = resp.parsed if getattr(resp, "parsed", None) is not None else None
    if not isinstance(data, dict):
        # Fallback (should rarely happen)
        return resp.text, {"name": None, "date": None, "time": None}

    reply = (data.get("reply") or "").strip()
    extract = data.get("extract") or {}
    return reply, extract
