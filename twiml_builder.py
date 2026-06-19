"""
TwiML builders for Twilio voice responses.

Czech TTS: Twilio routes cs-CZ to Google Cloud TTS – quality is good.
STT: <Gather input="speech" language="cs-CZ"> uses Google STT – supports Czech.
"""

from twilio.twiml.voice_response import VoiceResponse, Gather

VOICE = "Polly.Joanna"   # fallback; cs-CZ forces Google TTS regardless
LANGUAGE = "cs-CZ"
GATHER_TIMEOUT = 8          # seconds to wait for speech to start
SPEECH_TIMEOUT = "2"        # seconds of silence = end of utterance


def gather_response(speech_text: str, action_url: str) -> str:
    """Speak text, then listen for a Czech response."""
    r = VoiceResponse()
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        timeout=GATHER_TIMEOUT,
        speech_timeout=SPEECH_TIMEOUT,
        language=LANGUAGE,
    )
    g.say(speech_text, language=LANGUAGE)
    r.append(g)
    # If no speech detected → re-trigger same action with flag
    r.redirect(action_url + "?no_input=1", method="POST")
    return str(r)


def end_call(speech_text: str) -> str:
    """Say final message and hang up."""
    r = VoiceResponse()
    r.say(speech_text, language=LANGUAGE)
    r.pause(length=1)
    r.hangup()
    return str(r)


def filler_then_redirect(filler_text: str, redirect_url: str) -> str:
    """Přehraje krátkou výplňovou frázi, pak přesměruje na skutečné zpracování.
    Maskuje latenci LLM volání - místo ticha slyší volající přirozenou odmlku."""
    r = VoiceResponse()
    r.say(filler_text, language=LANGUAGE)
    r.redirect(redirect_url, method="POST")
    return str(r)


FILLER_PHRASES = [
    "Moment, prosím.",
    "Jistě, počkejte chviličku.",
    "Rozumím, hned to bude.",
    "Dobře, moment.",
    "Ano, chviličku.",
]


def pick_filler(turn_number: int) -> str:
    """Vybere výplňovou frázi podle pořadí tahu, aby se nepoužila stejná pořád."""
    return FILLER_PHRASES[turn_number % len(FILLER_PHRASES)]


def voicemail(municipality: str, product_label: str) -> str:
    """Leave a short voicemail when answering machine detected."""
    msg = (
        f"Dobrý den, zde Jana Nováková z firmy Adalux. "
        f"Jsme český výrobce solárního osvětlení a nabíjecích stanic pro elektrokola. "
        f"Volám ohledně nabídky pro obec {municipality}. "
        f"Prosím, navštivte adalux.cz nebo nám zavolejte zpět. "
        f"Děkuji, na shledanou."
    )
    return end_call(msg)
