"""Helpers for driving the interview agent's state in tests."""

from interview import InterviewAgent


async def settle_every_topic(
    agent: InterviewAgent, network: str = "in network"
) -> None:
    """Record an answer for every topic the call needs, as a finished interview has."""
    await agent.record_answer("provider_network_status", network)
    for topic in list(agent.open_topics):
        await agent.record_answer(topic, "answered")
