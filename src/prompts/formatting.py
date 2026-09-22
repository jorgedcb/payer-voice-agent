"""Spoken identity, procedure-code and service formatting for the prompts."""

NATO_ALPHABET: dict[str, str] = {
    "a": "alpha",
    "b": "bravo",
    "c": "charlie",
    "d": "delta",
    "e": "echo",
    "f": "foxtrot",
    "g": "golf",
    "h": "hotel",
    "i": "india",
    "j": "juliett",
    "k": "kilo",
    "l": "lima",
    "m": "mike",
    "n": "november",
    "o": "oscar",
    "p": "papa",
    "q": "quebec",
    "r": "romeo",
    "s": "sierra",
    "t": "tango",
    "u": "uniform",
    "v": "victor",
    "w": "whiskey",
    "x": "xray",
    "y": "yankee",
    "z": "zulu",
}


def nato_spell(word: str) -> str:
    """ "c" -> "C as in charlie"; "abc" -> "A as in alpha, B as in bravo, C as in charlie".

    The letter is upper-cased so the voice reads it as a letter: lowercase "i"
    and "a" are words to a TTS model. NATO words make the letters unambiguous.
    """
    parts = [
        f"{ch.upper()} as in {NATO_ALPHABET[ch]}"
        for ch in word.lower()
        if ch in NATO_ALPHABET
    ]
    return ", ".join(parts)


PAUSE = " ... "
"""The rest between two things a listener writes down separately: one code and the
next, the groups of a long number, the two ends of a range. The voice holds on a
spaced ellipsis; a comma is only a beat, and a period ends the sentence."""

_DIGIT_WORDS = "zero one two three four five six seven eight nine".split()


def speak_digits(s: str) -> str:
    """ "97151" -> "nine, seven, one, five, one" (non-digits dropped).

    Digits are written as words, one per beat: the voice does not expand figures
    reliably, and a written "97151" comes out as a number.
    """
    return ", ".join(_DIGIT_WORDS[int(ch)] for ch in s if ch.isdecimal())


def speak_phone(s: str) -> str:
    """ "+12025550123" -> "two, zero, two ... five, five, five ... zero, one, two, three".

    Digit words with commas, and PAUSE between the area code, the exchange and the
    line, so the voice rests where a person writing it down does. A leading US
    country code is dropped; anything that is not a 10-digit number is read as one
    group.
    """
    digits = [ch for ch in s if ch.isdecimal()]
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return _speak_three_three_four(digits)


def speak_npi(s: str) -> str:
    """ "1234567893" -> "one, two, three ... four, five, six ... seven, eight, nine, three".

    Each digit is a word, with commas within groups of three, three and four,
    and a spaced ellipsis between groups so the voice rests where a person
    writing it down does. Anything that is not ten digits is read as one
    comma-separated group.
    """
    return _speak_three_three_four([ch for ch in s if ch.isdecimal()])


def _speak_grouped(digits: list[str], sizes: tuple[int, ...]) -> str:
    """Digit words in groups of the given sizes, joined by PAUSE, when the count
    matches; any other count as one comma-separated group."""
    if len(digits) != sum(sizes):
        return speak_digits("".join(digits))
    groups, start = [], 0
    for size in sizes:
        groups.append("".join(digits[start : start + size]))
        start += size
    return PAUSE.join(speak_digits(group) for group in groups)


def _speak_three_three_four(digits: list[str]) -> str:
    return _speak_grouped(digits, (3, 3, 4))


def speak_tax_id(s: str) -> str:
    """ "12-3456789" -> "one, two ... three, four, five, six, seven, eight, nine".

    An EIN is written 2-7, so the rest falls where the hyphen is.
    """
    return _speak_grouped([ch for ch in s if ch.isdecimal()], (2, 7))


_MIN_RANGE_RUN = 3


def speak_cpt_codes(codes: list[str]) -> str:
    """Speak CPT codes the way a benefits caller does.

    Collapse ascending runs of 3+ wholly numeric codes into ranges. Keep
    alphanumeric codes intact and speak them individually, including letters.
    Each code is digit words with commas; codes, and the two ends of a range, are
    separated by PAUSE so the representative can write one down before the next.
    """
    unique = list(dict.fromkeys(code for code in codes if code))
    if not unique:
        return ""

    first_seen = {code: index for index, code in enumerate(unique)}
    ordered = sorted((code for code in unique if code.isdecimal()), key=int)

    runs: list[list[str]] = []
    for code in ordered:
        if runs and int(code) == int(runs[-1][-1]) + 1:
            runs[-1].append(code)
        else:
            runs.append([code])
    runs.extend([code] for code in unique if not code.isdecimal())

    # Detect runs by value, but present them in the order the caller listed them.
    # Sorting the output numerically would read "840 ... 97151 to 97158" for a
    # spec written 97151-97158 then 840, pushing the primary therapy codes behind
    # a modifier.
    runs.sort(key=lambda run: min(first_seen[code] for code in run))

    spoken: list[str] = []
    collapsed_any = False
    for run in runs:
        if len(run) >= _MIN_RANGE_RUN:
            spoken.append(f"{speak_digits(run[0])}{PAUSE}to {speak_digits(run[-1])}")
            collapsed_any = True
        else:
            # A pair reads worse as a range than as two codes.
            spoken.extend(speak_code(code) for code in run)
    if collapsed_any and len(spoken) > 1 and len(runs[-1]) < _MIN_RANGE_RUN:
        # After a range, "and" marks a trailing single code as its own item, not
        # the end of the range it follows. Ranges and flat lists need no prefix.
        spoken[-1] = f"and {spoken[-1]}"
    return PAUSE.join(spoken)


def speak_code(code: str) -> str:
    """ "F84.0" -> "F as in foxtrot, eight, four, point, zero"."""
    parts: list[str] = []
    for ch in code.lower():
        if ch in NATO_ALPHABET:
            parts.append(f"{ch.upper()} as in {NATO_ALPHABET[ch]}")
        elif ch.isdecimal():
            parts.append(_DIGIT_WORDS[int(ch)])
        elif ch == ".":
            parts.append("point")
    return ", ".join(parts)


def speak_places(locations: list[str]) -> str:
    """Read places in a fixed spoken order; an empty list falls back to the office."""
    places = [
        place
        for place in ("office", "home", "school", "telehealth", "daycare", "community")
        if place in locations
    ] or ["office"]
    if len(places) == 1:
        return places[0]
    if len(places) == 2:
        return " and ".join(places)
    return ", ".join(places[:-1]) + ", and " + places[-1]


_SERVICE_LABELS = {
    "mental_health": "ABA therapy",
    "mental_health_provider_outpatient": "ABA therapy",
    "physical_therapy": "physical therapy",
    "occupational_therapy": "occupational therapy",
    "speech_therapy": "speech therapy",
}


def service_label(service_type: str) -> str:
    """Say the service's name without changing its exact backend identity."""
    return _SERVICE_LABELS[service_type]
