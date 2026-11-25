"""Answers that leave a benefit topic open, replayed into real calls.

Each test takes a recorded Aetna call up to one topic and gives an answer that
settles less than it seems to: a benefit year named as none of the kinds, a
volunteered visit limit, a copay the member is not paying right now, a
reimbursement with no basis. The agent's next question must still be
that topic's. Words the real call did not have are marked where they are added.
"""

import pytest
from support.calls import (
    AETNA_ALL_TOPICS_CALL,
    AETNA_ALL_TOPICS_OOP_CLEAR,
    AETNA_ALL_TOPICS_PATIENT,
    AETNA_EARLY_CLOSING_CALL,
    AETNA_EARLY_CLOSING_PATIENT,
    AETNA_PT_CLINIC,
    closing_started,
    edit_call,
    replay,
    show_response,
)

NOT_CLOSING = (
    " It is not the closing question about other limitations or exclusions, and "
    "it does not ask for the representative's name or a reference number."
)

# The all-topics call's out-of-network accumulators, as the representative read
# them out. "Includes all deductibles and co pays." already says the deductible
# and the copay count toward the out-of-pocket maximum, so the copay test takes
# it out.
_OON_ACCUMULATORS = (
    "the deductible is three thousand dollar, and the out of pocket max is nine "
    "thousand. Includes all deductibles and co pays. For accumulations here, the "
    "net amount for the deductible is eight hundred twelve dollar and thirty cent. "
    "And the net amount for the out of pocket is nine hundred twenty dollar and "
    "fifteen cents."
)


async def _next_turn(session, start_assistant, patient, history, answer):
    agent = await start_assistant(**(AETNA_PT_CLINIC | patient))
    await replay(agent, history)
    result = await session.run(user_input=answer)
    show_response(answer, result)
    assert not closing_started(session, result), "The topic is still open."
    return result


@pytest.mark.asyncio
async def test_benefit_year_that_names_no_kind_is_asked_again(
    session, start_assistant, judge_llm
):
    # Added: the real call's representative gave the effective date, the funding
    # and the calendar year in one answer; here they give only the date, the
    # agent asks the benefit year, and the answer names no kind of year.
    history = [
        *edit_call(
            AETNA_ALL_TOPICS_CALL,
            through="Thank you. What is the coverage effective date for this member?",
        ),
        (
            "user",
            "It started on April first, twenty twenty five, with no termination date.",
        ),
        ("assistant", "Got it. Is the plan on a calendar year or a plan year?"),
    ]
    result = await _next_turn(
        session,
        start_assistant,
        AETNA_ALL_TOPICS_PATIENT,
        history,
        "It renews every year.",
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Stays on the benefit year: asks again whether the plan runs on a "
            "calendar year, a plan or service year, or a contract year, or asks "
            "when the benefit period ends or renews. Any wording is fine, and a "
            "brief acknowledgment may come first. It does not go on to another "
            "topic, such as whether the plan is self-funded or fully funded, "
            "coordination of benefits, or out-of-state coverage." + NOT_CLOSING
        ),
    )


@pytest.mark.asyncio
async def test_volunteered_visit_limit_keeps_its_topic_open(
    session, start_assistant, judge_llm
):
    # The 2026-11-19 call: the representative volunteered "sixty visits maximum
    # per year" and that none were used with the benefits. After the out-of-state
    # answer the agent asked the closing question, and the limit's type only
    # after it.
    history = edit_call(
        AETNA_EARLY_CLOSING_CALL,
        through="Does the plan cover the member out of state?",
    )
    result = await _next_turn(
        session,
        start_assistant,
        AETNA_EARLY_CLOSING_PATIENT,
        history,
        "Let me check here. Yes.",
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Stays on the sixty-visit limit: asks whether it is a hard or a soft "
            "maximum, or another question about that limit, such as where it "
            "applies, or clarifies the representative's garbled answer about "
            "out-of-network telehealth coverage. Any wording is fine, and a brief "
            "acknowledgment may come first. It does not go on to the out-of-pocket "
            "maximum, the deductible, a medical-necessity review or reimbursement."
            + NOT_CLOSING
        ),
    )


@pytest.mark.asyncio
async def test_copay_not_paid_after_a_met_maximum_is_still_asked(
    session, start_assistant, judge_llm
):
    # Changed: both out-of-network accumulators are met, and the answer about an
    # out-of-network copay says there is none right now, without its amount. The
    # in-network copay question comes first, as the agent asks it on this call.
    history = [
        *edit_call(
            AETNA_ALL_TOPICS_CALL,
            [
                (
                    _OON_ACCUMULATORS,
                    "the deductible is three thousand dollar, and the out of pocket "
                    "max is nine thousand. For accumulations here, both have been met.",
                ),
                (
                    "Out of network it is also a ten dollar co pay.",
                    "No co pay right now, since the out of pocket is met.",
                ),
            ],
            through=(
                "Understood. Does the out-of-pocket maximum apply to these services "
                "for both in network and out of network, in the office and via "
                "telehealth?"
            ),
        ),
        ("user", AETNA_ALL_TOPICS_OOP_CLEAR),
        (
            "assistant",
            "And in network, does the ten dollar copay count toward the "
            "out-of-pocket maximum?",
        ),
    ]
    result = await _next_turn(
        session,
        start_assistant,
        AETNA_ALL_TOPICS_PATIENT,
        history,
        "Yes. In network the co pay counts toward the out of pocket.",
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Stays on the out-of-network copay: asks whether the benefit has a "
            "copay even though the member is not paying one now, or whether that "
            "copay counts toward the out-of-pocket maximum, in any wording; a "
            "brief acknowledgment may come first. Going on to the deductible, a "
            "medical-necessity review or reimbursement does not count." + NOT_CLOSING
        ),
    )


@pytest.mark.asyncio
async def test_reimbursement_with_no_basis_is_asked_again(
    session, start_assistant, judge_llm
):
    # Added after the medical-necessity answers: the representative gives the
    # out-of-network cost sharing for the rate type, and again when asked for
    # the basis.
    history = [
        *edit_call(AETNA_ALL_TOPICS_CALL, through="No, not in network either."),
        (
            "assistant",
            "Thank you. For out of network, what is the out-of-network "
            "reimbursement rate type for this plan?",
        ),
        ("user", "It's fifty percent after the deductible."),
        (
            "assistant",
            "Okay. And is that reimbursement based on UCR, Medicare rates, local "
            "allowable rates, or another methodology?",
        ),
    ]
    result = await _next_turn(
        session,
        start_assistant,
        AETNA_ALL_TOPICS_PATIENT,
        history,
        "Out of network it's paid at fifty percent after deductible, yes.",
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Asks again what the out-of-network reimbursement is based on, such "
            "as UCR, Medicare rates or local allowable rates, in any wording; it "
            "may say it did not catch that. It does not take fifty percent as the "
            "basis and move on, and it does not ask for the rate yet." + NOT_CLOSING
        ),
    )
