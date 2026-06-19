"""
Creates Google Calendar events when a meeting is agreed during a call.
Uses a Service Account – share your calendar with the SA email.
"""

import json
from datetime import datetime, timedelta
from config import settings

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    GCAL_AVAILABLE = True
except ImportError:
    GCAL_AVAILABLE = False


def _service():
    if not GCAL_AVAILABLE:
        raise RuntimeError("google-api-python-client not installed")
    if not settings.GOOGLE_CALENDAR_CREDENTIALS:
        raise RuntimeError("GOOGLE_CALENDAR_CREDENTIALS not set")
    creds_data = json.loads(settings.GOOGLE_CALENDAR_CREDENTIALS)
    creds = Credentials.from_service_account_info(
        creds_data,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds)


def create_meeting(
    contact: dict,
    product_label: str,
    meeting_date: str,   # YYYY-MM-DD
    meeting_time: str,   # HH:MM
    meeting_place: str = None,
) -> str:
    """Create a 60-minute event and return its HTML link."""
    svc = _service()

    start = datetime.strptime(f"{meeting_date} {meeting_time}", "%Y-%m-%d %H:%M")
    end = start + timedelta(hours=1)
    place = meeting_place or f"Obecní úřad {contact.get('municipality', '')}"

    event = {
        "summary": f"ADALUX – {product_label} – {contact.get('municipality', '')}",
        "location": place,
        "description": (
            f"Obchodní schůzka s {contact.get('name', '')} ({contact.get('title', '')}).\n"
            f"Produkt: {product_label}\n"
            f"Tel: {contact.get('phone', '')}  |  Email: {contact.get('email', '')}\n"
            f"Domluveno telefonicky agentem ADALUX."
        ),
        "start": {"dateTime": start.isoformat(), "timeZone": "Europe/Prague"},
        "end":   {"dateTime": end.isoformat(),   "timeZone": "Europe/Prague"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email",  "minutes": 24 * 60},
                {"method": "popup",  "minutes": 60},
            ],
        },
    }

    result = svc.events().insert(calendarId=settings.CALENDAR_ID, body=event).execute()
    return result.get("htmlLink", "")
