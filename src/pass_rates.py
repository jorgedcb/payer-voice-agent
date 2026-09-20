"""Per-test pass rates across repeated pytest runs, from their JUnit XML files.

    python src/pass_rates.py reports/run1.xml reports/run2.xml --min 0.9

Prints a Markdown table, worst first, and exits 1 when any test passed in
fewer than --min of the runs where it ran, or when no test ran at all.
Skipped runs do not count either way.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Tally:
    passed: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def runs(self) -> int:
        return self.passed + self.failed

    @property
    def rate(self) -> float:
        return self.passed / self.runs if self.runs else 1.0


def tally(paths: list[Path]) -> dict[str, Tally]:
    tallies: dict[str, Tally] = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            if case.find("skipped") is not None:
                continue
            entry = tallies.setdefault(name, Tally())
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                entry.passed += 1
            else:
                entry.failed += 1
                first_line = ((problem.get("message") or "").splitlines() or [""])[0]
                entry.failures.append(first_line[:120])
    return tallies


def render(tallies: dict[str, Tally], minimum: float) -> tuple[str, bool]:
    rows = sorted(tallies.items(), key=lambda kv: (kv[1].rate, kv[0]))
    lines = ["| Test | Passed | Rate |", "|---|---|---|"]
    below = False
    for name, t in rows:
        flag = ""
        if t.rate < minimum:
            below = True
            flag = " :red_circle:"
        lines.append(f"| `{name}` | {t.passed}/{t.runs} | {t.rate:.0%}{flag} |")
        if t.failures:
            # The first failure's first line, whatever the threshold: an
            # informational section still needs to say what went wrong.
            lines.append(f"| | | {t.failures[0].replace('|', '&#124;')} |")
    if not rows:
        # Nothing collected is a broken selection (marker or config drift), not
        # a clean night: report it as a failure so the run cannot stay green.
        lines.append("| (no tests ran) | | :red_circle: |")
        below = True
    return "\n".join(lines), below


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument(
        "--min", type=float, default=0.9, help="required pass rate, 0-1"
    )
    args = parser.parse_args(argv)
    table, below = render(tally(args.reports), args.min)
    print(table)
    return 1 if below else 0


if __name__ == "__main__":
    sys.exit(main())
