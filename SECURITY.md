# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's
[Report a vulnerability](https://github.com/jorgedcb/payer-voice-agent/security/advisories/new)
form. Do not open a public issue or pull request containing vulnerability details.

Include the affected commit or version, a description of the impact, and steps
to reproduce using synthetic data. Share a minimal proof of concept when useful.
Do not include API keys, patient identifiers, real call recordings, or unredacted
transcripts. If a credential was exposed, revoke or rotate it rather than posting
its value in the report.

Use an environment you control for reproduction. Do not place calls to real
patients or insurers, access other people's data, or test production services
without authorization.

## Scope and fixes

This repository contains the voice agent, its prompts, dispatch handling,
recording and reporting code, and deployment examples. Reports about unintended
data disclosure, credential exposure, or unauthorized actions involving this
code are welcome.

Development happens on `main`. Include the commit you tested and whether the
issue reproduces on current `main`. A maintainer will use the private report to
discuss reproduction, a fix, and coordinated disclosure. This project does not
currently publish a response-time or backport commitment.
