"""The backend wire payload must reach the agents without changing its meaning."""

import json
from datetime import date
from pathlib import Path

import pytest

import prompts
from dispatch import load_sample_spec


def payload(**changes):
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "call_spec.json"
    data = json.loads(sample_path.read_text(encoding="utf-8"))
    return data | changes


def prepare(data):
    import dispatch

    return dispatch.parse_dispatch(json.dumps(data))


async def test_backend_payload_renders_both_agents():
    spec = prepare(payload())
    nav = prompts.instructions("navigator", spec)
    interview = prompts.instructions("verification", spec, call_date=date(2026, 9, 9))
    assert "W123456789" in nav
    assert "1990-01-01" in nav
    assert (
        "W as in whiskey, one, two, three, four, five, six, seven, eight, nine"
        in interview
    )
    assert "office and home" in interview
    # The call date is spoken only by the reference task, when a representative
    # says the reference is their name and the date.
    assert "09/09/2026" not in interview
    closing = prompts.instructions("call_reference", spec, call_date=date(2026, 9, 9))
    assert "09/09/2026" in closing
    assert spec.member_id == "W123456789"
    assert spec.member_dob == "1990-01-01"


@pytest.mark.parametrize(
    "scope,network_first,both,stop_when_oon",
    [
        ("both_networks", False, True, False),
        ("provider_network", True, False, False),
        ("in_network_only", True, False, True),
    ],
)
def test_backend_network_scope_keeps_its_meaning(
    scope, network_first, both, stop_when_oon
):
    spec = prepare(payload(network_benefit_scope=scope))
    rendered = prompts.instructions("verification", spec)
    first_topic = (
        "1. Provider network status"
        if network_first
        else "1. Benefits for physical therapy"
    )
    assert first_topic in rendered
    assert ("both networks' benefits" in rendered) is both
    assert ("in network and out of network" in rendered) is both
    stop = "Never ask a benefit, limit, cost-share or authorization question after that answer."
    assert (stop in rendered) is stop_when_oon
    # The OON reimbursement topic asks about an out-of-network provider's
    # claims, which in_network_only never collects.
    assert ("16. Out-of-network reimbursement" in rendered) is (not stop_when_oon)


def test_omitting_the_scope_renders_the_legacy_both_networks_script():
    rendered = prompts.instructions("verification", prepare(payload()))
    assert "1. Benefits for physical therapy" in rendered
    assert "both networks' benefits" in rendered


def test_an_unknown_scope_is_rejected():
    with pytest.raises(ValueError):
        prepare(payload(network_benefit_scope="everything"))


@pytest.mark.parametrize(
    "flag", ["require_both_network_benefits", "collect_oon_benefits"]
)
@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("scope", [None, "in_network_only"])
def test_dispatch_rejects_retired_network_flags(flag, value, scope):
    data = payload(**{flag: value})
    if scope is not None:
        data["network_benefit_scope"] = scope
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        prepare(data)


def test_omitted_defaults_and_explicit_false_are_distinct():
    spec = prepare(payload(auth_required_in_network=False))
    rendered = prompts.instructions("verification", spec)
    assert "Ask whether prior authorization is required in network" not in rendered
    assert "Ask whether prior authorization is required out of network" in rendered
    assert "NOTES ABOUT THIS PAYER" not in prompts.instructions("navigator", spec)


@pytest.mark.parametrize("data", [{}, [], {"first": "Old prompt payload"}])
def test_invalid_payload_is_not_replaced_by_sample_data(data):
    with pytest.raises(ValueError):
        prepare(data)


def test_missing_diagnosis_does_not_invent_a_code():
    spec = prepare(payload(dx_code=""))
    rendered = prompts.instructions("verification", spec)
    assert "The verification concerns general benefits." in rendered


async def test_handoff_uses_the_same_spec_with_phase_specific_formatting():
    from unittest.mock import Mock

    from livekit.agents import llm

    from navigator import NavigatorAgent

    spec = prepare(payload())
    navigator = NavigatorAgent(spec=spec, llm_model=Mock(spec=llm.LLM))
    interview = await navigator.representative_answered(opening="Hi, this is Greta.")
    assert "W123456789" in navigator.instructions
    assert (
        "W as in whiskey, one, two, three, four, five, six, seven, eight, nine"
        in interview.instructions
    )


@pytest.mark.parametrize("raw", ["", "not json", "{}"])
async def test_real_job_rejects_missing_metadata_before_starting_a_call(raw):
    from types import SimpleNamespace

    from agent import entrypoint

    ctx = SimpleNamespace(is_fake_job=lambda: False, job=SimpleNamespace(metadata=raw))
    with pytest.raises(ValueError):
        await entrypoint(ctx)


@pytest.mark.parametrize(
    "service_type,label",
    [
        ("mental_health", "ABA therapy"),
        ("mental_health_provider_outpatient", "ABA therapy"),
        ("physical_therapy", "physical therapy"),
        ("occupational_therapy", "occupational therapy"),
        ("speech_therapy", "speech therapy"),
    ],
)
def test_both_prompts_derive_the_service_label(service_type, label):
    spec = prepare(payload(service_type=service_type))
    assert f"benefits and eligibility questions for {label}." in prompts.instructions(
        "navigator", spec
    )
    assert f"Benefits for {label}" in prompts.instructions("verification", spec)


def test_dispatch_cannot_override_the_service_label():
    with pytest.raises(ValueError):
        prepare(payload(spoken_label="ABA therapy"))


@pytest.mark.parametrize(
    "service,diagnosis,ask_limits,expected",
    [
        ("occupational_therapy", "F84.0", True, True),
        ("speech_therapy", "F84.0", True, True),
        ("physical_therapy", "F84.0", True, False),
        ("mental_health", "F84.0", True, False),
        ("mental_health_provider_outpatient", "F84.0", True, False),
        ("speech_therapy", "F80.9", True, False),
        ("occupational_therapy", "", True, False),
        ("speech_therapy", "F84.0", False, False),
    ],
)
def test_visit_limit_followup_is_derived_from_call_facts(
    service, diagnosis, ask_limits, expected
):
    data = payload(service_type=service, dx_code=diagnosis, ask_visit_limits=ask_limits)
    rendered = prompts.instructions("verification", prepare(data))
    assert ("ask whether it still applies under F84.0" in rendered) is expected


def test_sample_loads_from_another_directory(monkeypatch, tmp_path):
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "call_spec.json"
    expected = prepare(json.loads(sample_path.read_text()))
    monkeypatch.chdir(tmp_path)

    assert load_sample_spec() == expected


@pytest.mark.parametrize(
    "codes,spoken",
    [
        (
            ["0362T", "0373T"],
            "zero, three, six, two, T as in tango ... zero, three, seven, three, T as in tango",
        ),
        (
            ["0362T", "0363T", "0364T"],
            "zero, three, six, two, T as in tango ... zero, three, six, three, T as in tango"
            " ... zero, three, six, four, T as in tango",
        ),
        (
            ["0362", "0362T", "H0031"],
            "zero, three, six, two ... zero, three, six, two, T as in tango"
            " ... H as in hotel, zero, zero, three, one",
        ),
        (
            ["0362T", "97153", "97151", "97152", "0373T"],
            "zero, three, six, two, T as in tango ... nine, seven, one, five, one ... to "
            "nine, seven, one, five, three ... and zero, three, seven, three, T as in tango",
        ),
        (
            ["97151", "97152", "97153", "840"],
            "nine, seven, one, five, one ... to nine, seven, one, five, three ... and eight, four, zero",
        ),
    ],
)
def test_interview_preserves_complete_procedure_codes(codes, spoken):
    spec = prepare(payload(cpt_codes=codes))
    rendered = prompts.instructions("verification", spec)
    assert f"The CPT codes are, {spoken}. The places of service" in rendered
    assert spec.cpt_codes == codes
