"""The single call specification shared by both agents.

Accepts the backend VoiceCallSpec fields used for payer calls. The backend owns
service and network policy; the worker only renders its resolved snapshot.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CallSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    payer_phone_number: str
    npi: str
    npi_type: Literal["individual", "facility"]
    tax_id: str | None = None
    facility_name: str
    facility_address: str
    callback_number: str
    caller_first_name: str
    caller_last_initial: str | None = None
    # ElevenLabs voice for this call, picked when it was triggered. None uses the
    # worker's own default (see voices.tts_chain).
    tts_voice_id: str | None = None
    member_id: str
    member_name: str
    member_dob: str
    ivr_instructions: str | None = None
    cpt_codes: list[str]
    dx_code: str
    service_locations: list[str] = Field(default_factory=list)
    service_type: Literal[
        "mental_health",
        "mental_health_provider_outpatient",
        "physical_therapy",
        "occupational_therapy",
        "speech_therapy",
    ]
    ask_visit_limits: bool
    ask_mnr: bool
    auth_required_in_network: bool | None = None
    auth_required_out_network: bool | None = None
    # Which networks' benefits the interview collects; the backend snapshots
    # the company setting at dispatch. both_networks: ask both on every call.
    # provider_network: ask network status first, collect that network only.
    # in_network_only: as provider_network, but an out-of-network provider
    # ends the benefit questions.
    network_benefit_scope: Literal[
        "both_networks", "provider_network", "in_network_only"
    ] = "both_networks"

    @field_validator("caller_last_initial")
    @classmethod
    def _one_spellable_letter(cls, value: str | None) -> str | None:
        """One Latin letter or nothing: both prompts speak it, and the interview
        spells it with the NATO alphabet, which has nothing for anything else."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z]", value):
            raise ValueError("caller_last_initial must be a single letter A-Z")
        return value


def parse_dispatch(raw: str) -> CallSpec:
    spec = CallSpec.model_validate_json(raw)
    if not spec.payer_phone_number.strip():
        raise ValueError("payer_phone_number is required")
    return spec


def load_sample_spec() -> CallSpec:
    """Load the shared console/test example independently of the working directory."""
    path = Path(__file__).resolve().parent.parent / "examples" / "call_spec.json"
    return parse_dispatch(path.read_text(encoding="utf-8"))


def sample_spec(**overrides: object) -> CallSpec:
    """The sample spec with fields replaced, validated like a real dispatch."""
    return CallSpec.model_validate(load_sample_spec().model_dump() | overrides)
