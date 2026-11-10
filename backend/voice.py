"""Talk-to-setup: the engineer describes the project out loud; we fill the Project Setup form.

Two open-source steps, both swappable by environment variable, no paid API needed:
  1. transcribe(): speech -> text with Whisper (faster-whisper, runs on CPU).
     Optional install: pip install -r backend/requirements-voice.txt
  2. understand(): text -> setup fields with an open model behind any OpenAI-compatible
     chat endpoint. Default is Ollama on this machine (http://localhost:11434/v1, qwen2.5:3b);
     point VOICE_LLM_URL / VOICE_LLM_MODEL / VOICE_LLM_KEY at Cloudflare Workers AI, Groq,
     or Claude's compatible endpoint to switch.

The model only reads; clean() decides what reaches the form. Anything not said stays empty,
and the engineer checks every value on the form before continuing. Audio is never stored.
"""

import datetime as dt
import json
import os
import re
import tempfile

import httpx

LLM_URL = os.getenv("VOICE_LLM_URL", "http://localhost:11434/v1").rstrip("/")
LLM_MODEL = os.getenv("VOICE_LLM_MODEL", "qwen2.5:3b")
LLM_KEY = os.getenv("VOICE_LLM_KEY", "")
WHISPER_MODEL = os.getenv("VOICE_WHISPER_MODEL", "base.en")

PROJECT_TYPES = ("Corridor", "Zone", "Single Intersection")
PERIODS = ("AM", "PM")
TEXT_FIELDS = ("name", "client", "projectNumber", "analyst", "checker", "scenario")
MAX_TEXT = 120  # longest value we accept into a form field
DATE_WORDS = re.compile(r"\b(today|tomorrow|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                        r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|"
                        r"july|august|september|october|november|december)\b|\d{1,4}[/-]\d{1,2}", re.I)

PROMPT = """You fill in a traffic study's project setup form from what an engineer said.
Today is {today}. Return ONLY a JSON object with these keys, using null for anything
the engineer did not clearly say. Never guess or invent a value.
- name: project name, e.g. "Main Street Corridor Study"
- client: client or agency, e.g. "City of Austin"
- projectNumber: project number as spoken, e.g. "30901249.003"
- analyst: analyst initials, e.g. "AK"
- checker: checker initials
- date: study date as YYYY-MM-DD (resolve "today", "next Tuesday" against today's date)
- scenario: scenario name, e.g. "Existing", "Opening Year 2030", "Cumulative"
- projectType: one of "Corridor" (intersections along one street), "Zone" (intersections on
  several streets), "Single Intersection"
- analysisPeriods: list containing "AM" and/or "PM" (peak hours studied)

What the engineer said:
\"\"\"{text}\"\"\""""


_whisper = None


def transcribe(audio: bytes, suffix: str = ".webm") -> str:
    """Speech to text with open-source Whisper. Raises RuntimeError if it isn't installed."""
    global _whisper
    try:
        from faster_whisper import WhisperModel  # optional dependency, imported lazily
    except ImportError as e:
        raise RuntimeError("Voice input isn't installed on this server "
                           "(pip install -r backend/requirements-voice.txt).") from e
    if _whisper is None:
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    # faster-whisper reads a path (decoded with its bundled ffmpeg); delete it right after.
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio)
        path = tmp.name
    try:
        segments, _ = _whisper.transcribe(
            path, vad_filter=True,
            # Vocabulary hint so traffic words aren't misheard.
            initial_prompt="Traffic study setup: corridor, intersection, AM peak, PM peak, "
                           "existing conditions, opening year, cumulative, Caltrans, county.")
        return " ".join(s.text.strip() for s in segments).strip()
    finally:
        os.unlink(path)


def _ask(text: str, today: str) -> str:
    """One chat call to the configured open model. Returns the raw reply text."""
    headers = {"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {}
    r = httpx.post(f"{LLM_URL}/chat/completions", headers=headers, timeout=60, json={
        "model": LLM_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": PROMPT.format(today=today, text=text)}],
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _json_object(reply: str) -> dict:
    """The first {...} in the reply; small models sometimes wrap JSON in prose or fences."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def clean(raw: dict) -> dict:
    """Keep only well-formed values for known fields. This is the trust boundary:
    whatever the model returns, only these shapes can reach the form."""
    out = {}
    for k in TEXT_FIELDS:
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and k == "projectNumber":
            v = str(v)
        if isinstance(v, str):
            v = " ".join(v.split())
            if v and v.lower() not in ("null", "none", "n/a", "unknown") and len(v) <= MAX_TEXT:
                out[k] = v
    for k in ("analyst", "checker"):  # initials: letters only, 1-4 of them
        if k in out:
            ini = re.sub(r"[^A-Za-z]", "", out[k]).upper()
            if 1 <= len(ini) <= 4:
                out[k] = ini
            else:
                del out[k]
    d = raw.get("date")
    if isinstance(d, str):
        try:
            out["date"] = dt.date.fromisoformat(d.strip()).isoformat()
        except ValueError:
            pass
    t = raw.get("projectType")
    if isinstance(t, str):
        match = [p for p in PROJECT_TYPES if p.lower() == t.strip().lower()]
        if match:
            out["projectType"] = match[0]
    p = raw.get("analysisPeriods")
    if isinstance(p, str):
        p = [p]
    if isinstance(p, list):
        said = {str(x).strip().upper().replace(" PEAK", "") for x in p}
        periods = [x for x in PERIODS if x in said]
        if periods:
            out["analysisPeriods"] = periods
    return out


def understand(text: str, today: str | None = None) -> dict:
    """Spoken (or typed) description -> cleaned setup fields."""
    text = (text or "").strip()
    if not text:
        return {}
    out = clean(_json_object(_ask(text[:4000], today or dt.date.today().isoformat())))
    # Small models like to fill the date with today when none was said; keep it only if one was.
    if "date" in out and not DATE_WORDS.search(text):
        del out["date"]
    return out
