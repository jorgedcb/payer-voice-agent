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

**Decision:** both the interview and the navigator use GPT-6 Sol with reasoning
effort `none`, with Gemini 3.8 Flash as their provider-error fallback. Fallback
does not evaluate response quality.

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

The navigator's fallback was recorded as passing 9/9 navigator tests on each of
three runs, about two seconds slower per turn; a different provider also reduces
shared outage risk. In November 2026 it passed the 96 navigator and call-flow
tests in one run, and wrote a person's first words in 2.9 to 4.4 s (median; p90
up to 7.8 s), against about 1 s for Sol, so a pickup during an OpenAI outage is
slow.

The navigator moved from GPT-5.6 Luna to GPT-6 Sol in November 2026. On the
navigator's prompt and tools, a menu turn's complete tool call took (median of
20, p90): GPT-5.6 Luna 1,125 ms (1,376), GPT-6 Sol at the provider's default
effort 959 ms (1,174), GPT-6 Luna 875 ms (1,263), all keying the right option
every time. The turn a person picks up on, from their greeting to the completed
handoff with its opening (median of 20, two greetings): GPT-5.6 Luna 1,148 and
1,243 ms, Sol at the default 924 and 974 ms, Sol `none` 1,016 and 953 ms.

The test results are for Sol at the provider's default effort. Sol passed the
navigator tests in three of three runs, and GPT-6 Luna as well; on the final
version, the 96 navigator and call-flow tests passed in two of three runs (one
BlueCard handoff failed once), the Aetna tests 10 of 10, and the two navigator
tests then quarantined (keying a lettered member ID with its pound, denying an
Aetna read-back of someone else) 10 of 10. The difference was the Aetna
assistant's full route, replayed from a 2026-11-20 call: Sol passed it 10 of 10, GPT-6 Luna 8 of 10 and
GPT-5.6 Luna 3 of 10. The failures all spoke the date of birth as "05/14/2018",
the form the prompt showed it in, so the prompt now shows it in words as well.
An earlier version of that test split the transfer announcement into separate
turns, which no production call produced: all 18 Aetna transfers from
2026-11-20 to 2026-11-25 arrived as one turn, heard after the agent had moved
to the queue's shorter end-of-turn wait. On that version both GPT-6 models
handed off on its lone "Okay?".

The navigator then went to effort `none`, to match the interview, without
re-running those tests on it. The two formerly quarantined navigator tests were
measured at `none` later (below); of the rest, only the pickup turn above is.

More reasoning does not stand in for those interview rules. With them taken out
of the prompts, Sol failed the tests behind them at `none`, `minimal` and `low`
alike (10 runs each): a goodbye before the reference went unasked 9 to 10 times,
the diagnosis question after a limit was skipped 7 to 10 times, and a declined
reimbursement basis was asked again 4 to 9 times; with them, every effort passed
8 to 10 of 10. The rules state what the call needs, which no model can infer.
Time to first token on an interview turn (median of 15 interleaved): `none` 785
ms, `minimal` 789 ms, `low` 1,104 ms.

The interview's fallback moved from Gemma to Gemini 3.8 Flash, the navigator's,
when the closing gate was removed (see Benefit topics are settled by the prompt),
since that gate was also what sent Gemma to its topic check. On the seeded
stage-direction context Gemini spoke none in 10 runs, Gemma 2 in 10. On the
early-closing replays both kept the call open 10 of 10, and both closed over the
garbled out-of-pocket answer 10 of 10, with the gate or without it. Gemini is
slower: its first token took 1.46 s (median of 15, p90 2.8 s) against Gemma's
0.68 s, and setting its reasoning effort did not change that. It only answers
while OpenAI's endpoint is failing.

The seven tests quarantined under GPT-4.1 and GPT-5.6 Luna went back into the
PR gate on 2026-11-25. Each passed 5 of 5 in that day's nightly, the first with
the interview on Sol and the navigator still on Luna, and 10 of 10 on main with
both phases on Sol at `none` and no closing gate. The two that had full nights
before were run 30 more times and passed all 30. The full gated suite with all
seven took 6 min 43 s (168 tests), against the nightly's 15-minute and the PR
job's 20-minute limits. What each was quarantined for, and its nightly range
from 2026-11-21 to 2026-11-24:

| Test | Quarantined for | Nightly, of 5 |
|---|---|---|
| `test_menu.py::test_enter_member_id_with_pound_in_one_call` | speaks the ID or keys another letter form; 2 of 16 keyed it (2026-11-17) | 0 to 2 |
| `test_menu.py::test_aetna_readback_of_someone_else_is_denied` | presses a key on a read-back it should deny | 1 to 5 |
| `test_closing.py::test_last_answer_hands_the_closing_to_the_task` | thanks the rep by name without recording it | 3 to 4 |
| `test_closing.py::test_first_name_alone_is_not_given_the_callers_initial` | a rep's name matching the caller's goes unrecorded, 6 of 8 | 2 to 4 (quarantined from 11-23) |
| `test_conversation_repair.py::…[Anita]` | corrects or offers to spell the caller's name unasked | 4 |
| `test_waiting.py::…[hold-permission]` | agrees to hold and calls `wait` in the same turn | 4 to 5 |
| `test_closing.py::…[reference-before-transfer]` | speaks alongside `wait` instead of only waiting | 4 to 5 |

The member-ID test began passing the night the letters rule (Member IDs with
letters) landed, with the navigator still on Luna; the other six the night the
interview moved to Sol.

**Code and coverage:** [`agent.py`](../src/agent.py),
[`navigator.py`](../src/navigator.py),
[`test_stage_directions.py`](../test/interview/test_stage_directions.py),
[`test_closing.py`](../test/interview/test_closing.py),
[`test_waiting.py`](../test/interview/test_waiting.py),
[`test_conversation_repair.py`](../test/interview/test_conversation_repair.py),
and the [`navigator tests`](../test/navigator/), among them
[`test_menu.py`](../test/navigator/test_menu.py). The stage-direction test retains
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

## Benefit topics are settled by the prompt

**Decision:** the interview's prompt lists the benefit topics as numbered headings
and tells the model to check them against the conversation before the closing
question. Nothing in code checks them: `end_call` starts the closing step when the
model calls it, with a reason (`verification_complete` or `call_cannot_continue`)
that is logged and stamped on the report.

**History:** from November 20 to 25, 2026, a `check_topics` tool had the model mark
every topic, with evidence, before closing, and `end_call` refused a finished
verification until a check found nothing open. It was added because GPT-4.1,
with the prompt rule above, asked the closing question with topics never asked
in 5 of 20 runs of the `after-out-of-state` replay, and a 2026-11-19 Aetna call
closed that way in production.

GPT-6 Sol at effort `none` did not close early on any of the four early-closing
replays (`after-visit-limits`, `after-out-of-state`, and the hard-maximum and
visits-used turns of the 2026-11-19 call): 15 of 15 each with the gate, with the
check offered but not enforced, and with no check on the earlier prompt, and
again at `minimal`. On the turn after the last topic, the check took 3.4 s
(median of 15) before the model wrote the question in a second call; without it
the first word came after 0.7 s.

The comparison also showed a flaw in the all-topics replay: it asked about the
medical-necessity review out of network only, on a call that collects both
networks, and took "No need for medical review" as settling both. Without the
check, Sol asked for the in-network review every time; the check marked it
answered. An exchange asking it in network is now added to the replay and marked.
With an answer that covers both networks, both versions asked the closing question
15 of 15, and both closed over the garbled out-of-pocket answer in 2 of 15 runs:
the check had taken the garbled words as the answer.

Without the check and with Gemini 3.8 Flash as the fallback, the live suite passed
161 of 161 in each of three runs.

On the replay as committed, the agent asked the garbled out-of-pocket answer again
in 13 of 13 runs, and never called `end_call` in the same reply as the closing
question (10 of 10; the test now asserts it). Nothing in code holds such an
ending any more, as the gate did once per check.

`verification_complete` is now the model's own judgment that every topic is
settled; the report carries it as given. A reader of the report that treats it
as proof every topic was asked takes that on trust.

**Code and coverage:** [`verification.md`](../src/prompts/verification.md),
[`interview.py`](../src/interview.py),
[`test_closing.py`](../test/interview/test_closing.py), and
[`test_benefit_topics.py`](../test/interview/test_benefit_topics.py).

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
