"""Real calls, as the model saw them, for replaying in tests.

Each call is the chat context at one turn, taken from the Langfuse trace: the
payer's utterances verbatim as STT delivered them, and the agent's own tool
calls in order. Identifiers are invented, since the calls carried real patients'.
"""

import json

from livekit.agents import ChatContext, llm


def called(result, name: str) -> bool:
    """Whether the agent called this tool during the turn."""
    return any(
        ev.type == "function_call" and ev.item.name == name for ev in result.events
    )


def assert_wait_is_exclusive(result) -> None:
    """Silence is an alternative to speech or another action, never an extra step."""
    if not called(result, "wait"):
        return
    actions = [ev for ev in result.events if ev.type == "function_call"]
    speech = [
        ev
        for ev in result.events
        if ev.type == "message" and ev.item.role == "assistant" and ev.item.text_content
    ]
    assert len(actions) == 1 and not speech, "wait must be the turn's only action."


async def assert_yields_turn(result, judge, *, after: str) -> None:
    """Accept silence or a brief acknowledgment without advancing or closing."""
    assert not called(result, "end_call"), "Waiting must not start closing."
    assert_wait_is_exclusive(result)
    # A model may acknowledge, call wait alone, or produce no speech.
    # Check every spoken message rather than pinning a tool choice or wording.
    for index, event in enumerate(result.events):
        if (
            event.type == "message"
            and event.item.role == "assistant"
            and event.item.text_content
        ):
            await (
                result.expect[index]
                .is_message(role="assistant")
                .judge(
                    judge,
                    intent=(
                        f"The representative just said {after!r}. In this context, the reply is "
                        "A brief conversational acknowledgment or courtesy that gives the "
                        "representative time to continue. A word or a short sentence is valid; "
                        "no particular wording is required. Does not ask a question, request "
                        "a call reference, start a new topic, claim verification is complete, "
                        "announce disconnection, or narrate stage directions."
                    ),
                )
            )


def handoff_opening(result) -> str:
    """The opening the navigator wrote into its handoff call.

    Consumes the handoff's own event, so the caller goes on asserting from the
    tool result that follows it.
    """
    call = result.expect.next_event().is_function_call(name="representative_answered")
    return json.loads(call.event().item.arguments)["opening"]


def turn_settings(session):
    """What the SDK resolved for the agent now running: session default, agent override."""
    activity = session.current_agent._get_activity_or_raise()
    return activity.endpointing_opts, activity.preemptive_generation_opts["enabled"]


def show_response(user_input, result):
    """Print a test turn's replies and tool activity; use pytest -s to see passes."""
    print(f"\nRepresentative: {user_input}")
    for event in result.events:
        item = getattr(event, "item", None)
        if item is None:
            continue
        if item.type == "message":
            print(f"Agent: {item.text_content}")
        elif item.type == "function_call":
            print(f"Tool call: {item.name}({item.arguments})")
        elif item.type == "function_call_output":
            print(f"Tool result: {item.output}")


# Aetna provider line, 2026-09-06, at the turn the menu
# read the patient back. The identifiers are synthetic stand-ins for what the
# agent said on the line; a test passes them to the navigator so the prompt's
# THIS CALL matches the replayed turns.
AETNA_PATIENT = {
    "member_name": "Rashid Amari",
    "member_id": "W812640935",
    "member_dob": "2018-05-14",
    "npi": "1477583926",
}

AETNA_CALL = [
    (
        "user",
        (
            "Thank you for calling Aetna's dedicated provider service center. To improve "
            "our service your call will be monitored and recorded. By continuing with this "
            "call, you understand, accept, and agree that the information communicated by "
            "Aetna is not an offer of payment does not guarantee coverage or payment, and "
            "is subject to all benefit plan terms and conditions. Including member "
            "eligibility at the time of service. Please say or enter your NPI or tax ID."
        ),
    ),
    ("speak", "1 4 7 7 5 8 3 9 2 6"),
    (
        "user",
        (
            "Just a moment. Thanks. I found your record. Let's get started. Say claims, "
            "coverage and benefits, precertification, contact information, a appeal "
            "inquiries, or join the network."
        ),
    ),
    ("speak", "coverage and benefits"),
    ("user", "Coverage and benefits. Please say or enter the patient's Aetna ID."),
    ("speak", "W 8 1 2 6 4 0 9 3 5"),
    ("user", "One moment, please."),
    ("wait", None),
    ("user", "And the patient's date of birth?"),
    ("speak", "May 14, 2018"),
]

# What STT wrote when the menu said "Rashid". The next turn on the real call.
AETNA_READBACK = "One moment, please. The patient is Richard Amari. Correct?"

# UnitedHealthcare provider line, 2026-09-22, up to the turn the virtual
# assistant asked for the member ID. It is a speech-first system: every prompt
# so far said what to say and none mentioned the keypad. The identifiers are
# synthetic stand-ins, numeric like the real ones.
UHC_PATIENT = {
    "member_id": "907264318",
    "member_dob": "1987-04-09",
    "npi": "1588694037",
}

# The operator note UHC calls carry. Two more, saying "no" to hearing the copay
# again and to checking another therapy, were removed on 2026-09-22: turning
# down more self-service is the navigator's job without being told.
UHC_IVR_INSTRUCTIONS = (
    "When they say, “If that’s all you need, you may hang up now,” respond with "
    "“representative.”"
)

UHC_CALL = [
    (
        "user",
        (
            "UnitedHealthcare. Your call may be monitored or recorded for quality or "
            "account security purposes. AI may assist. For quicker assistance, please "
            "have your NPI or tax ID number ready. You may also be asked for the member "
            "ID and date of birth. Having this information available will help you "
            "access self-service options and connect with a representative more quickly."
        ),
    ),
    ("wait", None),
    ("user", "For English, just remain on the line."),
    ("wait", None),
    (
        "user",
        (
            "I'm your virtual assistant at United Healthcare. If you're a member and "
            "need help with your health plan, say I'm a member. Otherwise, in a few "
            "words, tell me what are you calling about."
        ),
    ),
    ("speak", "benefits and eligibility"),
    ("user", "Are you calling as a health care professional?"),
    ("speak", "yes"),
    (
        "user",
        (
            "Sure. What type of benefit are you calling about? You can say medical, "
            "behavioral health, prescriptions, dental, or vision."
        ),
    ),
    ("speak", "medical"),
    (
        "user",
        (
            "In order to connect you to the right advocate, please specify what type "
            "of benefit you are calling about. You can say one of these options, "
            "medical, behavioral health, prescriptions, dental, or vision. What type "
            "of benefit are you calling about?"
        ),
    ),
    ("speak", "medical"),
    ("user", "Thanks."),
    ("wait", None),
]

# The next turn on the real call. The agent spoke the ID and the system misheard
# it twice, then failed the spoken NPI and tax ID the same way.
UHC_MEMBER_ID_QUESTION = (
    "Let's move forward with your benefit request. And what is the member ID?"
)

# The same line on another call that day, which got through authentication and
# into the automated benefits, up to the turn they were read out.
UHC_BENEFITS_CALL = [
    *UHC_CALL,
    ("user", UHC_MEMBER_ID_QUESTION),
    ("speak", "9 0 7 2 6 4 3 1 8"),
    ("user", "I heard nine zero seven two six four three one eight. Is that correct?"),
    ("speak", "yes"),
    (
        "user",
        (
            "What is the member's date of birth including the four digit year. For "
            "example, June nineteenth nineteen sixty seven."
        ),
    ),
    ("speak", "April 9, 1987"),
    (
        "user",
        (
            "Thank you. One moment while I pull up the account. Alright. I have the "
            "account in front of me now. One moment. I'm pulling that up now. Okay. "
            "What is your NPI?"
        ),
    ),
    ("speak", "1 5 8 8 6 9 4 0 3 7"),
    (
        "user",
        (
            "Thanks. I've got it. United's verification of a member's benefits is not "
            "a guarantee of payment, and United is not entering into a contract for "
            "payment of any amount by providing this information. Payments are only "
            "determined when claims are received and processed through the member's "
            "plan. Now what type of benefit are you calling about? For example, co "
            "pay, coinsurance, therapy benefits and limits, coordination of benefits, "
            "deductible, out of pocket, plan details, or PCP."
        ),
    ),
    ("speak", "therapy benefits and limits"),
    ("user", "Got it."),
    ("wait", None),
    ("user", "Just a moment while I check therapy benefits for you."),
    ("wait", None),
    (
        "user",
        (
            "Would you like co pay and coinsurance for in network, out of network, "
            "or in network tier one?"
        ),
    ),
    ("speak", "in network"),
    (
        "user",
        (
            "Okay. Would you like cardiac rehabilitation, occupational therapy, "
            "physical therapy, or speech therapy?"
        ),
    ),
    ("speak", "physical therapy"),
]

# The benefits read out, then two offers of more self-service. Saying yes to
# either keeps the call in the menu; on these calls the answer was "no" to both,
# then "representative" at the offer to hang up.
UHC_OFFER_TO_REPEAT = (
    "Okay. Here's the coinsurance information for physical therapy. Coinsurance is "
    "twenty percent after deductible has been met. I also found these details. "
    "Benefits allowed. Visits one hundred no dollar amount limit per calendar year "
    "limitation is combined for physical therapy, occupational therapy, and speech "
    "therapy for in network rehabilitative. Benefits remaining. Visits one hundred "
    "no dollar amount limit per remaining limitation is combined for physical "
    "therapy, occupational therapy, and speech therapy for in network "
    "rehabilitative. Visits three per remaining rehabilitative additional benefit "
    "for musculoskeletal pain management program. You need to hear that co pay and "
    "coinsurance again?"
)
UHC_OFFER_TO_CHECK_ANOTHER = (
    "Would you like to check another co pay or therapy for this member?"
)
UHC_OFFER_TO_HANG_UP = (
    "If that's all you need, you can hang up now. Otherwise, you can say check a "
    "different benefit, main menu, or connect to an advocate."
)

# Blue Cross Blue Shield of Michigan provider line, 2026-09-28, up to the turn
# the menu asked for the member's ZIP code. The call carried no ZIP: the agent
# said it had none, then keyed five zeros, and the menu read the contract status
# and hung up. The identifiers are synthetic stand-ins.
BCBSM_PATIENT = {
    "member_name": "Marisol Ortega",
    "member_id": "ZZB712408356",
    "member_dob": "1981-03-17",
    "member_zip": "49503",
    "npi": "1629384750",
}

BCBSM_CALL = [
    (
        "user",
        (
            "Hello. You've reached Blue Cross Blue Shield of Michigan and Blue Care "
            "Network. If you bill on a CMS fifteen hundred claim form, say professional. "
            "If you bill on a u b zero four claim form, say facility. You can also say "
            "dental, vision, or hearing."
        ),
    ),
    ("speak", "professional"),
    (
        "user",
        (
            "If you're currently... sorry. If you're currently billing on a CMS fifteen "
            "hundred, say professional or press one. If you're billing on a u b zero "
            "four, say facility or press two. You can also say dental or press three, "
            "vision or press four, or hearing or press five."
        ),
    ),
    ("send_dtmf_events", "1"),
    (
        "user",
        (
            "Please tell me the reason for your call. You can say claims or eligibility "
            "and benefits."
        ),
    ),
    ("speak", "eligibility and benefits"),
    (
        "user",
        (
            "Now, please tell me your national provider identifier or provider "
            "identification number. If it's completely numeric, you may enter it on "
            "your telephone keypad. You can also say, I don't have one."
        ),
    ),
    ("send_dtmf_events", "1629384750"),
    ("user", "That's one six two nine three eight four seven five zero. Right?"),
    ("speak", "yes"),
    (
        "user",
        (
            "One moment while I verify that national provider identifier. Please note "
            "that the information stated is based on finalized claims data and is not a "
            "guarantee of payment. Payment will be based on medical necessity, provider "
            "participation, and specialty, network status, procedure code, and diagnosis "
            "billed. Next, I need to know the type of professional services you provide. "
            "Please tell me your specialty when you hear it. You can say women's health, "
            "mental health services, general practice, podiatry, chiropractic services, "
            "durable medical equipment, or prosthetic and orthotic devices, ambulance, "
            "pediatrics, or say other services. To hear that list again, say repeat. "
            "Otherwise, tell me your specialty."
        ),
    ),
    ("speak", "other services"),
    ("user", "You said other services. Right?"),
    ("speak", "yes"),
    (
        "user",
        (
            "Alright. For verification purposes, please tell me the numeric portion of "
            "the member's contract number."
        ),
    ),
    ("speak", "7 1 2 4 0 8 3 5 6"),
    ("user", "Sorry. Please say the numeric portion of the member's contract number."),
    ("speak", "7 1 2 4 0 8 3 5 6"),
    (
        "user",
        (
            "I still didn't understand. Ignoring all of the letters in the beginning, "
            "please say the numeric portion of the member's contract number."
        ),
    ),
    ("speak", "seven one two four zero eight three five six"),
    ("user", "That's seven one two four zero eight three five six. Is that right?"),
    ("speak", "yes"),
    (
        "user",
        (
            "Thank you. One moment, please. Now tell me the member's date of birth, "
            "including the year."
        ),
    ),
    ("send_dtmf_events", "03171981"),
    ("user", "For exam..."),
    ("wait", None),
    (
        "user",
        "Thanks. Next, spell up to the first five letters of the member's first name.",
    ),
    ("speak", "M A R I S"),
    (
        "user",
        (
            "Sorry. Please spell up to the first five letters of the member's first "
            "name. For example, Samantha would be spelled s a m a n."
        ),
    ),
    ("speak", "M A R I S"),
    (
        "user",
        (
            "I still didn't understand. Please spell up to the first five letters of the "
            "member's first name. For example, George would be spelled g e o r g."
        ),
    ),
    (
        "speak",
        "M as in mike, A as in alpha, R as in romeo, I as in india, S as in sierra",
    ),
    ("user", "You said m a r i s. Is that right?"),
    ("speak", "yes"),
]

BCBSM_ZIP_QUESTION = "Please say or enter the member's five digit ZIP code."


# Aetna provider line, two physical therapy calls for one clinic, each replayed up
# to the answer after which the interview asked the closing question with benefit
# topics still open. The menu's read-out and the representatives' turns are as STT
# delivered them; the clinic, members, representatives, references and amounts are
# synthetic stand-ins. Both calls carried visit limits and the medical-necessity
# question, collected both networks, and had prior authorization on file.
AETNA_PT_CLINIC = {
    "caller_first_name": "Annie",
    "caller_last_initial": "S",
    "facility_name": "HARBORVIEW PHYSICAL THERAPY, PC",
    "facility_address": "4 W 22nd Street, Floor 11, New York, NY 10010",
    "npi": "1902846375",
    "npi_type": "facility",
    "tax_id": "83-5027419",
    "callback_number": "+12025550147",
    "cpt_codes": ["97161", "97110", "97112", "97140", "97530"],
    "dx_code": "",
    "service_locations": ["office", "telehealth"],
    "service_type": "physical_therapy",
    "ask_visit_limits": True,
    "ask_mnr": True,
    "auth_required_in_network": True,
    "auth_required_out_network": True,
}

# 2026-10-13. The representative answered the visit-limits question ("No, miss.",
# no maximum) and the agent asked about other limitations or exclusions and called
# end_call in the same turn, with the out-of-pocket questions and the
# medical-necessity review never asked.
AETNA_VISIT_LIMITS_PATIENT = {
    "member_name": "Marcus Bell",
    "member_id": "W418392057",
    "member_dob": "1984-06-21",
}

AETNA_VISIT_LIMITS_CALL = [
    (
        "user",
        (
            "Thank you for calling Aetna's dedicated provider service center. To improve "
            "our service, your call will be monitored and recorded. By continuing with "
            "this call, you understand, accept, and agree that the information "
            "communicated by Aetna is not an offer of payment, does not guarantee "
            "coverage or payment, and is subject to all benefit plan terms and "
            "conditions, including member eligibility at the time of service. Please say "
            "or enter your NPI or tax ID."
        ),
    ),
    (
        "user",
        (
            "Just a moment. Thanks. I found your record. Let's get started. Say claims or "
            "press one coverage and benefits or press two pre certification or press "
            "three contact information or press four appeal inquiries or press five or "
            "join the network or press six."
        ),
    ),
    ("user", "Coverage and benefits. Please say or enter the patient's Aetna ID."),
    ("user", "One moment, please."),
    ("wait", None),
    ("user", "Now, enter or say the patient's date of birth."),
    (
        "user",
        (
            "One moment, please. The patient is Marcus Bell. Correct? Say yes or press "
            "one. No, two."
        ),
    ),
    ("user", "To receive the information via fax, say have it faxed or press two."),
    ("wait", None),
    (
        "user",
        (
            "Would you like to check benefits using a procedure code? Say yes or press "
            "one, no, two."
        ),
    ),
    (
        "user",
        (
            "The reference number for all the information you'll hear on this call is a v "
            "a one five five five two four eight six zero three one nine. Again, the "
            "reference number is a v a one five five five two four eight six zero three "
            "one nine."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "This patient's coverage is active. They have an Aetna Choice POS two plan, "
            "which is self funded. This plan does not require referrals from the member's "
            "primary care physician. The effective date for the patient's plan is June "
            "fifteenth twenty twenty six."
        ),
    ),
    ("wait", None),
    (
        "user",
        ("The patient's original effective date is June fifteenth twenty twenty six."),
    ),
    ("wait", None),
    (
        "user",
        (
            "The group number is two four one eight zero three zero one zero zero zero "
            "zero one two."
        ),
    ),
    ("wait", None),
    ("user", "And their PCP? Laura k Benton."),
    ("wait", None),
    (
        "user",
        (
            "Benefit details, including deductibles, co pays, and coinsurance are also "
            "available. Do you want benefit details for this patient? Say yes or press "
            "one, no or press two."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Sorry. I didn't hear you. Do you want benefit details? say yes, or press "
            "one, no, or press two. For more information, say help or press star."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Sorry. I still didn't hear you. If you want benefit details, including "
            "deductibles, co pays, and coinsurance for this patient, press one. If not, "
            "press two. For more information, press star. Or to speak with a "
            "representative, press zero."
        ),
    ),
    (
        "user",
        (
            "Are there any other patients I can help you with? Say yes or press one, no, "
            "two."
        ),
    ),
    (
        "user",
        (
            "If your question is about registering for or using the Avelity provider "
            "portal, please contact Avelity Client Services at one eight hundred two "
            "eight two four five four eight. As our customer, your opinion is important "
            "to us. You may have an opportunity to give us feedback on today's call. "
            "Please stay on the line after the call to answer a short survey. Okay? "
            "Please hold while I transfer your call."
        ),
    ),
    (
        "user",
        (
            "you for holding. Your call will be answered by the next available "
            "representative. Did you know you can log in to Avality to submit secured "
            "document attachments, retrieve your remittance advice, and check claim "
            "status? You can also submit reconsiderations and appeals through our new "
            "streamlined process."
        ),
    ),
    ("wait", None),
    ("user", "All of our representatives are"),
    ("wait", None),
    (
        "user",
        (
            "Thank you for calling Aetna. My name is Dana. I'll be your customer advocate "
            "for today. How may I assist you?"
        ),
    ),
    ("assistant", "Hi Dana, I’m Annie, calling to"),
    ("user", "Hello?"),
    ("assistant", "Hi Dana, I’m calling to check benefits for one of your members."),
    ("user", "Yeah. See, I can opt to check the benefit. On VIP?"),
    (
        "assistant",
        (
            "Yes, that’s fine. I’d like to check physical therapy benefits, both in "
            "network and out of network, and I have CPT codes to check. Would you like "
            "those now?"
        ),
    ),
    ("user", "NPI of the provider."),
    (
        "assistant",
        (
            "Sure, it's one, nine, zero ... two, eight, four ... six, three, seven, five."
        ),
    ),
    ("user", "Provider's name and the address."),
    (
        "assistant",
        (
            "The provider name is HARBORVIEW PHYSICAL THERAPY, PC. The address is four, "
            "W, twenty,second street, floor one, one, New York, N, Y."
        ),
    ),
    ("user", "Let me check here."),
    ("wait", None),
    ("user", "moment"),
    ("wait", None),
    (
        "user",
        (
            "To confirm, the NPI is one nine zero two eight four six three seven five. "
            "correct?"
        ),
    ),
    ("assistant", "Yes, that's correct."),
    ("user", "using the NVI. I I only have here Victor Hale."),
    (
        "assistant",
        (
            "That’s fine. I’m looking for general physical therapy benefits, not a "
            "specific provider. Can we proceed with the benefit check for the member?"
        ),
    ),
    ("user", "For the member, may I please have the member's name?"),
    ("assistant", "Yes, it’s Marcus Bell."),
    ("user", "I check if we handle this plan. Do you have the member ID?"),
    (
        "assistant",
        (
            "Yes, it's W as in whiskey, four, one, eight, three, nine, two, zero, five, "
            "seven."
        ),
    ),
    ("user", "I'm a check here."),
    ("wait", None),
    ("user", "One moment. Let me pull it up for you."),
    ("wait", None),
    ("user", "We handle this plan what is the specific benefit."),
    (
        "assistant",
        (
            "I’m looking for physical therapy benefits for both in network and out of "
            "network. I have CPT codes to check: nine, seven, one, six, one ... nine, "
            "seven, one, one, zero ... nine, seven, one, one, two ... nine, seven, one, "
            "four, zero ... and nine, seven, five, three, zero. The visits could be in "
            "the office or by telehealth. Did you get all those?"
        ),
    ),
    ("user", "Yes."),
    (
        "user",
        (
            "Benefit for in network physical therapy, seventy percent after the doctor "
            "ball, no co pay."
        ),
    ),
    ("user", "No visit, Nemet."),
    (
        "assistant",
        (
            "Okay. Can you tell me if a deductible applies to in network physical "
            "therapy? And is the service covered for both office and telehealth?"
        ),
    ),
    (
        "user",
        (
            "Good afternoon. -- supply, and the deductible here is three thousand "
            "dollars. Nothing has been met."
        ),
    ),
    (
        "user",
        (
            "That of pocket seven thousand dollars met the amount of forty two dollars "
            "and fifteen cents."
        ),
    ),
    (
        "assistant",
        (
            "Got it. Can you let me know the coinsurance and copay for out of network "
            "physical therapy, and does a deductible apply? Is the service also covered "
            "at both office and telehealth out of network?"
        ),
    ),
    (
        "user",
        (
            "Autumn network with diversions after the deductible, neck opaque. And "
            "deductible for autumn network, nine thousand dollars. Nothing has been met. "
            "The out of pocket of fourteen thousand dollars met a math of forty two "
            "dollars and fifteen cents."
        ),
    ),
    (
        "assistant",
        (
            "Thank you. Is every one of those CPT codes billable for both in network and "
            "out of network, including for telehealth?"
        ),
    ),
    ("user", "Yeah."),
    ("assistant", "Thank you. Is HARBORVIEW"),
    ("user", "Yeah."),
    ("assistant", "Thanks. What is the coverage expiration date for the plan?"),
    ("user", "Let's not termination. It's the laptop."),
    (
        "assistant",
        (
            "Okay, thanks. Is the plan based on a calendar year, a contract year, or a "
            "service year?"
        ),
    ),
    ("user", "Adtrance on plan year. Uncheckedia"),
    (
        "user",
        (
            "at brace at April one twenty twenty six and twelve March thirty one twenty "
            "twenty seven."
        ),
    ),
    ("assistant", "Thank you. Is this plan self-funded or fully funded?"),
    ("user", "Self funded."),
    ("assistant", "Thanks. Is coordination of benefits on file for this member?"),
    ("user", "Yeah. Lowers now member needs to updated."),
    (
        "assistant",
        (
            "Thank you. Are there any other insurances on file for this member? If so, do "
            "you know their names and whether they're primary, secondary, or tertiary?"
        ),
    ),
    ("user", "Hard insurance."),
    (
        "assistant",
        (
            "Okay, thank you. Does the plan cover the member for physical therapy out of "
            "state?"
        ),
    ),
    ("user", "No. Uh, yes."),
    (
        "assistant",
        (
            "Got it. Are there any visit limits for physical therapy, for either in "
            "network or out of network, and for office and telehealth?"
        ),
    ),
    ("user", "Now"),
    ("assistant", "Thank you."),
]

AETNA_VISIT_LIMITS_ANSWER = "No, miss."

# 2026-10-14, after a transfer to a second representative. The provider is out of
# network, and the read-out volunteered a shared sixty-visit maximum with none used.
# After the out-of-state answer the agent asked about other limitations or
# exclusions: the hard or soft maximum, the out-of-pocket questions, the
# medical-necessity review and the out-of-network reimbursement were never asked.
AETNA_OUT_OF_STATE_PATIENT = {
    "member_name": "Owen Harper",
    "member_id": "W630925184",
    "member_dob": "1991-03-03",
}

AETNA_OUT_OF_STATE_CALL = [
    (
        "user",
        (
            "Thank you for calling Aetna's dedicated provider service center. To improve "
            "our service, your call will be monitored and recorded. By continuing with "
            "this call, you understand, accept, and agree that the information "
            "communicated by Aetna is not an offer of payment, does not guarantee "
            "coverage or payment, and is subject to all benefit plan terms and "
            "conditions, including member eligibility at the time of service. Please say "
            "or enter your NPI or tax ID."
        ),
    ),
    (
        "user",
        (
            "Just a moment. Thanks. I found your record. Let's get started. Say claims or "
            "press one coverage and benefits or press two pre certification or press "
            "three contact information or press four appeal inquiries or press five or "
            "join the network or press six."
        ),
    ),
    ("user", "Coverage and benefits. Please say or enter the patient's Aetna ID."),
    ("user", "One moment, please. Now enter or say the patient's date of birth."),
    (
        "user",
        (
            "One moment, please. The patient is... owen harper. Correct? Say yes or press "
            "one. No, two."
        ),
    ),
    ("user", "To receive the information via fax, say have it faxed or press two."),
    ("wait", None),
    (
        "user",
        (
            "Would you like to check benefits using a procedure code? say yes, or press "
            "one, no, two."
        ),
    ),
    (
        "user",
        (
            "The reference number for all the information you'll hear on this call is a v "
            "a one five five five three seven one four two six eight zero. Again, the "
            "reference number is a v a one five five five three seven one four two six "
            "eight zero."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "This patient's coverage is active. They have an Aetna Choice POS two plan, "
            "which is self funded. This plan does not require referrals from the member's "
            "primary care physician. The effective date for the patient's plan is January "
            "first twenty twenty six."
        ),
    ),
    ("wait", None),
    (
        "user",
        ("The patient's original effective date is January first twenty twenty six."),
    ),
    ("wait", None),
    (
        "user",
        (
            "The group number is two five three nine one seven zero one one zero zero "
            "zero zero two."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Benefit details, including deductibles, co pays, and coinsurance are also "
            "available. Do you want benefit details for this patient? Say yes or press "
            "one, no or press two."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Sorry. I didn't hear you. Do you want benefit details? say yes, or press "
            "one, no, or press two. For more information, say help or press star."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Sorry. I still didn't hear you. If you want benefit details, including "
            "deductibles, co pays, and coinsurance for this patient, press one. If not, "
            "press two. For more information, press star. Or to speak with a "
            "representative, press zero."
        ),
    ),
    (
        "user",
        (
            "Are there any other patients I can help you with? Say yes or press one, no, "
            "two."
        ),
    ),
    (
        "user",
        (
            "If your question is about registering for or using the Avelity provider "
            "portal, please contact Avelity client services at one eight hundred two "
            "eight two four five four eight as our customer. Your opinion is important to "
            "us. You may have an opportunity to give us feedback on today's call. Please "
            "stay on the line after the call to answer a short survey. Okay? Please hold "
            "while I transfer your call."
        ),
    ),
    (
        "user",
        (
            "Good afternoon. Thank you for calling Aetna. This is Monica, your customer "
            "service advocate. How may I help you?"
        ),
    ),
    ("assistant", "Hi Monica, I'm calling to check benefits for one"),
    ("user", "Hello. Good morning. Yes."),
    (
        "assistant",
        (
            "I’m looking for physical therapy benefits for in network and out of network, "
            "and I have CPT codes to check. \n\nNine, seven, one, six, one ... nine, seven, "
            "one, one, zero ... nine, seven, one, one, two ... nine, seven, one, four, "
            "zero ... nine, seven, five, three, zero. \n\nOffice"
        ),
    ),
    ("user", "Hold on."),
    ("wait", None),
    ("user", "Sorry for cutting you out."),
    ("user", "for cutting you."),
    (
        "user",
        (
            "Before I check the benefits and the CPT codes, I needed to validate the "
            "provider's information and members' information first."
        ),
    ),
    ("assistant", "Okay, sure. Let me know what you need to confirm."),
    (
        "user",
        ("Okay. Thank you so much. May I know your name and best callback number,"),
    ),
    (
        "assistant",
        (
            "My name is Annie, last initial S as in sierra. The best callback number is "
            "two, zero, two ... five, five, five ... zero, one, four, seven. It's a "
            "direct line, no extension."
        ),
    ),
    (
        "user",
        (
            "Hey. Thank you so much. Can you verify if the the provider's name is Hale "
            "Victor?"
        ),
    ),
    (
        "assistant",
        (
            "The provider name for this call is HARBORVIEW PHYSICAL THERAPY, PC, not Hale "
            "Victor."
        ),
    ),
    (
        "user",
        (
            "Okay? Can you provide me the tax ID number or the NPI? of the individual "
            "provider."
        ),
    ),
    (
        "assistant",
        (
            "I have the facility's tax ID and NPI. The tax ID is eight, three ... five, "
            "zero, two, seven, four, one, nine. The NPI is one, nine, zero ... two, "
            "eight, four ... six, three, seven, five. I don’t have an individual "
            "provider’s NPI for this call."
        ),
    ),
    (
        "user",
        ("Oh, can you repeat that again for me, please, slowly? Thank you so much."),
    ),
    (
        "assistant",
        (
            "Of course. The tax ID is eight, three ... five, zero, two, seven, four, one, "
            "nine.\n\nThe NPI is one, nine, zero ... two, eight, four ... six, three, "
            "seven, five."
        ),
    ),
    ("user", "Hey. Thank you so much."),
    ("assistant", "You're welcome."),
    ("user", "Can you spell out the first three letters of the facility"),
    ("assistant", "Sure. H as in hotel, A as in alpha, R as in romeo."),
    ("user", "Thank you, Annie."),
    ("assistant", "You're welcome."),
    ("user", "Let me just"),
    ("wait", None),
    ("user", "Of course. The"),
    ("wait", None),
    ("user", "May I have the"),
    ("user", "the code of the provider."),
    (
        "assistant",
        (
            "I don’t have a specific provider code for this facility. I have the tax ID "
            "and NPI if you need those."
        ),
    ),
    ("user", "How about the ZIP code of the location of the provider?"),
    (
        "assistant",
        ("The ZIP code for the provider location is one, zero, zero, one, zero."),
    ),
    ("user", "Thank you so much."),
    (
        "user",
        ("And can you repeat the spelling of the first three letters? of the facility"),
    ),
    ("assistant", "Sure. H as in hotel, A as in alpha, R as in romeo."),
    ("user", "Thank you so much."),
    ("assistant", "You're welcome."),
    ("user", "May I know the, uh, full name of the facility,"),
    ("assistant", "The full name of the facility is HARBORVIEW PHYSICAL THERAPY, PC."),
    (
        "user",
        (
            "Okay. Thank you so much. And the address would be four West twenty second "
            "Street, room eleven hundred, New York, New York one hundred ten?"
        ),
    ),
    (
        "assistant",
        (
            "It’s four, W, twenty, second, Street, floor eleven, New York, NY, one, zero, "
            "zero, one, zero. Not room eleven hundred, but floor eleven."
        ),
    ),
    (
        "user",
        (
            "Can I just deny me twenty second Street and room one one zero zero on my "
            "end?"
        ),
    ),
    (
        "assistant",
        (
            "You can note four West twenty second Street and floor eleven for the "
            "address. There isn’t a room eleven hundred; it’s floor"
        ),
    ),
    ("user", "Okay."),
    ("assistant", "Alright."),
    ("user", "Are you calling in behalf of the member Owen Harper?"),
    (
        "assistant",
        (
            "I'm calling on behalf of HARBORVIEW PHYSICAL THERAPY, PC to check benefits "
            "for the member Owen Harper."
        ),
    ),
    ("user", "May I know the date of birth of owen harper"),
    ("assistant", "Owen Harper’s date of birth is March third, nineteen ninety-one."),
    ("user", "Thank you."),
    ("assistant", "You're welcome."),
    (
        "user",
        (
            "Please give me one minute to two just to check if I handle the member's "
            "policy."
        ),
    ),
    ("wait", None),
    ("user", "Hello, Annie."),
    ("assistant", "Hi there."),
    (
        "user",
        (
            "Is it okay if I place this phone call on hold for one to two minutes just to "
            "check if I have the access to the members plan?"
        ),
    ),
    ("assistant", "Yes, that's fine."),
    ("user", "Thank you so much."),
    ("wait", None),
    (
        "user",
        (
            "Hello, Annie. Thank you for patiently waiting. So upon checking here, as "
            "much as I wanted to help you, there's a dedicated department that handles "
            "this"
        ),
    ),
    ("wait", None),
    ("user", "kind of account."),
    ("wait", None),
    (
        "user",
        ("And what I can do for you is transfer you over to the dedicated department."),
    ),
    ("assistant", "That sounds good. Thank you."),
    ("user", "So we're transferring you. Is there anything else?"),
    ("assistant", "No, that’s all. Thank you."),
    (
        "user",
        (
            "And before transferring you, I would like to have a quick recap. So I wasn't "
            "able to provide you any information because there's a dedicated department "
            "for the patients, uh, or the members plan. And I will be putting this phone "
            "call on hold while transferring you. Is that okay?"
        ),
    ),
    ("assistant", "Yes, that’s okay."),
    (
        "user",
        (
            "And just a heads up, there will be a quick survey after the next "
            "representative."
        ),
    ),
    ("assistant", "Thank you for letting me know."),
    ("user", "Azure, please stay on the line while I transfer you."),
    ("wait", None),
    (
        "user",
        (
            "Hi. Good afternoon. Thank you for calling Aetna. This is Priya, your "
            "customer advocate. How can I assist you?"
        ),
    ),
    (
        "assistant",
        (
            "Hi Priya. I’m calling to check physical therapy benefits for one of your "
            "members."
        ),
    ),
    (
        "user",
        (
            "I can definitely help regarding with the benefits, specifically for physical "
            "therapy. But before we proceed, can I ask for your name?"
        ),
    ),
    ("assistant", "My name is Annie, last initial S as in sierra."),
    (
        "user",
        (
            "Thank you so much, Annie. And are you calling for a provider, doctor Victor "
            "Hale?"
        ),
    ),
    (
        "assistant",
        (
            "No, I’m calling for HARBORVIEW PHYSICAL THERAPY, PC. That’s the facility "
            "name."
        ),
    ),
    ("user", "Can can I ask for the NPI of the facility?"),
    (
        "assistant",
        (
            "Sure. The NPI is one, nine, zero ... two, eight, four ... six, three, seven, "
            "five."
        ),
    ),
    ("user", "Thank you so much."),
    ("assistant", "You're welcome."),
    (
        "user",
        ("And for the, um, ZIP code of the provider, is that one zero zero one zero?"),
    ),
    ("assistant", "Yes, the ZIP code is one, zero, zero, one, zero."),
    (
        "user",
        (
            "Thank you so much, Annie. And for the member's validation, are you calling "
            "for member Owen Harper?"
        ),
    ),
    ("assistant", "Yes, I’m calling for member Owen Harper."),
    (
        "user",
        (
            "Wonderful. Let me just go ahead and check first if I do have the full access "
            "to this number's policy. Okay?"
        ),
    ),
    ("wait", None),
    ("user", "Hold on."),
    ("wait", None),
    (
        "user",
        (
            "Right. So as per checking here, I do have the access to this member's "
            "policy. And as for a concern for physical therapy, it would be covered at "
            "ninety percent after deductible no co pay. It has a sixty visits maximum per "
            "year for speech therapy, physical therapy, and also for occupational therapy "
            "combined. And as I check here, out of sixty visits, nothing has been met or "
            "used so far. Payment to provide accumulations?"
        ),
    ),
    (
        "assistant",
        (
            "Thank you for that. Is that benefit for in network or out of network? And "
            "can you also go over the out of network benefits for physical therapy?"
        ),
    ),
    (
        "user",
        (
            "Of course. Um, the one that I forwarded you earlier is for the in network "
            "benefit. And as for out of network, it will be covered at sixty percent "
            "after deductible, no co pay, sixty visits maximum per year due for ST, AT, "
            "and OT."
        ),
    ),
    (
        "assistant",
        (
            "Thank you. Are these benefits covered for office and telehealth visits, and "
            "are all of these CPT codes billable: nine, seven, one, six, one ... nine, "
            "seven, one, one, zero ... nine, seven, one, one, two ... nine, seven, one, "
            "four, zero ... nine, seven, five, three, zero?"
        ),
    ),
    (
        "user",
        (
            "Yeah. It is all about and billable, Annie, and for telehealth, it would be "
            "covered in in network benefit only, but ours forty out of network, it is not "
            "covered."
        ),
    ),
    (
        "assistant",
        (
            "Got it. Is the provider HARBORVIEW PHYSICAL THERAPY, PC in or out of network "
            "for this member’s plan?"
        ),
    ),
    ("user", "One moment."),
    ("wait", None),
    ("user", "Alumnia"),
    ("wait", None),
    (
        "user",
        (
            "I'll check you on my end, Annie, for this one. It is out of network, "
            "actually, to the Bamber Splat. So, basically, for telehealth, it is at "
            "copper."
        ),
    ),
    (
        "assistant",
        (
            "Okay, thank you for checking that. Can you tell me the coverage effective "
            "date for this plan?"
        ),
    ),
    (
        "user",
        (
            "Fact of date would be January first of a thousand twenty six. No future "
            "termination date."
        ),
    ),
    ("assistant", "Thank you. Is this plan based on a calendar"),
    ("user", "You're welcome."),
    ("assistant", "Is this plan based on a calendar"),
    ("user", "As he checked on my... yeah. It is based on a calendar year."),
    ("assistant", "Thank you. Is the plan self-funded or fully funded?"),
    ("user", "You're welcome."),
    ("assistant", "Is coordination"),
    ("user", "It is a self insured, self funded."),
    ("assistant", "Thank you. Is coordination of benefits on file for this member?"),
    (
        "user",
        (
            "Cortisi, o b n. And can you please advise the patient to update it? since "
            "there is no correlation of benefit on file."
        ),
    ),
    (
        "assistant",
        (
            "Got it, thank you. Does the plan cover the member for services out of state?"
        ),
    ),
    ("user", "One moment."),
    ("wait", None),
]

AETNA_OUT_OF_STATE_ANSWER = (
    "As per checking here, yeah, there's no state restrictions as... at all."
)


# 2026-10-14, the same clinic on another member's call, where the interview
# covered every topic before closing: the provider is out of network, the
# representative answered the medical-necessity question and then declined to
# give a reimbursement basis. The agent asked about other limitations or
# exclusions next, which is the turn the closing should still take.
AETNA_ALL_TOPICS_PATIENT = {
    "member_name": "Lena Ortiz",
    "member_id": "W527183946",
    "member_dob": "1979-11-08",
}

AETNA_ALL_TOPICS_CALL = [
    (
        "user",
        (
            "Thank you for calling Aetna's dedicated provider service center. To improve "
            "our service, your call will be monitored and recorded. By continuing with "
            "this call, you understand, accept, and agree that the information "
            "communicated by Aetna is not an offer of payment, does not guarantee "
            "coverage or payment, and is subject to all benefit plan terms and "
            "conditions, including member eligibility at the time of service. Please say "
            "or enter your NPI or tax ID."
        ),
    ),
    (
        "user",
        (
            "Just a moment. Thanks. I found your record. Let's get started. Say claims or "
            "press one coverage and benefits or press two pre certification or press "
            "three contact information or press four appeal inquiries or press five or "
            "join the network or press six."
        ),
    ),
    ("user", "Coverage and benefits. Please say or enter the patient's Aetna ID."),
    ("user", "One moment, please."),
    ("wait", None),
    ("user", "Now, enter or say the patient's date of birth."),
    (
        "user",
        (
            "One moment, please. The patient is LENA ORTIZ. Correct? Say yes or press "
            "one. No, two."
        ),
    ),
    ("user", "To receive the information via fax, say have it faxed or press two."),
    ("wait", None),
    (
        "user",
        (
            "Would you like to check benefits using the procedure code? Say yes or press "
            "one, no, two."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Sorry. I didn't hear you. If you would like to check benefits using a "
            "procedure code, say yes or press one. If not, say no or press two. For more "
            "information, say help or press star."
        ),
    ),
    (
        "user",
        (
            "The reference number for all the information you'll hear on this call is a v "
            "a one five five five four two zero six one eight three. Again, the reference "
            "number is a v a one five five five four two zero six one eight three."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "This patient's coverage is active. They have an open access managed choice "
            "POS plan, which is commercially funded. This plan does not require referrals "
            "from the member's primary care physician. The effective date for the "
            "patient's plan is April first twenty twenty five."
        ),
    ),
    ("wait", None),
    (
        "user",
        ("The patient's original effective date is April first twenty twenty five."),
    ),
    ("wait", None),
    (
        "user",
        (
            "The group number is one six two four eight three zero one four zero zero one "
            "zero two."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Benefit details, including deductibles, co pays, and coinsurance are also "
            "available. Do you want benefit details for this patient? Say yes or press "
            "one, no or press two."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Sorry. I didn't hear you. Do you want benefit details? say yes, or press "
            "one, no, or press two. For more information, say help or press star."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Sorry. I still didn't hear you. If you want benefit details, including "
            "deductibles, co pays, and coinsurance for this patient, press one. If not, "
            "press two. For more information, press star. Or to speak with a "
            "representative, press zero."
        ),
    ),
    (
        "user",
        (
            "Are there any other patients I can help you with? Say yes or press one, no, "
            "two."
        ),
    ),
    (
        "user",
        (
            "If your question is about registering for or using the Avelity provider "
            "portal, please contact Avelity Client Services at one eight hundred two "
            "eight two four five four eight. As our customer, your opinion is important "
            "to us. You may have an opportunity to give us feedback on today's call. "
            "Please stay on the line after the call to answer a short survey. Okay? "
            "Please hold while I transfer your call."
        ),
    ),
    (
        "user",
        (
            "Thank you for holding. Your call will be answered by the next available "
            "representative. Did you know you can log in to Avality to submit secured "
            "document attachments, retrieve your remittance advice, and check claim "
            "status? You can also submit reconsiderations and appeals through our new "
            "streamlined process."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "All of our representatives are currently assisting other customers. Did you "
            "know that we offer free educational webinars making it easier for you to do "
            "business with us regardless of your participation status? Go to Aetna "
            "webinars dot com or log in to a Validety and go to help and training, then "
            "click get trained to get more information and register. Thank you for your "
            "continued patience. A representative will be with you shortly."
        ),
    ),
    ("wait", None),
    (
        "user",
        (
            "Hello. Good morning. Thank you for calling Aetna. My name is Ray. I'm a "
            "customer service advocate for today. How can I assist you?"
        ),
    ),
    (
        "assistant",
        (
            "Hi Ray, this is Annie, last initial S as in sierra. I'm calling to check "
            "benefits for one of your members."
        ),
    ),
    ("user", "Is it for doctor Victor Hale?"),
    ("assistant", "It's for the facility HARBORVIEW PHYSICAL THERAPY, PC."),
    ("user", "Mhmm. And this is for the number LENA ORTIZ?"),
    ("assistant", "Yes, that's correct."),
    (
        "user",
        (
            "Thank you. Let me go and put up the account number here and double check if "
            "you have a nine fifty account."
        ),
    ),
    ("user", "One moment."),
    ("wait", None),
    (
        "user",
        ("Upon checking here, I have an architectural plan. So how can I assist you?"),
    ),
    (
        "assistant",
        (
            "I'm looking for physical therapy benefits for both in network and out of "
            "network. I have CPT codes to check. Would you like me to give you those "
            "codes now?"
        ),
    ),
    ("user", "Yeah."),
    (
        "assistant",
        (
            "Great. The codes are nine, seven, one, six, one ... nine, seven, one, one, "
            "zero ... nine, seven, one, one, two ... nine, seven, one, four, zero ... and "
            "nine, seven, five, three, zero. These would be for office and telehealth "
            "visits. Did you get all those?"
        ),
    ),
    ("user", "Yeah."),
    (
        "assistant",
        (
            "Okay. What are the benefits for physical therapy for both in network and out "
            "of network? Could you let me know the copay,"
        ),
    ),
    ("user", "Let me double check the codes, please."),
    ("assistant", "Sure, take your time. "),
    ("wait", None),
    (
        "user",
        (
            "I can see that the code here is valid and available and can be used under "
            "the member's plan. Four nine seven one six one, it has a one unit for one "
            "hundred eighty days. Okay?"
        ),
    ),
    (
        "assistant",
        (
            "Okay. Could you tell me the benefits for physical therapy for both in "
            "network and out of network? I need the copay, coinsurance, and whether a "
            "deductible applies"
        ),
    ),
    ("user", "Is it"),
    ("wait", None),
    ("user", "Is it done office?"),
    ("assistant", "Yes, it's done in the office and also via telehealth."),
    (
        "user",
        (
            "For this, the benefit for in network is one hundred percent no deductible "
            "after ten dollar co pay for physical and occupational therapy and the visit "
            "limit is based on medical necessity. We allow four modalities per date of "
            "service. Okay?"
        ),
    ),
    (
        "assistant",
        (
            "Got it, thank you. Could you let me know the out of network benefits for "
            "those same codes and places?"
        ),
    ),
    (
        "user",
        (
            "It's fifty percent after deductible for physical and occupational therapy. "
            "Visit limited list of medical necessity. For accumulation for out of "
            "network, um, upon checking here, the deductible is three thousand dollar, "
            "and the out of pocket max is nine thousand. Includes all deductibles and co "
            "pays. For accumulations here, the net amount for the deductible is eight "
            "hundred twelve dollar and thirty cent. And the net amount for the out of "
            "pocket is nine hundred twenty dollar and fifteen cents."
        ),
    ),
    (
        "assistant",
        (
            "Thanks. Is every CPT code I listed—including nine, seven, one, one, zero ... "
            "nine, seven, one, one, two ... nine, seven, one, four, zero ... and nine, "
            "seven, five, three, zero—covered at both in network and out of network, for "
            "office and telehealth?"
        ),
    ),
    ("user", "That is correct. Yes."),
    (
        "assistant",
        (
            "Perfect. Is this provider in network or out of network for LENA ORTIZ's "
            "plan?"
        ),
    ),
    ("user", "out of network."),
    ("assistant", "Thank you. What is the coverage effective date for this member?"),
    (
        "user",
        (
            "The plan here is open access managed choice that started on April first one "
            "twenty five with no termination date. It is a fully funded plan that runs in "
            "the calendar year."
        ),
    ),
    ("assistant", "Okay, got it. Is coordination of benefits on file for this member?"),
    ("user", "For the COV here, no order coverage exist."),
    (
        "user",
        (
            "last updated on June three of twenty twenty four. So please inform November "
            "to update it with our member services. Okay?"
        ),
    ),
    ("assistant", "Thank you. Does this plan cover the member out of state?"),
    ("user", "Let me double check."),
    ("wait", None),
    ("user", "Yes."),
    (
        "assistant",
        (
            "Thanks. Are there any visit limits for physical therapy for in network and "
            "out of network, office and telehealth? If so, what"
        ),
    ),
    ("user", "Based on medical necessity,"),
    (
        "assistant",
        (
            "Is that a hard or soft maximum? And how many visits have been used so far "
            "this year?"
        ),
    ),
    (
        "user",
        (
            "It's a high max, and we cannot see the visit that has been used when it's "
            "based on medical necessity."
        ),
    ),
    (
        "assistant",
        (
            "Understood. Does the out-of-pocket maximum apply to these services for both "
            "in network and out of network, in the office and via telehealth?"
        ),
    ),
    ("user", "Over these were out of network."),
    (
        "assistant",
        (
            "Thank you. For out of network, does the copay count toward the out-of-pocket "
            "maximum?"
        ),
    ),
    ("user", "No."),
    (
        "assistant",
        (
            "Does what is spent through the deductible count toward the out-of-pocket "
            "maximum?"
        ),
    ),
    ("user", "Sorry?"),
    (
        "assistant",
        (
            "Does the amount paid toward the deductible also count toward the "
            "out-of-pocket maximum for out of network?"
        ),
    ),
    ("user", "Yes."),
    (
        "assistant",
        (
            "Thank you. Is a medical-necessity review required for these services, and if "
            "so, at which visit does it begin for out of network, office and telehealth?"
        ),
    ),
    (
        "user",
        (
            "Let me double check. Okay? Would you mind if I put this call on hold for two "
            "to three minutes?"
        ),
    ),
    ("assistant", "That's fine, go ahead."),
    ("user", "Thank you."),
    ("wait", None),
    ("user", "Hello, ma'am."),
    ("assistant", "Hi, I'm here."),
    ("user", "And"),
    ("wait", None),
    ("user", "Your question again, sir?"),
    ("assistant", "Is a medical-necessity review required for physical"),
    ("user", "Medical review"),
    ("user", "No need for medical review."),
    (
        "assistant",
        (
            "Thank you. For out of network, what is the out-of-network reimbursement rate "
            "type for this plan? Is it based on UCR,"
        ),
    ),
]

AETNA_ALL_TOPICS_ANSWER = "We are no longer providing reimbursement rate."


async def replay(under_test, history) -> None:
    """Seed the agent with a conversation exactly as the model would have seen it.

    A message role ("user" for the line, "assistant" for what the agent said)
    becomes a message. Anything else is one of the agent's own tool calls,
    with the empty result every navigator tool returns: `send_dtmf_events`
    takes the keys as a string, the other tools their text or None.
    """
    chat_ctx = ChatContext()
    for n, (kind, payload) in enumerate(history):
        if kind in ("user", "assistant", "system"):
            chat_ctx.add_message(role=kind, content=payload)
            continue
        call_id = f"call_{n}"
        if kind == "send_dtmf_events":
            arguments = json.dumps({"events": list(payload)})
        elif payload is not None:
            arguments = json.dumps({"text": payload})
        else:
            arguments = "{}"
        chat_ctx.insert(
            llm.FunctionCall(call_id=call_id, name=kind, arguments=arguments)
        )
        chat_ctx.insert(
            llm.FunctionCallOutput(
                call_id=call_id, name=kind, output="", is_error=False
            )
        )
    await under_test.update_chat_ctx(chat_ctx)
