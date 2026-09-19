"""Capability probes: what a model does where we have not decided what is right.

These are NOT behavior tests. A pass says the model made one specific judgment
call; a fail says it made the other. Neither is a bug, and nothing here gates a
merge. They exist so a new model can be scored against the same situations:

    LK_NAVIGATOR_TEST_MODEL=openai/gpt-5.5 uv run pytest -m capability

The main suite skips them (see conftest.py).
"""

import pytest
from support.calls import AETNA_CALL, AETNA_PATIENT, AETNA_READBACK, replay
from support.navigator import hears, presses, spoken

pytestmark = pytest.mark.capability


async def test_confirms_a_garbled_readback_of_its_own_authentication(
    session, start_navigator
) -> None:
    """Does the model recognise its own authentication behind a mis-heard name?

    The situation, from a live Aetna call on 2026-09-06: the agent has just given
    the NPI, the member ID and the date of birth, the menu reads the patient back,
    and STT writes "Richard Amari" for a patient named Rashid Amari. The turn
    asks "Correct?".

    A pass means the model said "yes": it reasoned that the record came from the
    values it just gave, and that the name is a transcription error. On the real
    call the model said "no", the menu could not locate the patient, and the call
    was lost.

    DISCLAIMER: we have not decided that "yes" is the right answer. "Yes" recovers
    the call. "No" is the safe answer if the lookup really did land on another
    patient, which a mis-said digit can cause, and confirming a stranger's record
    is a privacy failure. Until that decision is made, this probe measures the
    capability and does not define the desired behavior. Production is not tuned
    toward either answer.
    """
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)

    result = await hears(session, AETNA_READBACK)

    assert presses(result) == []
    assert spoken(result).lower().strip(".") == "yes"
