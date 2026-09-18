# Payer voice agent

A voice AI agent that phones a health insurer, gets through the IVR, waits on
hold, and verifies a patient's benefits with a live representative. Built on
[LiveKit Agents](https://docs.livekit.io/agents/) with LiveKit Inference for
the models and ElevenLabs for the voice. It runs in production, placing real
benefit-verification calls for outpatient therapy providers in the US.

## What a call looks like

A call has three phases, and each is its own agent so the prompt, the model, and
the turn-taking settings match what the line is doing at that moment.

1. **Navigator** (`src/navigator.py`, `src/prompts/navigator.md`). A phone menu
   is a recording that pauses between options, so the agent waits well past a
   pause before acting. From the transcript alone it decides to stay quiet, say
   a menu option, key digits over DTMF, or hand off. Nothing second-guesses the
   model; the transcript is the only input.
2. **Hold** (`HoldAgent`, same file). Once the system says it is transferring,
   nothing pauses any more: the next voice is a person finishing a sentence. The
   model declares this transition by calling a tool, rather than the code
   guessing it from keywords, and the wait drops to conversational timing.
3. **Interview** (`src/interview.py`, `src/prompts/verification.md`). Reached
   only by handoff, so it starts mid-conversation with what the representative
   already said. It asks the benefit questions, reads identifiers back the way
   a person writing them down needs to hear them, and closes with a dedicated
   task (`src/call_reference.py`) that will not hang up until the
   representative confirms a call reference or states none exists.

The worker also records room audio to S3 through LiveKit Egress
(`src/recording.py`) and saves the native session report, stamped with when the
payer answered and what the closing confirmed (`src/report.py`). A backend owns
everything after that: reading the report, extracting the benefits, and
deciding what to do with them.

## Design notes worth reading

- **Identifiers are spoken as words with pauses**, not as digit strings
  (`src/prompts/formatting.py`). "1234567893" becomes "one, two, three ...
  four, five, six ... seven, eight, nine, three". Letters are NATO-spelled. The
  groupings were measured against the TTS by listening, not assumed.
- **Handoffs carry context.** The interview does not begin at silence; it
  inherits the navigator's chat history so the representative is never asked
  to repeat themselves.
- **Every model has a fallback**, and so does the voice. LLM, STT and TTS each
  run behind a `FallbackAdapter` (`src/agent.py`).
- **Tracing only ships to an approved destination.** The worker refuses to
  export spans anywhere but the HIPAA region of Langfuse, and reads none of the
  ambient `OTEL_*` variables that could redirect or relabel them.
- **The agent discloses that it is automated** when asked, and never claims to
  be a person or a named employee (`src/prompts/__init__.py`).

## Testing

Model behavior is measured, not asserted once. `test/` has three kinds of test:

- **Offline** tests cover formatting, dispatch parsing, report and recording
  plumbing, and the SDK surface the agents depend on. Deterministic, run on
  every PR with `--offline`.
- **Live** tests replay real call transcripts (with synthetic identifiers) into
  the agents and check what the model does at a specific turn: which menu option
  it speaks, whether it presses fax when a fax is offered, whether it recognizes
  its own authentication behind a mis-heard name. Marked `live` automatically
  by `test/conftest.py` for any test that touches a model. A failed live test is
  rerun once before CI goes red.
- **Capability probes** (`-m capability`) measure a model's judgment at a turn
  where the right answer is undecided. A pass or fail there is a measurement,
  not a bug, and they are skipped unless asked for.

Known-flaky tests are **quarantined** (`-m quarantine`), excluded from the PR
gate, and measured by the nightly workflow, which runs every live suite several
times and prints a pass rate per test (`src/pass_rates.py`). A test that slips
from 100% to 80% shows up there even when each PR run happened to pass.

```bash
uv sync
uv run pytest --offline          # deterministic suite
uv run pytest                    # plus the live suites (needs LiveKit credentials)
LK_TEST_MODEL=openai/gpt-4.1 uv run pytest   # compare a different model
```

## Running it

Copy `.env.example` to `.env` and fill it in. You need a LiveKit Cloud project
with a SIP outbound trunk, an ElevenLabs API key, and an S3-compatible bucket
for reports and audio.

```bash
uv run python src/agent.py console   # talk to the agent locally, no phone call
uv run python src/agent.py dev       # register as a worker against your LiveKit project
```

A call is triggered by an explicit agent dispatch whose metadata is the JSON in
`examples/call_spec.json` (schema in `src/dispatch.py`). The worker dials the
payer over SIP, runs the three phases, and shuts down when the call ends.

Deploy with `lk agent deploy`; the `Dockerfile` is what LiveKit Cloud builds.
`livekit.toml` is intentionally not committed: it names one deployment, and
each operator has their own.

## Configuration

| Variable | Purpose |
| --- | --- |
| `VOICE_CALL_AGENT_NAME` | Name the worker registers under; dispatches target it. |
| `TRACING_SERVICE_NAME` | OTel `service.name` on every span. |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | LiveKit project. |
| `SIP_TRUNK_ID` | Outbound SIP trunk used to dial. |
| `SESSION_REPORT_BUCKET`, `AUDIO_RECORDING_BUCKET`, `S3_*`, `AWS_*` | Where reports and audio land. |
| `ELEVEN_API_KEY` | Primary voice. |
| `LANGFUSE_*` | Optional tracing. Only the HIPAA region is accepted. |

## Status and license

This is the code as it runs in production, published with the company's
permission. The license is being finalized; until a `LICENSE` file lands,
treat the repository as all rights reserved.
