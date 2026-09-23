"""Spoken formatting of the values the prompts read out."""

import pytest

from prompts.formatting import (
    nato_spell,
    speak_code,
    speak_cpt_codes,
    speak_digits,
    speak_npi,
    speak_phone,
    speak_tax_id,
    us_date,
)


@pytest.mark.parametrize(
    ("raw", "shown"), [("1990-01-23", "01/23/1990"), ("2000-09-16", "09/16/2000")]
)
def test_an_iso_date_is_shown_month_first(raw: str, shown: str) -> None:
    assert us_date(raw) == shown


@pytest.mark.parametrize("raw", ["01/23/1990", "Jan 23 1990", "1990-02-30", ""])
def test_anything_else_is_shown_as_sent(raw: str) -> None:
    assert us_date(raw) == raw


@pytest.mark.parametrize(
    "raw",
    ["+12025550123", "12025550123", "2025550123", "(202) 555-0123", "202-555-0123"],
)
def test_us_phone_numbers_read_in_three_groups(raw: str) -> None:
    # The format Jorge measured as clearest on ElevenLabs Flash; see speak_phone.
    assert (
        speak_phone(raw)
        == "two, zero, two ... five, five, five ... zero, one, two, three"
    )


def test_other_lengths_are_read_as_one_group() -> None:
    assert speak_phone("5550123") == "five, five, five, zero, one, two, three"
    assert speak_phone("+44 20 7946 0958") == (
        "four, four, two, zero, seven, nine, four, six, zero, nine, five, eight"
    )


def test_nato_spell_reads_every_letter_as_a_letter() -> None:
    # On a live call the agent said "A, n, n, i, e" and the representative asked twice.
    assert (
        nato_spell("Annie")
        == "A as in alpha, N as in november, N as in november, I as in india, E as in echo"
    )
    # "as in" is how US reps say it and what speak_code already says; "S for
    # sierra" was one of the phrasings that got the caller asked if it was an AI.
    assert nato_spell("S") == "S as in sierra"


def test_npi_reads_in_three_groups_with_commas() -> None:
    # The format Jorge measured as clearest on ElevenLabs Flash; see speak_npi.
    assert (
        speak_npi("1234567893")
        == "one, two, three ... four, five, six ... seven, eight, nine, three"
    )


def test_npi_of_another_length_is_one_group() -> None:
    assert speak_npi("12345") == "one, two, three, four, five"


def test_tax_id_reads_in_its_written_two_seven_shape() -> None:
    assert (
        speak_tax_id("12-3456789")
        == "one, two ... three, four, five, six, seven, eight, nine"
    )
    assert speak_tax_id("12345") == "one, two, three, four, five"


def test_and_marks_a_trailing_code_only_never_a_trailing_range() -> None:
    # Review finding: with the range last, the old "and" opened the range.
    assert speak_cpt_codes(["840", "97151", "97152", "97153"]) == (
        "eight, four, zero ... nine, seven, one, five, one ... to nine, seven, one, five, three"
    )
    assert speak_cpt_codes(["97151", "97152", "97153", "840"]) == (
        "nine, seven, one, five, one ... to nine, seven, one, five, three ... and eight, four, zero"
    )


def test_only_decimal_digits_are_spoken() -> None:
    # str.isdigit() accepts superscripts that int() rejects; these render at dial time.
    assert speak_digits("9²7") == "nine, seven"
    assert speak_code("F8²4.0") == "F as in foxtrot, eight, four, point, zero"
    assert (
        speak_phone("² 2025550123")
        == "two, zero, two ... five, five, five ... zero, one, two, three"
    )
