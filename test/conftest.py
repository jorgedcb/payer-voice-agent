"""Shared test fixtures.

The models under test come from the agents themselves, so production and the
suite can't drift apart. Override them for a comparison run without editing any
file:

    LK_TEST_MODEL=google/gemma-4-31b-it uv run pytest
    LK_NAVIGATOR_TEST_MODEL=openai/gpt-6-sol uv run pytest test/navigator/
    LK_TEST_MODEL=openai/gpt-6-luna LK_TEST_REASONING=low uv run pytest test/interview/

LK_TEST_REASONING sets the interview model's reasoning effort. With neither
set, the interview's LLM is built exactly as production builds it
(agent.primary_llm); with only LK_TEST_MODEL, that model runs at its provider's
default effort.

The judge is neither: it is pinned (JUDGE_MODEL), so a comparison run changes
the model under test and not the grader.
"""

from os import getenv

import pytest
from livekit.agents import NOT_GIVEN, AgentSession, inference, mock_tools

import agent
import navigator
from dispatch import sample_spec
from interview import InterviewAgent
from navigator import HoldAgent, NavigatorAgent
from prompts import call_date_today

# Two models, two knobs: the interview runs on the session's model, the
# navigator on its own. Each suite runs against what production runs.
TEST_MODEL = getenv("LK_TEST_MODEL")
TEST_REASONING = getenv("LK_TEST_REASONING")
NAVIGATOR_TEST_MODEL = getenv("LK_NAVIGATOR_TEST_MODEL", navigator.NAVIGATOR_LLM)
# Grades judge() intents. Reasoning stays at the provider's default: grading has
# no latency budget, and the reply is judged against the intent, not answered.
JUDGE_MODEL = "openai/gpt-6-sol"


def pytest_addoption(parser):
    parser.addoption(
        "--offline", action="store_true", help="Skip tests that use LiveKit inference"
    )


MODEL_FIXTURES = {
    "judge_llm",
    "llm",
    "navigator_llm",
    "session",
    "start_assistant",
    "start_navigator",
}


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
    """The interview's LLM, as production runs it unless overridden."""
    if request.config.getoption("--offline"):
        pytest.skip("requires LiveKit inference")
    if TEST_MODEL is None and TEST_REASONING is None:
        model = agent.primary_llm()
    else:
        # A comparison run: a named model starts from its provider's default
        # effort, not production's, which was chosen for production's model.
        model = agent.primary_llm(
            model=TEST_MODEL or agent.PRIMARY_LLM,
            reasoning=TEST_REASONING or NOT_GIVEN,
        )
    async with model:
        yield model


@pytest.fixture
async def judge_llm(request):
    """The grader for judge(), the same whatever model is under test."""
    if request.config.getoption("--offline"):
        pytest.skip("requires LiveKit inference")
    async with inference.LLM(model=JUDGE_MODEL) as model:
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
    async with inference.LLM(
        model=NAVIGATOR_TEST_MODEL, extra_kwargs={"parallel_tool_calls": False}
    ) as model:
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

    async def _start(*, phase: str = "menu", **overrides) -> NavigatorAgent | HoldAgent:
        """The menu phase by default; `phase="hold"` starts in the queue, for a
        replay that picks up after the switch."""
        spec = sample_spec(**overrides)
        under_test: NavigatorAgent | HoldAgent = (
            HoldAgent(spec=spec, call_date=call_date_today(), llm_model=navigator_llm)
            if phase == "hold"
            else NavigatorAgent(spec=spec, llm_model=navigator_llm)
        )
        await session.start(under_test)
        return under_test

    return _start
