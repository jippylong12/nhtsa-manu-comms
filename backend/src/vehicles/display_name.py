"""How a make or model is shown to a person.

NHTSA files everything in capitals ("CHEVROLET SILVERADO EV"). The stored value never
changes; this is only what is displayed. It mirrors `frontend/src/features/vehicles/
displayName.ts` rule for rule, so an email names a car the way the app does.
"""

import re

# Trim levels and badges that are read letter by letter, so they stay in capitals.
ACRONYMS = frozenset(
    "AMG AWD CLA CLS EQA EQB EQE EQS ESV FWD GLA GLB GLC GLE GTE GTI GTO GTS HEV IROC PHEV "
    "RWD SLK SRT STI SUV TRD TTS USA".split()
)

# Names whose capitals sit somewhere a general rule cannot find.
SPECIAL = {"MCLAREN": "McLaren", "BRIGHTDROP": "BrightDrop"}

_NAMED = re.compile(r"^([A-Z]{4,})(\d+)$")
_HAS_VOWEL = re.compile(r"[AEIOU]")


def _word(token: str) -> str:
    if not token:
        return token
    upper = token.upper()
    if upper in SPECIAL:
        return SPECIAL[upper]
    if upper in ACRONYMS:
        return upper
    # MAZDA3 -> Mazda3: a real word with a number stuck on the end.
    named = _NAMED.match(upper)
    if named and _HAS_VOWEL.search(named.group(1)):
        return named.group(1)[0] + named.group(1)[1:].lower() + named.group(2)
    # A6, 4S, CT5, RAV4, 1500: anything else with a digit is a code.
    if any(ch.isdigit() for ch in upper):
        return upper
    # EV, GT, XL, S: one or two letters are read out, not pronounced.
    if len(upper) <= 2:
        return upper
    # GMC, BMW, FCCC: no vowel means nobody says it as a word.
    if not _HAS_VOWEL.search(upper):
        return upper
    return upper[0] + upper[1:].lower()


def display_name(raw: str | None) -> str:
    """Title-case a make or model: CHEVROLET -> Chevrolet, SILVERADO EV -> Silverado EV."""
    if not raw:
        return ""
    return " ".join("-".join(_word(p) for p in part.split("-")) for part in raw.split())


def display_vehicle(year: int | str, make: str | None, model: str | None) -> str:
    """"2024 Chevrolet Silverado EV" from the stored year, make and model."""
    return " ".join(p for p in (str(year), display_name(make), display_name(model)) if p)
