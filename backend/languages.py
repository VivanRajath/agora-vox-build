"""Which language the user is speaking, from the script of what speech recognition wrote down.

ARES listens for several languages at once (ASR_LANGUAGE="en-IN,kn-IN,hi-IN") and writes each in its
own script, so the script tells us the language. Vox then replies in it: Murf's multilingual Indian
voices (Pooja by default) read Kannada, Hindi, Tamil and Telugu text even when set to English.
"""
import re

from . import llm

# Unicode blocks for Indian scripts. Hindi and Marathi share Devanagari; Hindi is the likelier one.
SCRIPTS = [
    ("kn", "Kannada", re.compile(r"[ಀ-೿]")),
    ("hi", "Hindi", re.compile(r"[ऀ-ॿ]")),
    ("ta", "Tamil", re.compile(r"[஀-௿]")),
    ("te", "Telugu", re.compile(r"[ఀ-౿]")),
    ("ml", "Malayalam", re.compile(r"[ഀ-ൿ]")),
    ("bn", "Bengali", re.compile(r"[ঀ-৿]")),
    ("gu", "Gujarati", re.compile(r"[઀-૿]")),
    ("pa", "Punjabi", re.compile(r"[਀-੿]")),
]
NAMES = {code: name for code, name, _ in SCRIPTS} | {"en": "English"}

# The language each project's user spoke last, so announcements made outside a turn match it.
_last: dict[str, str] = {}


def detect(text: str) -> str:
    """Language code of the text: the Indian script it uses most, else English."""
    counts = [(len(rx.findall(text or "")), code) for code, _, rx in SCRIPTS]
    n, code = max(counts)
    letters = len(re.findall(r"[A-Za-z]", text or ""))
    return code if n and n >= letters * 0.3 else "en"


def remember(project_id: str, code: str) -> None:
    _last[project_id] = code


def current(project_id: str) -> str:
    return _last.get(project_id, "en")


def reply_rule(code: str) -> str:
    """The last line of Vox's system prompt for this turn."""
    if code == "en":
        return ""
    name = NAMES[code]
    return (
        f"\n\nThe user is speaking {name}. Reply ONLY in {name}, written in {name} script, in short spoken sentences. "
        "Keep brand names, film titles and technical words as they are. Tool arguments stay in English: translate the "
        f"user's request into English for them, and transliterate names (e.g. a shop name) into English letters."
    )


async def translate(text: str, code: str) -> str:
    """Server-written lines (announcements, check-backs) in the user's language; the original on failure."""
    if code == "en" or not text:
        return text
    name = NAMES[code]
    system = (
        f"Translate this spoken message from a website-building voice assistant into natural, simple spoken {name}, "
        f"in {name} script. Keep names, brand names and web addresses as they are. Reply with the translation only."
    )
    try:
        out = (await llm.complete(system, text, "vox", 400)).strip()
        return out or text
    except llm.LLMError:
        return text
