"""The provider's network as the clinic's contracts state it.

Replays a 2026-11-24 UnitedHealthcare physical therapy call on a delegated plan,
with synthetic identifiers. One representative found the provider in network by
NPI and tax ID; on the retry, another looked it up by name and address only and
said out of network. Later she asked whether the provider was contracted, and
the agent, with nothing on file to answer from, repeated her own answer back as
a fact: "This provider is not contracted ... Just out of network." She took it
as the provider's word and said prior authorization was not required.

The call now carries the contracts' network. The agent checks a representative's
answer against it once, answers "are you contracted?" from it, and collects
both networks when the answer still disagrees.
"""

from support.calls import replay, said, show_response, spoken_digits

NPI = "1234567893"
TAX_ID = "123456789"

ASKED = [
    (
        "user",
        "Thank you so much for that one, Annie. And may I know what specific "
        "benefit information you need to know for this patient?",
    ),
    ("assistant", "I'm looking to check physical therapy benefits for this member."),
    ("user", "Okay. Is it in or out of network?"),
    (
        "assistant",
        "Could you check whether this provider is in network or out of network "
        "for the member's plan?",
    ),
    (
        "user",
        "Let me go ahead and double check that one for you. And just a heads up, "
        "for this patient account, this is a delegated plan for... okay. Go ahead.",
    ),
    ("wait", None),
]

SAID_OUT = (
    "So while tracking that one for you, you can also double check the benefits "
    "in our portal at u h c provider dot com. And showing here, Annie, that this "
    "provider is out of network under this patient account. And for the coverage, "
    "that is ninety dollars co pay for each Medicare covered physical therapy visit."
)

RECHECKED = [
    *ASKED,
    ("user", SAID_OUT),
    (
        "assistant",
        "Our records show this provider is in network with you. The NPI is one, "
        "two, three ... four, five, six ... seven, eight, nine, three, and the tax "
        "ID is one, two ... three, four, five, six, seven, eight, nine. Could you "
        "look it up again by NPI and tax ID? And is the network for physical "
        "therapy managed by another company on this plan?",
    ),
    ("user", "Sure, one moment."),
    ("wait", None),
]

STILL_OUT = (
    "Okay, I looked it up by the NPI and the tax ID, and it's still showing out "
    "of network for this member. I don't see another company on the network side."
)

CONTRACTED = (
    "And just to confirm first, are you contracted from OptumHealth, physical "
    "health, or UHC or not?"
)


async def test_a_representative_s_other_network_is_checked_again(
    session, start_assistant, llm
):
    agent = await start_assistant(
        network_benefit_scope="provider_network",
        contract_network="in_network",
        npi=NPI,
        tax_id=TAX_ID,
    )
    await replay(agent, ASKED)

    result = await session.run(user_input=SAID_OUT)
    show_response(SAID_OUT, result)

    digits = spoken_digits(said(result))
    assert NPI in digits or TAX_ID in digits, "Give the NPI or tax ID to look up."
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Does not accept out of network yet. Says its records show the "
                "provider is in network (or contracted) with them, gives the "
                "provider's NPI and/or tax ID, and asks the representative to look "
                "it up again by NPI or tax ID. May also ask whether another company "
                "handles the network for this plan, or whether it still shows out of "
                "network. Does not ask about out-of-network benefits in this turn."
            ),
        )
    )


async def test_an_answer_that_agrees_with_the_records_moves_on(
    session, start_assistant, llm
):
    agent = await start_assistant(
        network_benefit_scope="provider_network",
        contract_network="in_network",
        npi=NPI,
        tax_id=TAX_ID,
    )
    await replay(agent, ASKED)

    answer = (
        "Alright. It shows here that the provider is in network in UnitedHealthcare. "
        "Co pay is fifty dollars per visit."
    )
    result = await session.run(user_input=answer)
    show_response(answer, result)

    assert "records" not in said(result).lower()
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Takes in network as the answer and goes on with the physical "
                "therapy benefits topic: reading out the CPT codes counts, as does "
                "asking about coinsurance, deductible or coverage. Does not ask "
                "them to look the network up again or recite the NPI or tax ID."
            ),
        )
    )


async def test_a_dispute_that_stands_collects_both_networks(
    session, start_assistant, llm
):
    agent = await start_assistant(
        network_benefit_scope="provider_network",
        contract_network="in_network",
        npi=NPI,
        tax_id=TAX_ID,
    )
    await replay(agent, RECHECKED)

    result = await session.run(user_input=STILL_OUT)
    show_response(STILL_OUT, result)

    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Accepts the answer without disputing it again or asking for "
                "another lookup. Goes on with the benefits, and asks for the "
                "in-network benefits (or for both networks' benefits). Fails if it "
                "asks only about out-of-network benefits with no mention of in "
                "network."
            ),
        )
    )


async def test_are_you_contracted_is_answered_from_the_records(
    session, start_assistant, llm
):
    agent = await start_assistant(
        network_benefit_scope="provider_network",
        contract_network="in_network",
        npi=NPI,
        tax_id=TAX_ID,
    )
    await replay(
        agent,
        [
            *RECHECKED,
            ("user", STILL_OUT),
            (
                "assistant",
                "Okay, thanks for checking. Could you also give me the in-network "
                "benefits for physical therapy?",
            ),
            (
                "user",
                "In network it would be fifty dollars co pay per visit, no deductible.",
            ),
            (
                "assistant",
                "Got it. Is prior authorization required for physical therapy "
                "visits at the office?",
            ),
        ],
    )

    result = await session.run(user_input=CONTRACTED)
    show_response(CONTRACTED, result)

    assert "not contracted" not in said(result).lower()
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Answers that its records show the provider is in network or "
                "contracted with them, as what its records show. Saying it has "
                "nothing on file about OptumHealth, the other company named, is "
                "fine. Fails if it says the provider is not contracted or is out "
                "of network as its own answer, or that it has nothing on file "
                "about UnitedHealthcare (UHC), the plan it is calling."
            ),
        )
    )


async def test_are_you_contracted_without_records_is_not_answered_with_theirs(
    session, start_assistant, llm
):
    agent = await start_assistant(
        network_benefit_scope="provider_network",
        npi=NPI,
        tax_id=TAX_ID,
    )
    await replay(
        agent,
        [
            *ASKED,
            ("user", SAID_OUT),
            (
                "assistant",
                "Got it, thank you. Is a deductible applied for these physical "
                "therapy visits, and is the code billable?",
            ),
            (
                "user",
                "There is no deductible for this patient account, and the code is "
                "valid and available.",
            ),
            (
                "assistant",
                "Is prior authorization required for out-of-network physical "
                "therapy visits at the office?",
            ),
        ],
    )

    result = await session.run(user_input=CONTRACTED)
    show_response(CONTRACTED, result)

    assert "not contracted" not in said(result).lower()
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Says it doesn't have the provider's contract status on file, and "
                "may ask them to check by NPI or tax ID. Must not claim, as its own "
                "answer, that the provider is not contracted or is out of network."
            ),
        )
    )


async def test_in_or_out_of_network_is_turned_back_before_they_answer(
    session, start_assistant, llm
):
    agent = await start_assistant(
        network_benefit_scope="provider_network",
        contract_network="in_network",
        npi=NPI,
        tax_id=TAX_ID,
    )
    await replay(agent, ASKED[:2])

    question = ASKED[2][1]
    result = await session.run(user_input=question)
    show_response(question, result)

    assert "records" not in said(result).lower()
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Asks the representative to check whether the provider is in or out "
                "of network for the member's plan. Does not state the provider's "
                "network itself, and does not say what its own records show."
            ),
        )
    )


async def test_a_volunteered_other_network_is_checked_in_both_networks_scope(
    session, start_assistant, llm
):
    agent = await start_assistant(
        network_benefit_scope="both_networks",
        contract_network="in_network",
        npi=NPI,
        tax_id=TAX_ID,
        cpt_codes=["97110"],
        service_locations=["office"],
    )
    await replay(
        agent,
        [
            ASKED[0],
            (
                "assistant",
                "I'm looking to check physical therapy benefits for in network and "
                "out of network, and I have a CPT code to check.",
            ),
            ("user", "Okay, go ahead with the code."),
            (
                "assistant",
                "It's nine seven one one zero, for visits at the office. Did you get "
                "that?",
            ),
            ("user", "Got it. Let me pull that up for you."),
            ("wait", None),
        ],
    )

    result = await session.run(user_input=SAID_OUT)
    show_response(SAID_OUT, result)

    digits = spoken_digits(said(result))
    assert NPI in digits or TAX_ID in digits, "Give the NPI or tax ID to look up."
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Does not accept out of network yet. Says its records show the "
                "provider is in network (or contracted) with them, gives the "
                "provider's NPI and/or tax ID, and asks the representative to look "
                "it up again by NPI or tax ID. May also ask whether another company "
                "handles the network for this plan."
            ),
        )
    )
