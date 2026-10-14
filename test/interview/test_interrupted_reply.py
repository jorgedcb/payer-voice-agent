"""A reply the representative talks over: the model is told how much of it was heard.

An Aetna call on 2026-10-13, as the interview model received it on
openai/gpt-4.1. The representative answered the visit-limits question in two
pieces that STT split ("Now", then "No, miss."), and the agent's next question
was talked over after "Thank you.". The SDK stores only the played words, so
the model saw a bare "Thank you." followed by "No, miss.", took it for a
goodbye, and closed without asking about the out-of-pocket maximum or medical
necessity review. Earlier the network-status question was cut to "Is RIVERBEND"
and the representative's "Yeah." was taken as its answer.

Names, identifiers, amounts and dates are synthetic.
"""

import pytest
from livekit.agents import llm
from support.calls import called, replay, show_response

from interview import mark_interrupted_replies
from prompts import INTERRUPTED_REPLY_NOTE

SPEC = dict(
    caller_first_name="Annie",
    caller_last_initial="S",
    facility_name="RIVERBEND PHYSICAL THERAPY",
    facility_address="415 Harbor St, Suite 2, Austin, TX 78701",
    npi="1592604718",
    member_name="Marcus Webb",
    member_id="W614208375",
    cpt_codes=["97161", "97110", "97112", "97140", "97530"],
    service_locations=["office", "telehealth"],
    auth_required_in_network=False,
    auth_required_out_network=False,
    ask_visit_limits=True,
    ask_mnr=True,
)

# The navigator's part, which the interview inherits as user messages.
MENU = [
    (
        "user",
        "Thank you for calling Aetna's dedicated provider service center. To improve "
        "our service, your call will be monitored and recorded. By continuing with this "
        "call, you understand, accept, and agree that the information communicated by "
        "Aetna is not an offer of payment, does not guarantee coverage or payment, and "
        "is subject to all benefit plan terms and conditions, including member "
        "eligibility at the time of service. Please say or enter your NPI or tax ID.",
    ),
    (
        "user",
        "Just a moment. Thanks. I found your record. Let's get started. Say claims or "
        "press one coverage and benefits or press two pre certification or press three "
        "contact information or press four appeal inquiries or press five or join the "
        "network or press six.",
    ),
    ("user", "Coverage and benefits. Please say or enter the patient's Aetna ID."),
    ("user", "One moment, please."),
    ("wait", None),
    ("user", "Now, enter or say the patient's date of birth."),
    (
        "user",
        "One moment, please. The patient is Marcus Webb. Correct? Say yes or press "
        "one. No, two.",
    ),
    ("user", "To receive the information via fax, say have it faxed or press two."),
    ("wait", None),
    (
        "user",
        "Would you like to check benefits using a procedure code? Say yes or press "
        "one, no, two.",
    ),
    (
        "user",
        "The reference number for all the information you'll hear on this call is a v "
        "a two four six eight one three five seven nine zero two four. Again, the "
        "reference number is a v a two four six eight one three five seven nine zero "
        "two four.",
    ),
    ("wait", None),
    (
        "user",
        "This patient's coverage is active. They have an Aetna Choice POS two plan, "
        "which is self funded. This plan does not require referrals from the member's "
        "primary care physician. The effective date for the patient's plan is January "
        "first twenty twenty six.",
    ),
    ("wait", None),
    (
        "user",
        "The patient's original effective date is January first twenty twenty six.",
    ),
    ("wait", None),
    (
        "user",
        "The group number is one eight four two nine zero zero three zero zero zero "
        "zero two six.",
    ),
    ("wait", None),
    ("user", "And their PCP? Taylor m Brooks."),
    ("wait", None),
    (
        "user",
        "Benefit details, including deductibles, co pays, and coinsurance are also "
        "available. Do you want benefit details for this patient? Say yes or press "
        "one, no or press two.",
    ),
    ("wait", None),
    (
        "user",
        "Sorry. I didn't hear you. Do you want benefit details? say yes, or press one, "
        "no, or press two. For more information, say help or press star.",
    ),
    ("wait", None),
    (
        "user",
        "Sorry. I still didn't hear you. If you want benefit details, including "
        "deductibles, co pays, and coinsurance for this patient, press one. If not, "
        "press two. For more information, press star. Or to speak with a "
        "representative, press zero.",
    ),
    (
        "user",
        "Are there any other patients I can help you with? Say yes or press one, no, two.",
    ),
    (
        "user",
        "If your question is about registering for or using the Avelity provider "
        "portal, please contact Avelity Client Services at one eight hundred five five "
        "five zero one nine nine. As our customer, your opinion is important to us. You "
        "may have an opportunity to give us feedback on today's call. Please stay on "
        "the line after the call to answer a short survey. Okay? Please hold while I "
        "transfer your call.",
    ),
    (
        "user",
        "you for holding. Your call will be answered by the next available "
        "representative. Did you know you can log in to Avality to submit secured "
        "document attachments, retrieve your remittance advice, and check claim "
        "status? You can also submit reconsiderations and appeals through our new "
        "streamlined process.",
    ),
    ("wait", None),
    ("user", "All of our representatives are"),
    ("wait", None),
]

TO_NETWORK_STATUS = MENU + [
    (
        "user",
        "Thank you for calling Aetna. My name is Renee. I'll be your customer advocate "
        "for today. How may I assist you?",
    ),
    ("interrupted", "Hi Renee, I'm Annie, calling to"),
    ("user", "Hello?"),
    ("assistant", "Hi Renee, I'm calling to check benefits for one of your members."),
    ("user", "Yeah. See, I can opt to check the benefit. On VIP?"),
    (
        "assistant",
        "Yes, that's fine. I'd like to check physical therapy benefits, both in "
        "network and out of network, and I have CPT codes to check. Would you like "
        "those now?",
    ),
    ("user", "NPI of the provider."),
    (
        "assistant",
        "Sure, it's one, five, nine ... two, six, zero ... four, seven, one, eight.",
    ),
    ("user", "Provider's name and the address."),
    (
        "assistant",
        "The provider name is RIVERBEND PHYSICAL THERAPY. The address is four, one, "
        "five, Harbor street, suite two, Austin, T, X.",
    ),
    ("user", "Let me check here."),
    ("wait", None),
    ("user", "moment"),
    ("wait", None),
    (
        "user",
        "To confirm, the NPI is one five nine two six zero four seven one eight. correct?",
    ),
    ("assistant", "Yes, that's correct."),
    ("user", "using the NVI. I I only have here Morgan Ellis."),
    (
        "assistant",
        "That's fine. I'm looking for general physical therapy benefits, not a "
        "specific provider. Can we proceed with the benefit check for the member?",
    ),
    ("user", "For the member, may I please have the member's name?"),
    ("assistant", "Yes, it's Marcus Webb."),
    ("user", "I check if we handle this plan. Do you have the member ID?"),
    (
        "assistant",
        "Yes, it's W as in whiskey, six, one, four, two, zero, eight, three, seven, five.",
    ),
    ("user", "I'm a check here."),
    ("wait", None),
    ("user", "One moment. Let me pull it up for you."),
    ("wait", None),
    ("user", "We handle this plan what is the specific benefit."),
    (
        "assistant",
        "I'm looking for physical therapy benefits for both in network and out of "
        "network. I have CPT codes to check: nine, seven, one, six, one ... nine, "
        "seven, one, one, zero ... nine, seven, one, one, two ... nine, seven, one, "
        "four, zero ... and nine, seven, five, three, zero. The visits could be in the "
        "office or by telehealth. Did you get all those?",
    ),
    ("user", "Yes."),
    (
        "user",
        "Benefit for in network physical therapy, seventy percent after the doctor "
        "ball, no co pay.",
    ),
    ("user", "No visit, Nemet."),
    (
        "assistant",
        "Okay. Can you tell me if a deductible applies to in network physical therapy? "
        "And is the service covered for both office and telehealth?",
    ),
    (
        "user",
        "Good afternoon. -- supply, and the deductible here is two thousand dollars. "
        "Nothing has been met.",
    ),
    (
        "user",
        "That of pocket six thousand dollars met the amount of forty two dollars and "
        "ten cents.",
    ),
    (
        "assistant",
        "Got it. Can you let me know the coinsurance and copay for out of network "
        "physical therapy, and does a deductible apply? Is the service also covered at "
        "both office and telehealth out of network?",
    ),
    (
        "user",
        "Autumn network with diversions after the deductible, neck opaque. And "
        "deductible for autumn network, eight thousand dollars. Nothing has been met. "
        "The out of pocket of twelve thousand dollars met a math of forty two dollars "
        "and ten cents.",
    ),
    (
        "assistant",
        "Thank you. Is every one of those CPT codes billable for both in network and "
        "out of network, including for telehealth?",
    ),
    ("user", "Yeah."),
    # Written as "Thank you. Is RIVERBEND PHYSICAL THERAPY considered in network
    # or out of network for this member's plan?"; the representative's "Yeah." was
    # already under way.
    ("interrupted", "Thank you. Is RIVERBEND"),
]

TO_VISIT_LIMITS = TO_NETWORK_STATUS + [
    ("user", "Yeah."),
    ("assistant", "Thanks. What is the coverage expiration date for the plan?"),
    ("user", "Let's not termination. It's the laptop."),
    (
        "assistant",
        "Okay, thanks. Is the plan based on a calendar year, a contract year, or a "
        "service year?",
    ),
    ("user", "Adtrance on plan year. Uncheckedia"),
    (
        "user",
        "at brace at January one twenty twenty six and twelve December thirty one "
        "twenty twenty six.",
    ),
    ("assistant", "Thank you. Is this plan self-funded or fully funded?"),
    ("user", "Self funded."),
    ("assistant", "Thanks. Is coordination of benefits on file for this member?"),
    ("user", "Yeah. Lowers now member needs to updated."),
    (
        "assistant",
        "Thank you. Are there any other insurances on file for this member? If so, do "
        "you know their names and whether they're primary, secondary, or tertiary?",
    ),
    ("user", "Hard insurance."),
    (
        "assistant",
        "Okay, thank you. Does the plan cover the member for physical therapy out of "
        "state?",
    ),
    ("user", "No. Uh, yes."),
    (
        "assistant",
        "Got it. Are there any visit limits for physical therapy, for either in "
        "network or out of network, and for office and telehealth?",
    ),
    ("user", "Now"),
    # Written as "Thank you. Does the out-of-pocket maximum apply to physical
    # therapy visits, for both in network and out of network, and at both office
    # and telehealth?"; the rest of the representative's answer cut it off.
    ("interrupted", "Thank you."),
]


def test_a_note_follows_each_interrupted_reply_only() -> None:
    chat_ctx = llm.ChatContext()
    chat_ctx.add_message(role="user", content="Any visit limits?")
    chat_ctx.add_message(role="assistant", content="Thank you.", interrupted=True)
    chat_ctx.add_message(role="user", content="No, miss.")
    chat_ctx.add_message(role="assistant", content="Okay. Does the deductible apply?")

    marked = mark_interrupted_replies(chat_ctx)

    assert [(m.role, m.text_content) for m in marked.messages()] == [
        ("user", "Any visit limits?"),
        ("assistant", "Thank you."),
        ("system", INTERRUPTED_REPLY_NOTE),
        ("user", "No, miss."),
        ("assistant", "Okay. Does the deductible apply?"),
    ]
    # Per request only: what was said on the line stays as it was.
    assert len(chat_ctx.items) == 4


def test_a_history_without_interruptions_is_passed_through() -> None:
    chat_ctx = llm.ChatContext()
    chat_ctx.add_message(role="assistant", content="Any visit limits?")
    chat_ctx.add_message(role="user", content="No.")
    assert mark_interrupted_replies(chat_ctx) is chat_ctx


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param("No, miss.", id="as-transcribed"),
        pytest.param("No.", id="no"),
        pytest.param(
            "Nope.",
            id="nope",
            # 2026-10-13: still closes 2 in 10 runs with the note (4 in 6
            # without it); measured nightly.
            marks=pytest.mark.quarantine,
        ),
    ],
)
async def test_a_short_answer_after_a_cut_off_question_does_not_end_the_call(
    session, start_assistant, llm, reply
) -> None:
    agent = await start_assistant(**SPEC)
    await replay(agent, TO_VISIT_LIMITS)

    result = await session.run(user_input=reply)
    show_response(reply, result)

    assert not called(result, "end_call"), (
        "The out-of-pocket and medical-necessity topics were never asked."
    )
    await result.expect.contains_message(role="assistant").judge(
        llm,
        intent=(
            "Asks the representative a benefits question that has not been answered "
            "yet in this call, such as whether the out-of-pocket maximum applies to "
            "physical therapy, whether a deductible counts toward it, or whether a "
            "medical-necessity review is required. Does not ask about other "
            "limitations or exclusions, ask for a name or call reference, or say "
            "goodbye."
        ),
    )


async def test_a_yes_to_a_cut_off_question_does_not_answer_it(
    session, start_assistant, llm
) -> None:
    agent = await start_assistant(**SPEC)
    await replay(agent, TO_NETWORK_STATUS)

    result = await session.run(user_input="Yeah.")
    show_response("Yeah.", result)

    assert not called(result, "end_call")
    await result.expect.contains_message(role="assistant").judge(
        llm,
        intent=(
            "Asks whether the provider is in network or out of network for the "
            "member's plan, since the representative did not hear that question."
        ),
    )
