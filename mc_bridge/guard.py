"""Checks that run in code, before and after the AI.

Customer text is DATA. It is cleaned, capped and wrapped before the model sees
it, and these rules decide on their own (no model involved) when a person
must take over: risk words, an explicit request for a human, an opt-out.
"""
from __future__ import annotations

import re
import unicodedata

ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")
CONTROL = re.compile(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F​-‏‪-‮⁦-⁩]")
INJECTION_PATTERNS = [
    r"ignore (all |any )?(the |your )?(previous|prior|above) (instructions|rules|prompt)",
    r"disregard (the |your )?(system|previous) (prompt|instructions)",
    r"you are now",
    r"system prompt",
    r"reveal (your|the) (prompt|instructions|api key|token)",
    r"act as (an? )?(admin|developer|system)",
    r"</?(customer_message|system|assistant)>",
    r"تجاهل (كل )?التعليمات",
]


def language(text: str) -> str:
    """'ar' if most words are in Arabic script, else 'en' (counted by words, so a
    mixed 'كم سعر cleaning؟' is Arabic)."""
    words = (text or "").split()
    ar = sum(1 for w in words if ARABIC.search(w))
    la = sum(1 for w in words if LATIN.search(w) and not ARABIC.search(w))
    return "ar" if ar > la else "en"


def clean(text: str, max_chars: int = 2000) -> str:
    text = unicodedata.normalize("NFC", str(text or ""))
    text = CONTROL.sub("", text)
    text = text.replace("<", "‹").replace(">", "›")  # nothing can close our tags
    return text[:max_chars].strip()


def injection_flags(text: str) -> list[str]:
    low = (text or "").lower()
    return [p for p in INJECTION_PATTERNS if re.search(p, low)]


def _contains(text: str, phrase: str) -> bool:
    low, p = text.lower(), phrase.lower()
    if ARABIC.search(p):
        return p in low
    return re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low) is not None


def risk_categories(text: str, risk_words: dict[str, list[str]]) -> list[str]:
    return sorted({cat for cat, words in risk_words.items() for w in words if _contains(text or "", w)})


def wants_human(text: str, human_words: list[str]) -> bool:
    return any(_contains(text or "", w) for w in human_words)


def is_opt_out(text: str, opt_out_words: list[str]) -> bool:
    """Only a short message that IS an opt-out counts ('stop', 'STOP please'),
    so 'how do I stop my order?' is not read as unsubscribing."""
    t = (text or "").strip().lower().rstrip(".!؟?")
    if not t:
        return False
    if len(t.split()) > 4:
        return False
    return any(_contains(t, w) for w in opt_out_words)


def is_opt_in(text: str) -> bool:
    return (text or "").strip().lower().rstrip(".!") in {"start", "unstop", "subscribe", "ابدأ"}


SECRET_PATTERNS = [re.compile(p) for p in (r"sk-ant-[A-Za-z0-9_-]{10,}", r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}",
                                            r"(?i)api[_ -]?key\s*[:=]\s*\S{8,}")]


def leaks_secret(text: str) -> bool:
    return any(p.search(text or "") for p in SECRET_PATTERNS)
