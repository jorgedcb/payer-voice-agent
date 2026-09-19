"""Shared test fixtures.

The models under test come from the agents themselves, so production and the
suite can't drift apart. Override them for a comparison run without editing any
file:

    LK_TEST_MODEL=google/gemma-4-31b-it uv run pytest
    LK_NAVIGATOR_TEST_MODEL=openai/gpt-4.1 uv run pytest test/navigator/
"""

from os import getenv

import pytest
from livekit.agents import AgentSession, inference, mock_tools

import agent
from dispatch import sample_spec
import navigator
from interview import InterviewAgent
from navigator import HoldAgent, NavigatorAgent

# Two models, two knobs: the interview runs on the session's model, the
# navigator on its own. Each suite runs against what production runs.
TEST_MODEL = getenv("LK_TEST_MODEL", agent.PRIMARY_LLM)
NAVIGATOR_TEST_MODEL = getenv("LK_NAVIGATOR_TEST_MODEL", navigator.NAVIGATOR_LLM)


def pytest_addoption(parser):
    parser.addoption("--offline", action="store_true", help="Skip tests that use LiveKit inference")


MODEL_FIXTURES = {"llm", "navigator_llm", "session", "start_assistant", "start_navigator"}


def pytest_collection_modifyitems(config, items):
    """Mark model-backed tests `live`, and skip capability probes unless asked.

    `live` is derived, not declared: any test that uses a model fixture calls
    LiveKit inference, and those are the tests CI gates and the nightly
    measures with `-m live`. Declaring it by hand would drift.

    A skip, not a deselect (an `addopts = "-m 'not capability'"` would AND with
    `-k` and with a path, so naming the probe's file collected nothing, and any
    other `-m` on the command line replaced the guard and ran the live probe).
    """
    for item in items:
        if MODEL_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.live)
    if "capability" in config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="capability probe; run with -m capability")
    for item in items:
        if "capability" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
async def llm(request):
    """An LLM for the agent under test, and for judge() where a test uses it."""
    if request.config.getoption("--offline"):
        pytest.skip("requires LiveKit inference")
    async with inference.LLM(model=TEST_MODEL) as model:
        yield model


@pytest.fixture
async def session(llm):
    async with AgentSession(llm=llm) as s:
        yield s


@pytest.fixture
async def start_assistant(session):
    """Start the InterviewAgent directly, as if a human were already on the line.

    Returns the agent so a test can seed chat history on it.
    """

    async def _start(**overrides) -> InterviewAgent:
        under_test = InterviewAgent(spec=sample_spec(**overrides))
        await session.start(under_test)
        return under_test

    return _start


@pytest.fixture
async def navigator_llm(request):
    """One model, not the production fallback pair: a failover inside a test
    would hide which model the prompt was actually measured against."""
    if request.config.getoption("--offline"):
        pytest.skip("requires LiveKit inference")
    async with inference.LLM(model=NAVIGATOR_TEST_MODEL, extra_kwargs={"parallel_tool_calls": False}) as model:
        yield model


def _send_dtmf_events(_ctx, events) -> None:
    # Successful input ends silently, like the production wrapper. The SDK trims
    # a mock's positionals by count, so keep ctx first.
    return None


@pytest.fixture
async def start_navigator(navigator_llm, session):
    """Start the call the way production does: with the NavigatorAgent first."""
    # The model sees LiveKit's real `send_dtmf_events` schema and description;
    # only its execution is intercepted, since there is no room to publish into.
    # Both phases carry the tool, so both need the interception: a call that moves
    # to the queue and is then asked for a value again keys it from there.
    for phase in (NavigatorAgent, HoldAgent):
        mock_tools(phase, {"send_dtmf_events": _send_dtmf_events}, session=session)

    async def _start(**overrides) -> NavigatorAgent:
        under_test = NavigatorAgent(
            spec=sample_spec(**overrides),
            llm_model=navigator_llm,
        )
        await session.start(under_test)
        return under_test

    return _start
