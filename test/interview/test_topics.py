"""check_topics and the end_call gate, offline.

The live behaviour (the model marking topics and asking the open ones) is in
test_closing.py.
"""

from itertools import product
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from livekit.agents import ToolError

from call_reference import CallReferenceResult
from dispatch import sample_spec
from interview import CLOSING_ASKED, CLOSING_QUESTION, InterviewAgent
from prompts import instructions
from topics import benefit_topics, open_topics

ANSWERED = {"status": "answered", "evidence": "Yes, it applies."}
UNAVAILABLE = {"status": "unavailable", "evidence": "We can't give that out."}
SKIPPED = {"status": "skipped", "evidence": "Prior authorization is on file."}
OPEN = {"status": "open", "evidence": ""}


def keys(**overrides: object) -> list[str]:
    return [t.key for t in InterviewAgent(spec=sample_spec(**overrides)).topics]


def test_topics_follow_what_the_call_asks() -> None:
    assert keys() == [
        "benefits_for_physical_therapy",
        "provider_network_status",
        "coverage_effective_date",
        "coverage_expiration_date",
        "benefit_year_type_and_benefit_period_end_date",
        "funding",
        "coordination_of_benefits",
        "out_of_state_coverage",
        "prior_authorization",
        "visit_limits",
        "visit_limit_type_and_visits_used",
        "out_of_pocket_maximum_applies_to_the_service",
        "copay_counts_toward_the_out_of_pocket_maximum",
        "deductible_counts_toward_the_out_of_pocket_maximum",
        "medical_necessity_review",
        "out_of_network_reimbursement",
    ]
    # The provider's network comes first when only that network is collected.
    assert keys(network_benefit_scope="provider_network")[:2] == [
        "provider_network_status",
        "benefits_for_physical_therapy",
    ]
    # Topics the spec settles or leaves out are not asked about.
    left_out = set(keys()) - set(
        keys(
            auth_required_in_network=True,
            auth_required_out_network=True,
            ask_visit_limits=False,
            ask_mnr=False,
        )
    )
    assert left_out == {
        "prior_authorization",
        "visit_limits",
        "visit_limit_type_and_visits_used",
        "medical_necessity_review",
    }


def test_every_spec_renders_distinct_topics() -> None:
    # The entrypoint reads the topics before dialing; every spec branch must parse,
    # with one topic per heading the template rendered for it.
    services = ["physical_therapy", "speech_therapy", "mental_health"]
    auths = [None, True]
    for scope, service, dx, auth_in, auth_out, limits, mnr in product(
        ["both_networks", "provider_network"],
        services,
        ["", "F84.0"],
        auths,
        auths,
        [True, False],
        [True, False],
    ):
        spec = sample_spec(
            network_benefit_scope=scope,
            service_type=service,
            dx_code=dx,
            auth_required_in_network=auth_in,
            auth_required_out_network=auth_out,
            ask_visit_limits=limits,
            ask_mnr=mnr,
        )
        prompt = instructions("verification", spec)
        assert len(benefit_topics(prompt)) == prompt.count("\n### ")


def test_a_prompt_without_the_topic_section_fails_at_start() -> None:
    with pytest.raises(ValueError):
        benefit_topics("# Role\nNo topics here.")


def test_schema_asks_about_every_topic() -> None:
    agent = InterviewAgent(spec=sample_spec())
    (tool,) = [t for t in agent.tools if t.info.name == "check_topics"]
    parameters = tool.info.raw_schema["parameters"]
    assert parameters["required"] == [t.key for t in agent.topics]
    for prop in parameters["properties"].values():
        assert prop["required"] == ["status", "evidence"]
        assert prop["properties"]["status"]["enum"] == [
            "answered",
            "unavailable",
            "skipped",
            "open",
        ]


@pytest.mark.parametrize("mark", [ANSWERED, UNAVAILABLE, SKIPPED])
def test_a_settled_mark_needs_its_evidence(mark) -> None:
    agent = InterviewAgent(spec=sample_spec())
    (first, *_) = agent.topics
    marks = {t.key: ANSWERED for t in agent.topics}
    marks[first.key] = mark
    assert open_topics(agent.topics, marks) == []
    marks[first.key] = dict(mark, evidence="  ")
    assert open_topics(agent.topics, marks) == [first]


@pytest.mark.parametrize(
    "mark", [OPEN, "answered", {"status": "done", "evidence": "x"}]
)
def test_open_or_malformed_marks_leave_the_topic_open(mark) -> None:
    agent = InterviewAgent(spec=sample_spec())
    (first, *_) = agent.topics
    marks = {t.key: ANSWERED for t in agent.topics}
    marks[first.key] = mark
    assert open_topics(agent.topics, marks) == [first]


def test_open_topics_name_the_first_to_ask() -> None:
    agent = InterviewAgent(spec=sample_spec())
    marks = {t.key: ANSWERED for t in agent.topics}
    marks["out_of_pocket_maximum_applies_to_the_service"] = OPEN
    marks["out_of_network_reimbursement"] = OPEN
    reply = agent.check_topics(marks)
    assert reply.startswith(
        "Not every topic is settled. Ask about Out-of-pocket maximum applies to the service now"
    )
    assert "Out-of-network reimbursement" in reply


@pytest.mark.parametrize(
    "mark", [None, {"evidence": "Plan covers out of state."}, {"status": "done"}]
)
def test_a_check_without_statuses_is_sent_back(mark) -> None:
    # Not read as open: that would send the model to ask topics already answered.
    agent = InterviewAgent(spec=sample_spec())
    marks: dict[str, object] = {t.key: ANSWERED for t in agent.topics}
    if mark is None:
        del marks["funding"]
    else:
        marks["funding"] = mark
    with pytest.raises(ToolError, match="Missing for: funding"):
        agent.check_topics(marks)
    assert agent.check_topics(settled(agent)) == CLOSING_QUESTION


def settled(agent: InterviewAgent) -> dict[str, dict[str, str]]:
    return {t.key: ANSWERED for t in agent.topics}


async def hear(agent: InterviewAgent, text: str) -> None:
    """A representative turn arriving in the agent's conversation."""
    chat_ctx = agent.chat_ctx.copy()
    chat_ctx.add_message(role="user", content=text)
    await agent.update_chat_ctx(chat_ctx)


async def test_the_closing_question_comes_once() -> None:
    agent = InterviewAgent(spec=sample_spec())
    assert agent.check_topics(settled(agent)) == CLOSING_QUESTION
    # Checked again before the representative answered: still the question.
    assert agent.check_topics(settled(agent)) == CLOSING_QUESTION
    await hear(agent, "No, nothing else.")
    assert agent.check_topics(settled(agent)) == CLOSING_ASKED


async def test_reopened_topics_bring_the_question_back() -> None:
    agent = InterviewAgent(spec=sample_spec())
    agent.check_topics(settled(agent))
    await hear(agent, "Actually, the deductible does not count.")
    agent.check_topics(dict(settled(agent), funding=OPEN))
    await hear(agent, "It's self-funded.")
    assert agent.check_topics(settled(agent)) == CLOSING_QUESTION


def _closable(agent: InterviewAgent) -> AsyncMock:
    agent._end_call_tool._end_call = AsyncMock(return_value="bye")
    return agent._end_call_tool._end_call


async def _end(agent: InterviewAgent, reason: str):
    with patch.object(
        InterviewAgent,
        "_collect_call_reference",
        AsyncMock(return_value=CallReferenceResult(reference="AB73921")),
    ):
        return await agent.end_call(MagicMock(), reason=reason)


async def test_a_finished_verification_cannot_end_unchecked() -> None:
    agent = InterviewAgent(spec=sample_spec())
    shutdown = _closable(agent)
    with pytest.raises(ToolError, match="check_topics"):
        await _end(agent, "verification_complete")
    shutdown.assert_not_awaited()
    assert agent.call_reference is None


async def test_a_finished_verification_cannot_end_with_topics_open() -> None:
    agent = InterviewAgent(spec=sample_spec())
    shutdown = _closable(agent)
    agent.check_topics(dict(settled(agent), out_of_network_reimbursement=OPEN))
    await hear(agent, "Anything else?")
    # Sent back to the check, not to a topic that may have been answered since.
    with pytest.raises(ToolError, match="Call check_topics again"):
        await _end(agent, "verification_complete")
    shutdown.assert_not_awaited()


async def test_ending_as_the_check_passes_is_held_once() -> None:
    # A check and end_call in one response, or the closing question and end_call
    # in one turn: the model is sent to the closing question first.
    agent = InterviewAgent(spec=sample_spec())
    shutdown = _closable(agent)
    agent.check_topics(settled(agent))
    with pytest.raises(ToolError, match="other limitations or exclusions"):
        await _end(agent, "verification_complete")
    shutdown.assert_not_awaited()
    # Once only: the question may have been asked and answered before the check.
    assert await _end(agent, "verification_complete") == "bye"
    shutdown.assert_awaited_once()


async def test_ending_after_the_answer_to_the_closing_question() -> None:
    agent = InterviewAgent(spec=sample_spec())
    shutdown = _closable(agent)
    agent.check_topics(settled(agent))
    await hear(agent, "No, that's everything.")
    assert await _end(agent, "verification_complete") == "bye"
    shutdown.assert_awaited_once()


async def test_a_call_that_cannot_continue_ends_without_a_check() -> None:
    # A refusal or a failed transfer must not be held on the benefit topics.
    agent = InterviewAgent(spec=sample_spec())
    shutdown = _closable(agent)
    assert await _end(agent, "call_cannot_continue") == "bye"
    shutdown.assert_awaited_once()


async def test_a_refused_ending_cannot_switch_reason_in_the_same_turn() -> None:
    agent = InterviewAgent(spec=sample_spec())
    shutdown = _closable(agent)
    await hear(agent, "Yes, it covers out of state.")
    with pytest.raises(ToolError):
        await _end(agent, "verification_complete")
    with pytest.raises(ToolError, match="Nothing new was said"):
        await _end(agent, "call_cannot_continue")
    shutdown.assert_not_awaited()

    await hear(agent, "Sorry, I have to disconnect now, we can't continue.")
    assert await _end(agent, "call_cannot_continue") == "bye"
