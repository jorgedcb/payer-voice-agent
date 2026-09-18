"""The agent's authored language, loaded like application templates."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo
from functools import cache
from importlib.resources import files
from dispatch import CallSpec

from jinja2 import Environment, StrictUndefined

from .formatting import nato_spell, speak_code, speak_cpt_codes, speak_digits, speak_npi, speak_phone, speak_places, speak_tax_id, service_label

END_CALL_DESCRIPTION = (
    "End the call. Call it once the closing question is answered, or when the "
    "representative says the call cannot continue, including a refusal to deal "
    "with an AI. It says goodbye for you."
)
DISCLOSURE = (
    "Asked whether you are a person or an automated system, you are a virtual "
    "assistant calling on behalf of the provider's office, trained specifically for "
    "benefits verification calls and quicker than a usual one. Say so plainly and "
    "carry on. Never a person, never a named employee."
)


# StrictUndefined: a variable with no value raises at session start rather than
# rendering empty. On this prompt an empty render is a rep hearing "The NPI number
# is ." -- a call that never starts is the better failure.
#
# trim_blocks/lstrip_blocks are deliberately OFF: the prompt hand-tunes its own
# whitespace with {%- and -%}, and turning them on would silently change spacing
# the author already controls.
_env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)

_env.filters.update(
    nato_spell=nato_spell,
    speak_code=speak_code,
    speak_cpt_codes=speak_cpt_codes,
    speak_digits=speak_digits,
    speak_npi=speak_npi,
    speak_phone=speak_phone,
    speak_tax_id=speak_tax_id,
    speak_places=speak_places,
    service_label=service_label,
)
_env.globals.update(end_call_description=END_CALL_DESCRIPTION, disclosure=DISCLOSURE)


# Names the templates receive on top of the spec's own fields. A CallSpec field
# with one of these names would shadow it at dial time; test_greeting pins it.
RESERVED_TEMPLATE_NAMES = frozenset({"call_date", "phase"})

# Which phase of the call a prompt is rendered for. A closed set because the
# templates branch on `phase == "menu"`: any other spelling silently renders the
# hold variant, which would drop the menu's own rules while its tools stay.
Phase = Literal["menu", "hold"]


@cache
def _template(name: str):
    resource = files(__package__).joinpath(f"{name}.md")
    if not resource.is_file():
        raise FileNotFoundError(f"no prompt named {name!r}")
    return _env.from_string(resource.read_text(encoding="utf-8"))


def call_date_today() -> date:
    """Today in the payers' time zone; captured once at dial so the whole call agrees."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def today_call_date(call_date: date | None = None) -> str:
    """The call's date as spoken to the representative."""
    return (call_date or call_date_today()).strftime("%m/%d/%Y")


def instructions(
    name: str, spec: CallSpec, *, call_date: date | None = None, phase: Phase = "menu"
) -> str:
    """Render backend fields directly; filters handle presentation, not call data."""
    return _template(name).render(
        **spec.model_dump(),
        call_date=today_call_date(call_date),
        phase=phase,
    )
