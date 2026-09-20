# Contributing

Open an issue for a bug or proposed change, or submit a focused pull request.
For vulnerabilities, use the private reporting process in [SECURITY.md](SECURITY.md).
Use synthetic data in issues, pull requests, fixtures, and examples; leave out
credentials, patient information, real recordings, and unredacted transcripts.

## Development setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), fork this
repository, and clone your fork. The project requires Python 3.14 or newer;
`uv sync` can provision a compatible interpreter.

From the repository directory, create a branch and install the locked dependencies,
including development tools:

```bash
git switch -c your-change
uv sync --locked
```

The offline checks do not need provider credentials. For live tests or running
the agent, copy `.env.example` to `.env` and configure your own development
credentials. See the [README](README.md#running-it) for runtime requirements.
Never commit `.env` or reuse a production deployment's configuration for tests.

## Checks before opening a pull request

Run the same deterministic checks as CI:

```bash
uv run --no-sync ruff check src/ test/
uv run --no-sync ruff format --check src/ test/
uv run --no-sync deptry .
uv run --no-sync mypy src/
uv run --no-sync pytest test/ --offline -q --tb=short -m "not quarantine"
```

To apply formatting and import sorting before checking:

```bash
uv run --no-sync ruff check --select I --fix src/ test/
uv run --no-sync ruff format src/ test/
```

Live behavior tests require `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and
`LIVEKIT_API_SECRET` and make billable inference requests:

```bash
uv run --no-sync pytest test/ -q --tb=short -m "live and not quarantine"
```

CI skips the live suite when credentials are unavailable, including on fork PRs.
A green `live-tests` check can therefore mean the suite was skipped. State
whether live tests ran when describing validation.

Tests live in `test/interview/`, `test/navigator/`, and `test/call_flow/`, with
shared infrastructure tests at the root of `test/`. Shared fixtures belong in
`test/conftest.py`, and replay helpers in `test/support/`.

For agent or prompt changes, reproduce the behavior in realistic conversation
context, test nearby cases that should still work, and repeat live behavioral
checks to account for model variability. Evaluate meaning, actions, and outcomes
rather than requiring exact wording unless the behavior requires it. Report
remaining failures or uncertainty. Capability probes and quarantined tests are
described in the [testing guide](README.md#testing).

## Pull requests

Target `main`. Explain the problem, what changes for callers or maintainers, and
which checks you ran. Add regression coverage when changing behavior. If a
dependency changes, update both `pyproject.toml` and `uv.lock` as appropriate.

The branch must be up to date with `main`, required CI checks must pass, and
review conversations must be resolved before merging. Keep the change focused
so it can be reviewed independently.
