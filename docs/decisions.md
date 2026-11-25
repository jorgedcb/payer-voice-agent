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

**Decision:** the interview uses GPT-6 Sol with reasoning effort `none`, with Gemma
as its provider-error fallback; the navigator uses GPT-5.6 Luna with Gemini 3.8
Flash as its fallback. Fallback does not evaluate response quality.

**History:** on a seeded stage-direction failure context, Gemma spoke stage
directions in 6 of 10 runs and GPT-4.1 in 0 of 10. This motivated the interview
order. It was a small, context-specific measurement, not a general model ranking.

OpenAI shuts GPT-4.1 down in its API on 2027-01-14, so in November 2026 the
interview moved to GPT-6 Sol. On the interview's own prompt and tools, time to
first token over 12 interleaved requests per setting (median): GPT-4.1 571 ms,
GPT-6 Sol `none` 654 ms, Sol `minimal` 699 ms, GPT-6 Luna `low` 726 ms, Sol at the
provider's default 1,484 ms. GPT-6.2 Sol (841 ms) and GPT-6.1 Sol (about 1.2 s)
were slower at every effort, and Luna at `minimal` or `none` opened every turn
with a tool call instead of speaking. GPT-6.1, GPT-6.2 and the 6.1 and 6.2 Luna
models were not available through LiveKit Inference. Before prompt changes, Sol
`none` failed 6 to 7 of the 79 live interview tests per run and Luna `low` 6 to
9; the failures were rules GPT-4.1 had filled in on its own (asking for a
reference when the representative says goodbye, answering "rehabilitative" for a
non-developmental diagnosis, the diagnosis question after a volunteered visit
limit, a declined reimbursement basis), now written into the prompts. With them,
Sol `none` passed the full live suite in three runs except one failure each in
two of them, and spoke no stage directions in 10 of 10 runs on the seeded
context.

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

The default voice was selected by listening. Calls and the playground did not
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

## Member IDs with letters

**Decision:** the navigator keys member IDs, as it keys every number, and says
the whole ID once a line that asked for the member ID itself shows it needs
the letters: it asks for the ID including its letters, reads the ID back
without them, or finds no member after the keyed digits. A line that asks for
the numbers alone or the numeric portion keeps getting them keyed, and its
read-back of those digits is confirmed. A question about part of the ID gets
only that part. When the ID has letters, THIS CALL also carries it spelled
out, letters as NATO words and digits one at a time, and the rule points
there.

**History:** in November 2026 several member-not-found calls lost an ID's
letters. Cigna's assistant read back the keyed digits and asked for "any
letter at the start"; the agent said yes. Anthem's assistants asked for the ID
again after finding no member; the agent keyed the same digits, or said the ID
without a letter inside it. A Blue plan asked for the ID "including any
letters"; the agent keyed the digits. Replaying those turns with identifiers
swapped, the turns failed 8 of 8 (one Cigna read-back), 5 of 8 (Anthem, inner
letter) and 3 of 8 (Cigna, no match) before the change, and 0 of 36 Cigna
read-backs and 0 of 40 other turns after, both with bare letters and again
with NATO letters. Worded rules alone left the inner letter dropped about one
time in four: the model rebuilt the ID from the prefix it had said and the
digits it had keyed. Copying the spelled-out form fixed that.

The spelled-out form first used bare letters and was switched to NATO words,
as BlueCard's and Anthem's systems ask for them. On real calls bare letters
came back as other letters ("D A X" heard as "b r x"), and an ID that failed
as "X three L" went through as "X-ray three Lima". These are call
observations, not a recognition benchmark.

A letter in `speak`'s identifier example ("W 0 0 1 2") was tried too and
dropped: the results above hold without it, and with it a Blue plan's "In a
few words, how can I help you?" was taken for a person 4 of 8 times, against
0 of 8 without it. For the same reason a dedicated tool that says the member
ID was not added: a new tool changes which tool the model picks on unrelated
turns, and copying the spelled-out line already measured 0 failures. Whether
to say an ID with letters at the first prompt, rather than key it, was left
open: some lines take the digits alone.

**Code and coverage:** [`navigator.md`](../src/prompts/navigator.md),
[`formatting.py`](../src/prompts/formatting.py) (`one_at_a_time`), and
[`test_member_id_letters.py`](../test/navigator/test_member_id_letters.py).

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

## Benefit topics are checked before closing

**Decision:** the closing question comes from `check_topics`, a tool that asks the
model to mark every benefit topic as answered, unavailable (the representative
cannot or will not give it), skipped or open. A mark counts only with its evidence:
the words from the line, or the fact on file or skip condition that applies. The
topics are the `###` headings of the rendered prompt, read again before dialing, so
the tool and the prompt list the same ones. `end_call` with reason
`verification_complete` is refused until a check finds nothing open. An ending in
the same turn as that check is held once and sent to the closing question; only
once, because the model may have asked and heard it before checking.
`call_cannot_continue` (a refusal, a failed transfer) ends without a check, but not
in the same turn a finished verification was refused. The reason is logged with the
topics the last check left open, and stamped on the report's closing.

**History:** with a prompt rule to check the topics against the conversation
before closing, GPT-4.1 still asked about other limitations or exclusions with
topics never asked in 5 of 20 runs of the `after-out-of-state` replay, and a
2026-11-19 Aetna call closed that way in production, leaving the out-of-pocket
questions and the out-of-network reimbursement unasked. Tracking topics in code
after every answer also fixed it, but cost a model call per answer; the check
costs one model step when the model believes it is done, about four seconds for
that turn. Marks without the representative's words still settled unasked
topics in about one run in ten of the `after-visit-limits` replay. Gemma, the
fallback, rarely calls the check on its own; the refused unchecked ending sends it
there, and it reached the reference step in every traced run, though it re-asked an
answered topic in 2 of 4 runs of the last-topic replay.

**Code and coverage:** [`topics.py`](../src/topics.py),
[`interview.py`](../src/interview.py),
[`test_topics.py`](../test/interview/test_topics.py), and
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
