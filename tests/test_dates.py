"""Date repair: unambiguous slips only, everything else nulled and counted."""

from __future__ import annotations

import pytest

from wantedmt.normalize.dates import repair


class TestRepair:
    @pytest.mark.parametrize(
        "value",
        ["2011-06-01T00:00:00", "2004-01-01T12:30:00", "2026-08-01T13:02:24"],
    )
    def test_plausible_dates_are_left_alone(self, value):
        assert repair(value) == (value, "ok")

    @pytest.mark.parametrize(
        ("broken", "fixed"),
        [
            ("0211-06-01T00:00:00", "2011-06-01T00:00:00"),
            ("0206-03-14T09:00:00", "2006-03-14T09:00:00"),
            ("0224-01-05T00:00:00", "2024-01-05T00:00:00"),
        ],
    )
    def test_transposed_leading_digits_are_repaired(self, broken, fixed):
        """One reading, not a choice: no other swap lands in the register's life."""
        assert repair(broken) == (fixed, "repaired")

    @pytest.mark.parametrize(
        "value",
        [
            "0201-07-02T00:00:00",
            "1900-01-12T00:00:00",
            "0001-01-01T00:00:00",
            "1985-01-01T00:00:00",
        ],
    )
    def test_unrecoverable_dates_are_nulled(self, value):
        assert repair(value) == (None, "implausible")

    def test_a_future_date_is_not_invented_into_the_past(self):
        assert repair("3011-01-01T00:00:00") == (None, "implausible")

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank(self, value):
        assert repair(value) == (None, "empty")

    @pytest.mark.parametrize("value", ["not a date", "2011/06/01", "01.06.2011"])
    def test_other_formats_are_not_guessed_at(self, value):
        """The source is ISO-only; a different shape means something else is wrong, and …"""
        assert repair(value) == (None, "unparsable")

    def test_date_without_a_time_part_survives(self):
        assert repair("2011-06-01") == ("2011-06-01", "ok")
