"""
Správa konverzace – Haiku 4.5 pro hovor (levné, dostačující pro strukturovaný scénář).
System prompt se načítá ze souboru prompts/system_prompt.txt – lze upravovat
přes dashboard bez zásahu do kódu.
"""

import json
import re
import anthropic
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from config import settings

_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

CALL_MODEL = "claude-haiku-4-5-20251001"   # levný, dostačující pro vedení hovoru
PROMPT_PATH = Path("prompts/system_prompt.txt")

# ── Outcomes ──────────────────────────────────────────────────────────────

class Outcome:
    ONGOING = "ongoing"
    MEETING = "meeting_agreed"
    NOT_INTERESTED = "not_interested"
    CALLBACK = "callback_requested"
    SEND_EMAIL = "send_email"
    VOICEMAIL = "voicemail"
    NO_ANSWER = "no_answer"
    WRONG_PERSON = "wrong_person"

TERMINAL = {Outcome.MEETING, Outcome.NOT_INTERESTED, Outcome.SEND_EMAIL, Outcome.VOICEMAIL}

# ── Products ──────────────────────────────────────────────────────────────

PRODUCTS = {
    "lighting": {
        "label": "Solární osvětlení",
        "pitch_short": "solárního veřejného osvětlení",
    },
    "charging": {
        "label": "Solární nabíjecí stanice",
        "pitch_short": "solárních nabíjecích stanic pro e-kola a elektromobily",
    },
}

# ── Prompt loading (cached, with hot-reload check) ─────────────────────────

_prompt_cache = {"text": None, "mtime": None}


def get_prompt_template() -> str:
    """Načte system prompt ze souboru; znovu načte při změně souboru."""
    mtime = PROMPT_PATH.stat().st_mtime if PROMPT_PATH.exists() else None
    if _prompt_cache["text"] is None or _prompt_cache["mtime"] != mtime:
        _prompt_cache["text"] = PROMPT_PATH.read_text(encoding="utf-8")
        _prompt_cache["mtime"] = mtime
    return _prompt_cache["text"]


def save_prompt_template(new_text: str) -> None:
    """Uloží upravený prompt (z dashboardu) zpět do souboru."""
    PROMPT_PATH.parent.mkdir(exist_ok=True)
    PROMPT_PATH.write_text(new_text, encoding="utf-8")
    _prompt_cache["text"] = None  # vynutí reload


# ── Conversation state ────────────────────────────────────────────────────

@dataclass
class CallSession:
    municipality_row: dict        # celý řádek z databáze (id, name, phone, kraj, ...)
    product: str
    history: list = field(default_factory=list)
    outcome: str = Outcome.ONGOING
    mayor_name: Optional[str] = None
    meeting_date: Optional[str] = None
    meeting_time: Optional[str] = None
    meeting_place: Optional[str] = None
    callback_date: Optional[str] = None
    notes: str = ""
    transcript: list = field(default_factory=list)   # [(speaker, text), ...] pro Excel log
    turns: int = 0
    no_input_count: int = 0


# ── Core functions ────────────────────────────────────────────────────────

def opening_speech(session: CallSession) -> str:
    """První slova při zvednutí hovoru (na úřad, ne přímo starostovi)."""
    prod = PRODUCTS.get(session.product, PRODUCTS["lighting"])["pitch_short"]
    speech = (
        f"Dobrý den, Jana Nováková z firmy ADALUX, jsme výrobce {prod} z Ostravy. "
        f"Ráda bych krátce představila naši nabídku panu starostovi nebo paní starostce, "
        f"máte chvilku mě s ním nebo s ní spojit?"
    )
    session.transcript.append(("agent", speech))
    return speech


def generate_response(session: CallSession, user_speech: str) -> dict:
    """
    Vygeneruje další repliku agenta pomocí Haiku.
    BLOCKING – volat přes asyncio.to_thread v async kontextu.
    """
    row = session.municipality_row
    prod = PRODUCTS.get(session.product, PRODUCTS["lighting"])

    system = get_prompt_template().replace("{municipality}", row.get("name", ""))
    system = system.replace("{product_label}", prod["label"])

    messages = list(session.history)
    messages.append({"role": "user", "content": user_speech})
    session.transcript.append(("mayor_or_staff", user_speech))

    response = _client.messages.create(
        model=CALL_MODEL,
        max_tokens=400,
        system=system,
        messages=messages,
    )

    raw = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
    clean = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", clean)
        data = json.loads(m.group(0)) if m else {"speech": raw, "outcome": "ongoing"}

    session.history.append({"role": "user", "content": user_speech})
    session.history.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)})
    session.transcript.append(("agent", data.get("speech", "")))

    session.outcome = data.get("outcome", Outcome.ONGOING)
    session.turns += 1

    if data.get("mayor_name"):
        session.mayor_name = data["mayor_name"]
    if data.get("meeting_date"):
        session.meeting_date = data["meeting_date"]
    if data.get("meeting_time"):
        session.meeting_time = data["meeting_time"]
    if data.get("meeting_place"):
        session.meeting_place = data["meeting_place"]
    if data.get("callback_date"):
        session.callback_date = data["callback_date"]
    if data.get("notes"):
        session.notes = data["notes"]

    return data


def full_transcript_text(session: CallSession) -> str:
    """Spojí celý přepis hovoru do jednoho textu pro Excel log."""
    labels = {"agent": "ADALUX", "mayor_or_staff": "OBEC"}
    return "\n".join(f"{labels.get(s, s)}: {t}" for s, t in session.transcript)
