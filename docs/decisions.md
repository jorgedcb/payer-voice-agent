# Implementation decisions

This log preserves the history behind the current code. Inline comments explain
the constraints a maintainer needs at the point of use; experiments, earlier
approaches, and measurements belong here.

The observations below were recorded in source comments imported in
[`12edf79`](https://github.com/jorgedcb/payer-voice-agent/commit/12edf79).
They are historical evidence, not results rerun for this cleanup. Test links
identify regression coverage, not proof of current live-call performance.
Names and identifying call details are omitted.

## Telephony noise cancellation

**Decision:** retain `BVCTelephony()` for the SIP call pipeline in
[`agent.py`](../src/agent.py). The selection is based on matching the noise
cancellation model to telephony audio, not a demonstrated improvement in this
agent's turn detection.

**History:** the original comment described plain BVC as the model used during a
phone benchmark in which the turn detector cleared only 50% of turns. It then
described switching to BVCTelephony for narrowband (8 kHz) SIP audio to find out
whether that rate improved. The 50% figure is a BVC baseline only.

**Evidence status (2026-09-20):** no BVC-versus-BVCTelephony comparison result was
found in the repository or its available Git history. BVCTelephony was already
selected in the initial import; the subsequent entrypoint cleanup retained it.
Whether a comparison was measured outside this repository is unknown. Treat
the benefit as **unverified**, not as a successful experiment.

A future comparison should use the same synthetic or approved audio, STT, VAD,
turn detector, and endpointing settings for both models, and record the sample
size, detector-clear rate, end-of-turn delay, and premature interruptions. A
text-only model replay cannot settle an audio-processing comparison.

This choice is specific to the SIP deployment. If WebRTC is added, revisit the
selection by participant kind rather than assuming the same model fits both.

## Model selection

**Decision:** the interview uses GPT-4.1 with Gemma as its provider-error fallback;
the navigator uses GPT-5.6 Luna with Gemini 3.8 Flash as its fallback. Fallback
does not evaluate response quality.

**History:** on a seeded stage-direction failure context, Gemma spoke stage
directions in 6 of 10 runs and GPT-4.1 in 0 of 10. This motivated the interview
order. It was a small, context-specific measurement, not a general model ranking.
The navigator tolerates more model latency while traversing menus; its fallback
was recorded as passing 9/9 navigator tests on each of three runs, about two
seconds slower per turn. A different provider also reduces shared outage risk.

**Code and coverage:** [`agent.py`](../src/agent.py),
[`navigator.py`](../src/navigator.py),
[`test_stage_directions.py`](../test/interview/test_stage_directions.py), and
the [`navigator tests`](../test/navigator/). The stage-direction test retains
its reproduction context; the historical fallback runs have no stored benchmark
artifact in this repository.

## Speech recognition and identity hints

**Decision:** use Deepgram Flux for English conversation, with AssemblyAI as the
fallback, and carry member and caller names in session-owned STT keyterms.
Exclude facility names.

**History:** Flux replaced Nova-3 as a trial. The source comments do not record a
comparative result, so the configured choice is not evidence that Flux won a
benchmark. A misheard member name caused the navigator to reject an identity
read-back, and the caller's name was repeatedly transcribed as other names or
words. These observations motivated the identity hints.

The facility name was also a keyterm until a representative's “Hold on” was
transcribed as “ABA” at confidence 0.57 after the agent mentioned ABA therapy.
Facility names can contain ordinary conversation words, making that bias too
broad. These are recorded call observations, not controlled STT measurements.

**Code:** [`STTContextOptions` in agent.py](../src/agent.py).

## Voice and spoken identifiers

**Decision:** use ElevenLabs Flash (`eleven_flash_v2_5`) with explicit voice
settings and preformatted digit words, pauses, and NATO letters. The direct
plugin is optional at construction; missing credentials leave xAI and Cartesia
available as fallbacks.

**History:** `eleven_v3_conversational` was tried first and dropped. The original
note says its text-to-dialogue path did not hold up on calls, but provides no
failure count or comparison artifact. Flash was selected for streaming latency;
the old comment's approximately 75 ms figure is not an end-to-end measurement
from this repository. The direct plugin replaced the LiveKit Inference route
after ElevenLabs left that route, according to the original implementation note.

Jorge selected the default voice by listening. Calls and the playground did not
sound alike with settings unset: the plugin sent an empty `voice_settings`
object, while the playground used stored voice settings. Explicit settings make
the comparison reproducible. Stability was raised from 0.55 to 0.7 after short
replies sounded excited; the other pinned settings were kept from the voice.

Listening comparisons favored digit words with commas inside 3–3–4 groups and
spaced ellipses between groups for phone numbers and NPIs. Bare mixed-case
letters in a caller-name spelling were hard enough to understand that the
representative asked again. NATO spelling and uppercase letters address that
ambiguity. These were listening observations, not an intelligibility benchmark.

**Code and coverage:** [`voices.py`](../src/voices.py),
[`formatting.py`](../src/prompts/formatting.py),
[`test_tts_chain.py`](../test/test_tts_chain.py),
[`test_formatting.py`](../test/test_formatting.py), and
[`test_numbers.py`](../test/interview/test_numbers.py).

## Turn timing and the human handoff

**Decision:** menus use longer endpointing and no speculative generation; hold
uses the interview's endpointing and enables speculative generation. The
navigator writes the opening in the handoff tool call, and the interview speaks
that opening without another model request. Start the pipeline before dialing
so it can receive the payer's first audio.

**History:** the VAD's 0.55 s default was recorded as producing a 0.577 s wait
even on turns cleared by the audio detector. Its silence window was lowered to
0.25 s, the detector's supported floor in that implementation. The detector's
0.3 s minimum endpointing then governed the floor. Turns below the recorded
0.56 score threshold waited the default 2.5 s maximum, about half of the observed
turns. A 1.5 s maximum left roughly 0.34 s beyond the longest measured genuine
mid-turn pause of 1.164 s. These numbers describe the earlier sample; they do
not establish a safe bound for every conversation.

On an Aetna menu call, continued menu audio invalidated three speculative model
generations. On a human pickup, the menu's 1.5 s wait contributed to a 4.35 s
delay after a representative asked the caller's name. The representative said
“Hello?” over the delayed opening and ended the call. Separating hold timing
and composing the opening during handoff remove avoidable waits; no before/after
latency result was recorded in those comments.

An entry greeting also overlapped the first real turn and caused the handoff
tool to run twice in the test harness. The navigator therefore listens first.

**Code and coverage:** [`agent.py`](../src/agent.py),
[`navigator.py`](../src/navigator.py), [`interview.py`](../src/interview.py),
and the [`call-flow tests`](../test/call_flow/).

## Navigator actions use tools

**Decision:** waiting, speaking, keypad entry, and phase transitions all use
tools. Successful speech and keypad actions finish without a follow-up model
response; errors remain available for recovery. The interview retains ordinary
spoken model output.

**History:** once waiting and keypad entry were tools, GPT-4.1 and Luna answered
speech-only menus with invented keypad digits instead of text. Giving speech
its own tool put every navigator action on the same channel. The original notes
do not give a failure rate. Successful keypad results were later suppressed in
[`e87c14a`](https://github.com/jorgedcb/payer-voice-agent/commit/e87c14a) so the
navigator waits for the IVR's response rather than taking another action.

**Code and coverage:** [`navigator.py`](../src/navigator.py),
[`test_menu.py`](../test/navigator/test_menu.py), and
[`test_keypad.py`](../test/navigator/test_keypad.py).

## Closing is enforced by a task

**Decision:** `end_call` runs `CallReferenceTask` before the SDK hang-up. The task
collects the representative's name and a confirmed reference, or an explicit
statement that none is available. Code references receive preformatted speech;
composed name/date references are read naturally.

**History:** prompt-only reference collection let the interview hang up on a
goodbye about one turn in five, according to the original comments. Asked to
spell a reference itself, the model used bare letters and numerals on every
recorded run despite pacing instructions; no run count was preserved. Supplying
the formatted read-back avoids asking the model to derive pronunciation.

**Code and coverage:** [`call_reference.py`](../src/call_reference.py),
[`interview.py`](../src/interview.py),
[`test_call_reference.py`](../test/interview/test_call_reference.py), and
[`test_closing.py`](../test/interview/test_closing.py).

## Traces are grouped by call ID

**Decision:** stamp `langfuse.session.id` with a span processor in addition to
passing metadata to LiveKit.

**History:** inside a job, LiveKit's metadata processor applied job attributes
and returned without applying the caller's metadata. Langfuse consequently
fell back to `gen_ai.conversation.id` (the room SID), making lookup by the
application's `vc-` call ID fail. The extra processor supplies that ID on every
span while LiveKit still supplies its job attributes.

**Code and coverage:** [`tracing.py`](../src/tracing.py) and
[`test_observability.py`](../test/test_observability.py).
