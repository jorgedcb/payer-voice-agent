"""The nightly pass-rate report: JUnit files in, Markdown and an exit code out."""

from pathlib import Path

from pass_rates import main, render, tally


def junit(path: Path, cases: dict[str, str]) -> Path:
    body = "".join(
        f'<testcase classname="t" name="{name}">'
        + {
            "pass": "",
            "fail": '<failure message="boom: details"/>',
            "fail-pipe": '<failure message="expected str | None, got int"/>',
            "fail-silent": "<failure/>",
            "error": '<error message=""/>',
            "skip": "<skipped/>",
        }[kind]
        + "</testcase>"
        for name, kind in cases.items()
    )
    path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>")
    return path


def test_rates_count_only_runs_where_the_test_ran(tmp_path: Path) -> None:
    runs = [
        junit(tmp_path / "1.xml", {"a": "pass", "b": "fail", "c": "skip"}),
        junit(tmp_path / "2.xml", {"a": "pass", "b": "pass", "c": "pass"}),
    ]
    t = tally(runs)
    assert (t["t::a"].passed, t["t::a"].runs) == (2, 2)
    assert (t["t::b"].passed, t["t::b"].runs) == (1, 2)
    assert (t["t::c"].passed, t["t::c"].runs) == (1, 1)
    assert t["t::b"].failures == ["boom: details"]


def test_worst_first_and_threshold(tmp_path: Path) -> None:
    runs = [
        junit(tmp_path / f"{i}.xml", {"a": "pass", "b": "fail" if i < 2 else "pass"})
        for i in range(5)
    ]
    table, below = render(tally(runs), minimum=0.9)
    assert below
    lines = table.splitlines()
    assert "`t::b`" in lines[2] and "3/5" in lines[2] and ":red_circle:" in lines[2]
    assert "boom" in lines[3]
    assert "`t::a`" in lines[4] and "100%" in lines[4]


def test_exit_code_follows_threshold(tmp_path: Path, capsys) -> None:
    run = junit(tmp_path / "1.xml", {"a": "pass", "b": "fail"})
    assert main([str(run), "--min", "0.9"]) == 1
    assert main([str(run), "--min", "0"]) == 0
    assert "| Test |" in capsys.readouterr().out


def test_no_tests_at_all_is_a_failure(tmp_path: Path) -> None:
    # An empty selection (marker or config drift) must not read as a clean night.
    run = junit(tmp_path / "1.xml", {})
    table, below = render(tally([run]), minimum=0)
    assert below
    assert "no tests ran" in table


def test_failure_without_a_message_still_counts(tmp_path: Path) -> None:
    # pytest writes <error> without a useful message for some setup failures,
    # and a run cut off by the job timeout can leave one empty; the report must
    # still render rather than die on the first such row.
    run = junit(tmp_path / "1.xml", {"a": "fail-silent", "b": "error"})
    t = tally([run])
    assert (t["t::a"].failed, t["t::b"].failed) == (1, 1)
    assert t["t::a"].failures == [""] and t["t::b"].failures == [""]
    table, below = render(t, minimum=0.9)
    assert below and "`t::a`" in table


def test_failure_message_cannot_break_the_table(tmp_path: Path) -> None:
    run = junit(tmp_path / "1.xml", {"a": "fail-pipe"})
    table, _ = render(tally([run]), minimum=0)
    row = next(line for line in table.splitlines() if "expected str" in line)
    assert row.count("|") == 4, row


def test_failure_message_is_shown_even_under_an_informational_floor(
    tmp_path: Path,
) -> None:
    run = junit(tmp_path / "1.xml", {"a": "fail"})
    table, below = render(tally([run]), minimum=0)
    assert not below
    assert "boom: details" in table
