"""end_call does not start the closing while a topic the call needs is open.

Offline: which topics a call needs, how recorded answers settle them, and the
gate on end_call. On real calls the interview asked the closing question with
the medical-necessity review and the topics after it still unasked; the model
alone lost its place on long calls, so the check lives in code.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from livekit.agents import ToolError
from support.interview import settle_every_topic

from call_reference import CallReferenceResult
from dispatch import sample_spec
from interview import InterviewAgent
from topics import required_topics

PLAN_FACTS = [
    "coverage_effective_date",
    "coverage_expiration_date",
    "benefit_year_type",
    "funding",
    "coordination_of_benefits",
    "out_of_state_coverage",
]
COST_SHARE = [
    "out_of_pocket_maximum_applies",
    "copay_counts_toward_out_of_pocket",
    "deductible_counts_toward_out_of_pocket",
]
VISIT_LIMITS = ["visit_limits", "visit_limit_type_and_visits_used"]


def test_a_both_networks_call_needs_every_topic_in_prompt_order() -> None:
    # The sample call: both networks, prior authorization not on file, visit
    # limits and the medical-necessity review asked.
    assert required_topics(sample_spec(), provider_out_of_network=None) == [
        "benefits",
        "provider_network_status",
        *PLAN_FACTS,
        "prior_authorization",
        *VISIT_LIMITS,
        *COST_SHARE,
        "medical_necessity_review",
    ]


def test_out_of_network_reimbursement_is_needed_only_out_of_network() -> None:
    spec = sample_spec()
    assert "out_of_network_reimbursement" not in required_topics(spec, False)
    assert required_topics(spec, True)[-1] == "out_of_network_reimbursement"


def test_topics_the_backend_did_not_ask_for_are_not_needed() -> None:
    spec = sample_spec(ask_mnr=False, ask_visit_limits=False)
    needed = required_topics(spec, provider_out_of_network=False)
    assert "medical_necessity_review" not in needed
    assert not set(VISIT_LIMITS) & set(needed)


@pytest.mark.parametrize(
    ("in_network", "out_network", "needed"),
    [
        (True, False, False),
        (None, False, True),
        (True, None, True),
    ],
)
def test_prior_authorization_on_file_for_both_networks_is_not_asked(
    in_network, out_network, needed
) -> None:
    spec = sample_spec(
        auth_required_in_network=in_network, auth_required_out_network=out_network
    )
    assert ("prior_authorization" in required_topics(spec, None)) is needed


@pytest.mark.parametrize(
    ("out_of_network", "needed"), [(False, False), (True, True), (None, True)]
)
def test_provider_network_scope_asks_authorization_only_for_its_network(
    out_of_network, needed
) -> None:
    # In network is on file; out of network is not.
    spec = sample_spec(
        network_benefit_scope="provider_network",
        auth_required_in_network=False,
        auth_required_out_network=None,
    )
    topics = required_topics(spec, out_of_network)
    assert topics[0] == "provider_network_status"
    assert ("prior_authorization" in topics) is needed


def test_in_network_only_out_of_network_needs_only_the_plan_facts() -> None:
    spec = sample_spec(network_benefit_scope="in_network_only")
    assert required_topics(spec, True) == ["provider_network_status", *PLAN_FACTS]
    in_network = required_topics(spec, False)
    assert "medical_necessity_review" in in_network
    assert "out_of_network_reimbursement" not in in_network


def interview(**overrides) -> InterviewAgent:
    agent = InterviewAgent(spec=sample_spec(**overrides))
    agent._end_call_tool._end_call = AsyncMock(return_value="bye")
    return agent


async def test_end_call_with_topics_open_names_them_and_does_not_close() -> None:
    agent = interview()
    await agent.record_answer("benefits", "80% after deductible, no copay")
    collect = AsyncMock()

    with (
        patch.object(InterviewAgent, "_collect_call_reference", collect),
        pytest.raises(ToolError) as refused,
    ):
        await agent.end_call(MagicMock())

    message = str(refused.value)
    assert "benefits" not in agent.open_topics
    assert all(topic in message for topic in agent.open_topics)
    # In prompt order, so "the first one still open" is the next to ask.
    assert message.index("provider_network_status") < message.index(
        "medical_necessity_review"
    )
    collect.assert_not_awaited()
    agent._end_call_tool._end_call.assert_not_awaited()


async def test_end_call_closes_once_every_topic_is_recorded() -> None:
    agent = interview()
    await settle_every_topic(agent)
    assert agent.open_topics == []

    collect = AsyncMock(return_value=CallReferenceResult(reference="AB73921"))
    with patch.object(InterviewAgent, "_collect_call_reference", collect):
        assert await agent.end_call(MagicMock()) == "bye"

    collect.assert_awaited_once_with(ask_limitations=True)
    assert agent.call_reference == CallReferenceResult(reference="AB73921")


async def test_a_representative_who_cannot_continue_ends_the_call_unasked() -> None:
    # A refusal or a dead end must not be held open for topics nobody will answer,
    # and the closing does not ask them about limitations either.
    agent = interview()
    collect = AsyncMock(return_value=CallReferenceResult(reference=None))
    with patch.object(InterviewAgent, "_collect_call_reference", collect):
        assert (
            await agent.end_call(MagicMock(), representative_cannot_continue=True)
            == "bye"
        )
    collect.assert_awaited_once_with(ask_limitations=False)


async def test_out_of_network_answer_adds_the_reimbursement_topic() -> None:
    agent = interview()
    await settle_every_topic(agent)
    await agent.record_answer("provider_network_status", "Out of network for this plan")
    assert agent.open_topics == ["out_of_network_reimbursement"]

    await agent.record_answer("out_of_network_reimbursement", "UCR, 110% of Medicare")
    assert agent.open_topics == []


async def test_in_network_only_out_of_network_closes_without_the_limitations_question() -> (
    None
):
    agent = interview(network_benefit_scope="in_network_only")
    await agent.record_answer("provider_network_status", "out-of-network")
    for topic in PLAN_FACTS:
        await agent.record_answer(topic, "answered")

    collect = AsyncMock(return_value=CallReferenceResult(reference="AB1"))
    with patch.object(InterviewAgent, "_collect_call_reference", collect):
        await agent.end_call(MagicMock())
    collect.assert_awaited_once_with(ask_limitations=False)


@pytest.mark.parametrize(
    "answer", ["Not sure yet", "in network for office, out of network for home"]
)
async def test_network_status_must_say_in_or_out_of_network(answer: str) -> None:
    agent = interview()
    with pytest.raises(ToolError, match="in network.*out of network"):
        await agent.record_answer("provider_network_status", answer)
    assert "provider_network_status" in agent.open_topics


async def test_an_empty_answer_settles_nothing() -> None:
    agent = interview()
    with pytest.raises(ToolError):
        await agent.record_answer("funding", "  ")
    assert "funding" in agent.open_topics


async def test_a_corrected_network_status_replaces_the_first() -> None:
    agent = interview()
    await settle_every_topic(agent, network="out of network")
    await agent.record_answer("provider_network_status", "in network after all")
    assert agent.open_topics == []


async def test_recording_names_the_next_open_topic() -> None:
    # The model records without speaking, so the result has to carry the turn on:
    # it gets a reply, and the reply knows what is still open.
    agent = interview()
    reply = await agent.record_answer("benefits", "80% after deductible, no copay")
    assert reply is not None and "provider_network_status" in reply
    assert "end_call" not in reply


async def test_recording_the_last_open_topic_points_to_end_call() -> None:
    agent = interview()
    await settle_every_topic(agent)
    await agent.record_answer("provider_network_status", "out of network")
    reply = await agent.record_answer("out_of_network_reimbursement", "not provided")
    assert reply is not None and "end_call" in reply
