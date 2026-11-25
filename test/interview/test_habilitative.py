"""Speech and occupational therapy for a developmental diagnosis is habilitative.

Payers file therapy for a developmental diagnosis (ICD-10 F80-F89, autism among
them) under habilitative benefits, whatever the payer, and representatives ask
"habilitative or rehabilitative?" to pick the benefit they read. On real F84.0
speech therapy calls the agent answered "rehabilitative", or agreed it was "just
general speech therapy". What changes under the diagnosis is mostly the visit
limits: they can stop applying, or the diagnosis can add restrictions.

Identifiers are synthetic.
"""

import re

import pytest
from support.calls import (
    AETNA_PT_CLINIC,
    AETNA_VISIT_LIMITS_CALL,
    AETNA_VISIT_LIMITS_PATIENT,
    replay,
    said,
    show_response,
)

SPEECH = {
    "service_type": "speech_therapy",
    "cpt_codes": ["92507", "92523"],
    "service_locations": ["office"],
}

# Before the representative asks which benefit: the agent has asked for speech
# therapy benefits and read its codes, as on the calls that went wrong.
CODES_READ = [
    ("user", "Thank you for calling. My name is Kim. How can I help you today?"),
    ("assistant", "Hi Kim, I'm calling to check benefits for one of your members."),
    ("user", "Sure. What benefits are you checking?"),
    (
        "assistant",
        "I'm looking for speech therapy benefits for in network and out of network, "
        "and I have CPT codes to check.",
    ),
    ("user", "Go ahead with the codes."),
    (
        "assistant",
        "Nine, two, five, zero, seven, and nine, two, five, two, three, in the "
        "office. Did you get those?",
    ),
    ("user", "Yes, I got them. Give me a moment."),
]

# "habilitative" on its own, not inside "rehabilitative".
HABILITATIVE = re.compile(r"(?<![a-z])habilitat", re.IGNORECASE)
REHABILITATIVE = re.compile(r"rehabilitat", re.IGNORECASE)
# Finds the turn that brings up the diagnosis; a judge decides what it asked.
ABOUT_THE_DIAGNOSIS = re.compile(r"diagnos|F as in|F eight|F84", re.IGNORECASE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "Is this for habilitative or rehabilitative?",
        "Is this for rehabilitative speech therapy?",
        "And these codes are not related to habilitative, only for general speech "
        "therapy?",
    ],
    ids=["either", "rehab-only", "general"],
)
@pytest.mark.parametrize("diagnosis", ["F84.0", "F80.9"])
async def test_developmental_diagnosis_is_habilitative(
    session, start_assistant, judge_llm, question, diagnosis
) -> None:
    agent = await start_assistant(**SPEECH, dx_code=diagnosis)
    await replay(agent, CODES_READ)

    result = await session.run(user_input=question)
    show_response(question, result)

    answer = said(result)
    assert HABILITATIVE.search(answer), f"did not say habilitative: {answer!r}"
    # "not habilitative" says the word too.
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            judge_llm,
            intent=(
                "Answers that the services are habilitative; a bare 'Habilitative.' "
                "is enough. It does not say or agree that they are rehabilitative or "
                "just general therapy."
            ),
        )
    )


@pytest.mark.asyncio
# R13.11 is dysphagia, a clinic's default for speech therapy.
@pytest.mark.parametrize("diagnosis", ["", "R13.11"], ids=["no-code", "other-code"])
async def test_general_therapy_stays_rehabilitative(
    session, start_assistant, judge_llm, diagnosis
) -> None:
    agent = await start_assistant(**SPEECH, dx_code=diagnosis)
    await replay(agent, CODES_READ)

    question = "Is this for habilitative or rehabilitative?"
    result = await session.run(user_input=question)
    show_response(question, result)

    answer = said(result)
    assert REHABILITATIVE.search(answer), f"did not say rehabilitative: {answer!r}"
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            judge_llm,
            intent=(
                "Answers that the services are rehabilitative. It does not say they "
                "are habilitative."
            ),
        )
    )


@pytest.mark.asyncio
async def test_benefits_are_asked_as_habilitative(session, start_assistant) -> None:
    agent = await start_assistant(**SPEECH, dx_code="F84.0")
    await replay(
        agent,
        [
            (
                "user",
                "Thank you for calling. My name is Kim. How can I help you today?",
            ),
            (
                "assistant",
                "Hi Kim, I'm calling to check benefits for one of your members.",
            ),
        ],
    )

    question = "Sure. What benefits are you checking?"
    result = await session.run(user_input=question)
    show_response(question, result)

    answer = said(result)
    assert HABILITATIVE.search(answer), f"asked for plain speech therapy: {answer!r}"


def as_habilitative_speech(history, *, volunteered_limit=False):
    """An Aetna physical therapy call from 2026-10-13, told as the speech therapy
    call this rule is about, with the agent asking the way the prompt now asks.
    With `volunteered_limit`, the benefits read-out names a visit limit."""
    told = []
    for kind, value in history:
        if isinstance(value, str):
            value = (
                value.replace("general physical therapy", "habilitative speech therapy")
                .replace(
                    "physical therapy benefits", "habilitative speech therapy benefits"
                )
                .replace("physical therapy", "speech therapy")
            )
            if volunteered_limit and value == "No visit, Nemet.":
                value = (
                    "Thirty visits per calendar year, in and out of network combined."
                )
        told.append((kind, value))
    return told


# The clinic's own codes, as the replayed call read them out.
SPEECH_CLINIC = {
    **AETNA_PT_CLINIC,
    "service_type": "speech_therapy",
    "dx_code": "F84.0",
}
LIMIT_UNDER_THE_DIAGNOSIS = (
    "Asks whether visit limits apply, or are different, under the member's diagnosis "
    "code, or whether that diagnosis adds any restriction."
)


async def asks_about_the_diagnosis(session, judge_llm, lines) -> None:
    """The diagnosis question may come right away or after the limit's own
    questions; the turns given cover both orders."""
    asked = []
    for line in lines:
        result = await session.run(user_input=line)
        show_response(line, result)
        asked.append(said(result))
        if ABOUT_THE_DIAGNOSIS.search(asked[-1]):
            await (
                result.expect.next_event()
                .is_message(role="assistant")
                .judge(judge_llm, intent=LIMIT_UNDER_THE_DIAGNOSIS)
            )
            return
    pytest.fail(f"never asked how the diagnosis changes the visit limits: {asked!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "follow_up"),
    [
        (
            "Yes, thirty visits per calendar year, in and out of network combined.",
            "It's a hard maximum, and none have been used.",
        ),
        ("No, there are no visit limits.", "Okay."),
    ],
    ids=["a-limit", "no-limit"],
)
async def test_visit_limits_are_checked_against_the_diagnosis(
    session, start_assistant, judge_llm, answer, follow_up
) -> None:
    agent = await start_assistant(**SPEECH_CLINIC, **AETNA_VISIT_LIMITS_PATIENT)
    # Up to the agent asking about visit limits.
    await replay(agent, as_habilitative_speech(AETNA_VISIT_LIMITS_CALL[:-2]))

    await asks_about_the_diagnosis(session, judge_llm, (answer, follow_up))


@pytest.mark.asyncio
async def test_a_volunteered_limit_is_checked_against_the_diagnosis(
    session, start_assistant, judge_llm
) -> None:
    """The read-out named the limit, so the agent never asks for one itself."""
    agent = await start_assistant(**SPEECH_CLINIC, **AETNA_VISIT_LIMITS_PATIENT)
    history = as_habilitative_speech(AETNA_VISIT_LIMITS_CALL, volunteered_limit=True)
    # Up to the out-of-state answer, the turn before visit limits come up.
    *before, (_, out_of_state) = history[:-3]

    await replay(agent, before)
    # The call's answer was "No. Uh, yes.": an agent may ask which it is first.
    await asks_about_the_diagnosis(
        session,
        judge_llm,
        (
            out_of_state,
            "Yes, out of state is covered.",
            "It's a hard maximum, and none have been used.",
            "Okay.",
        ),
    )
