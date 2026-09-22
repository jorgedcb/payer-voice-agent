"""How the caller sounds, in words.

Representatives decide the caller is an AI from stiff, formulaic wording, not
from the voice, and in about a hundred calls a representative who decided that
never went on to help. Each turn here is replayed from a real call, with
synthetic identifiers, at the moment before a representative asked whether they
were talking to an AI: the line the agent says next is the one under test.

Judges check the register and the meaning, not particular words; the few
substring checks are for the exact phrases that got calls flagged.
"""

import pytest
from support.calls import replay, show_response

UHC_PICKUP = [
    (
        "user",
        "Thank you for calling United Healthcare. My name is Austin, your provider "
        "service advocate. May I have your name and your last name initial?",
    ),
    ("assistant", "Hi Austin, this is Annie, last initial S as in sierra."),
]


async def test_small_talk_gets_a_plain_reply(session, start_assistant, llm) -> None:
    # UHC call [call ID removed], 2026-09-22. "I'm doing well, thank you. I appreciate
    # your help." was followed by "Are you a virtual assistant AI? or a live human."
    agent = await start_assistant(caller_first_name="Annie", caller_last_initial="S")
    await replay(agent, UHC_PICKUP)

    question = "Thank you very much, Annie. How are you doing today?"
    result = await session.run(user_input=question)
    show_response(question, result)

    spoken = result.expect.next_event().is_message(role="assistant")
    text = spoken.event().item.text_content.lower()
    assert "appreciate" not in text, text
    await spoken.judge(
        llm,
        intent=(
            "Answers 'How are you doing today?' the way a person on a work call does: "
            "a few casual words such as 'Good, thanks, how about you?' or 'Doing good, "
            "thanks.' It may go on to say what it is calling about. It does not use "
            "formal courtesies such as 'I appreciate your help' or 'Thank you for "
            "asking', and it does not read like a script."
        ),
    )
    result.expect.no_more_events()


PT_VERIFIED = [
    ("user", "Thanks for calling. This is Morgan. Can I have your name?"),
    ("assistant", "Hi Morgan, this is Annie, last initial S as in sierra."),
    ("user", "Thanks Annie. And what's a good callback number?"),
    ("assistant", "Two, zero, two ... five, five, five ... zero, one, two, three."),
    ("user", "And the member's ID?"),
    (
        "assistant",
        "W as in whiskey, eight, one, two, six, four, zero, nine, three, five.",
    ),
    ("user", "Okay, I have the member here. What are you calling about today?"),
    (
        "assistant",
        "I'm looking for physical therapy benefits, in and out of network, and I "
        "have some CPT codes to check.",
    ),
    ("user", "Okay. What are the codes?"),
    (
        "assistant",
        "Nine, seven, one, one, zero ... to nine, seven, one, one, six. Place of "
        "service is office.",
    ),
]


@pytest.mark.parametrize(
    "question",
    [
        "And what's the primary diagnosis code?",
        "Do you have a diagnosis code for that?",
    ],
)
async def test_no_diagnosis_code_is_said_the_way_a_caller_says_it(
    session, start_assistant, llm, question
) -> None:
    # From a PT call Jose reviewed in Slack: "The verification concerns general
    # benefits. I do not have a specific diagnosis code for this call." was
    # followed by "am I speaking to live human representative here?". His own
    # wording: "I don't have diagnosis code for this one, will be just general
    # physical therapy under the CPT that I gave you."
    agent = await start_assistant(
        caller_first_name="Annie",
        caller_last_initial="S",
        callback_number="2025550123",
        member_id="W812640935",
        service_type="physical_therapy",
        cpt_codes=["97110", "97112", "97116"],
        service_locations=["office"],
        dx_code="",
    )
    await replay(agent, PT_VERIFIED)

    result = await session.run(user_input=question)
    show_response(question, result)

    spoken = result.expect.next_event().is_message(role="assistant")
    text = spoken.event().item.text_content.lower()
    for paperwork_word in ("verification", "concerns"):
        assert paperwork_word not in text, text
    await spoken.judge(
        llm,
        intent=(
            "Says plainly that there is no diagnosis code for this one and it is "
            "just general physical therapy under the CPT codes already given, in "
            "relaxed everyday words with contractions, the way a clinic's billing "
            "staff says it on the phone (for example 'I don't have a diagnosis code "
            "for this one, it's just general PT under those CPT codes'). It does not "
            "invent a code or a condition, and it does not sound like a formal "
            "statement read from a form."
        ),
    )
    result.expect.no_more_events()


async def test_provider_name_is_given_as_a_person_gives_it(
    session, start_assistant, llm
) -> None:
    # Call [call ID removed], 2026-09-21: "It is Northgate Sports Physical Therapy."
    # then "The NPI number is, ..." and the rep asked "Are you a virtual assistant
    # or virtual representative?"
    agent = await start_assistant(
        caller_first_name="Annie",
        caller_last_initial="S",
        facility_name="Northgate Sports Physical Therapy",
    )
    await replay(
        agent,
        [
            (
                "user",
                "Thanks for calling provider services, this is Dana. Can I get your name?",
            ),
            ("assistant", "Hi Dana, this is Annie, last initial S as in sierra."),
        ],
    )

    question = "Thanks, Annie. And what's the name of the provider you're calling from?"
    result = await session.run(user_input=question)
    show_response(question, result)

    spoken = result.expect.next_event().is_message(role="assistant")
    text = spoken.event().item.text_content.strip()
    assert not text.lower().startswith("it is "), text
    await spoken.judge(
        llm,
        intent=(
            "Gives the provider name, Northgate Sports Physical Therapy, and "
            "nothing the representative did not ask for. Any natural spoken phrasing "
            "is fine: the name on its own, 'It's ...', 'The provider is ...', 'I'm "
            "calling from ...'."
        ),
    )
    result.expect.no_more_events()


BENEFITS_THROUGH_COB = PT_VERIFIED + [
    (
        "user",
        "Okay. So in network, physical therapy is covered at twenty percent "
        "coinsurance after the deductible, no copay. Out of network is forty "
        "percent after the deductible. All of those codes are fine in the office.",
    ),
    ("assistant", "Got it. And is the provider in or out of network for this plan?"),
    ("user", "You're in network."),
    ("assistant", "Okay. What's the effective date on the coverage?"),
    ("user", "January first, twenty twenty-six."),
    ("assistant", "And the expiration date?"),
    ("user", "December thirty-first, twenty twenty-six."),
    ("assistant", "Is that a calendar year or a plan year?"),
    ("user", "Calendar year."),
    ("assistant", "Is the plan self-funded or fully funded?"),
    ("user", "Fully funded."),
    ("assistant", "Is there any coordination of benefits on file?"),
]


async def test_questions_are_asked_in_a_callers_words(
    session, start_assistant, llm
) -> None:
    # "Does the plan cover services rendered outside the member's home state?"
    # is the prompt's topic text, and it was read to representatives word for
    # word on several calls in the two days before this test was written.
    agent = await start_assistant(
        caller_first_name="Annie",
        caller_last_initial="S",
        callback_number="2025550123",
        member_id="W812640935",
        service_type="physical_therapy",
        cpt_codes=["97110", "97112", "97116"],
        service_locations=["office"],
        dx_code="",
    )
    await replay(agent, BENEFITS_THROUGH_COB)

    answer = "No COB on file, they've only got this plan."
    result = await session.run(user_input=answer)
    show_response(answer, result)

    spoken = result.expect.next_event().is_message(role="assistant")
    text = spoken.event().item.text_content.lower()
    assert "rendered" not in text, text
    await spoken.judge(
        llm,
        intent=(
            "Asks whether the plan covers the member out of state, in plain spoken "
            "words a clinic staffer would use, such as 'And does the plan cover them "
            "out of state?' or 'Is there out-of-state coverage?'. A brief "
            "acknowledgment first is fine. It is not a formal phrasing such as "
            "'services rendered outside the member's home state'."
        ),
    )
    result.expect.no_more_events()
