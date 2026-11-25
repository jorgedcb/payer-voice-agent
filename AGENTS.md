# Agent instructions

This project is public. Every file in it is published to
[jorgedcb/payer-voice-agent](https://github.com/jorgedcb/payer-voice-agent), and so is
everything written onto that repository's GitHub pages: code, comments, fixtures, and
issue and pull request text, including every earlier version of an edited description.
Removing something later leaves it in that history, so check text before you commit or
post it.

Nothing published here may identify a real call, the people and businesses on it, or the
team's internal workspace: no internal call or trace IDs, no links to internal tools
(chat threads, task boards, dashboards, admin pages), no names of patients,
representatives, clinics, providers or team members, and no real member IDs, NPIs, tax
IDs, addresses, phone numbers or call references. Describe a call by payer and date ("a
2026-09-22 UnitedHealthcare call"), a person by role ("a teammate who makes these calls
by hand"), and use synthetic values, as [CONTRIBUTING.md](CONTRIBUTING.md) requires.

Setup, checks and testing are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Question what you are given

Requirements, tests, earlier decisions and their measurements are claims, not facts.
Before acting on one, ask what it rests on and check it against real calls, reasoning
from what a call needs rather than from what a test or a document says.

- Question the requirement first: whether it is needed, and whether it is stated
  correctly.
- A test is evidence about the agent only where its input matches what real calls send:
  what the line says, and how speech recognition splits it into turns. Check that before
  changing a prompt or choosing a model because of a result. A navigator test once split
  a transfer announcement into turns no real call produced, and read at face value it
  would have ruled out a model.
- A decision holds for the models, prompts and calls it was measured on. When those
  change, measure it again instead of carrying it forward, or reversing it, on its
  record alone.
