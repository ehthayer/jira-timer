"""Tests for pure functions in jira_timer.cli — no Jira/state-file I/O."""
import pytest

from jira_timer.cli import (
    TICKET_ID_REGEX,
    format_duration,
    format_jira_duration,
    is_in_progress,
    parse_duration,
    round_seconds,
)


class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert format_duration(125) == "2m 05s"

    def test_hours_and_minutes(self):
        assert format_duration(3725) == "1h 02m"

    def test_exact_hour(self):
        assert format_duration(3600) == "1h 00m"

    def test_zero(self):
        assert format_duration(0) == "0s"


class TestFormatJiraDuration:
    def test_hours_and_minutes(self):
        assert format_jira_duration(5400) == "1h 30m"

    def test_only_hours(self):
        assert format_jira_duration(7200) == "2h"

    def test_only_minutes(self):
        assert format_jira_duration(900) == "15m"

    def test_sub_minute_minimum(self):
        assert format_jira_duration(30) == "1m"

    def test_zero(self):
        assert format_jira_duration(0) == "1m"


class TestRoundSeconds:
    def test_no_rounding(self):
        assert round_seconds(1234, interval_minutes=0) == 1234

    def test_nearest_rounds_down(self):
        # 7 minutes → nearest 15 → 0
        assert round_seconds(7 * 60, 15, "nearest") == 0

    def test_nearest_rounds_up(self):
        # 8 minutes → nearest 15 → 15
        assert round_seconds(8 * 60, 15, "nearest") == 15 * 60

    def test_nearest_exact_half_rounds_up(self):
        # 7.5 minutes → nearest 15 → 15 (remainder == interval/2)
        assert round_seconds(int(7.5 * 60), 15, "nearest") == 15 * 60

    def test_up_always_rounds_up(self):
        assert round_seconds(60, 15, "up") == 15 * 60
        assert round_seconds(14 * 60, 15, "up") == 15 * 60

    def test_up_exact_interval_unchanged(self):
        assert round_seconds(15 * 60, 15, "up") == 15 * 60

    def test_down_always_rounds_down(self):
        assert round_seconds(14 * 60, 15, "down") == 0
        assert round_seconds(29 * 60, 15, "down") == 15 * 60


class TestParseDuration:
    def test_minutes(self):
        assert parse_duration("30m") == 30 * 60

    def test_hours(self):
        assert parse_duration("2h") == 2 * 3600

    def test_hours_and_minutes(self):
        assert parse_duration("1h30m") == 3600 + 30 * 60

    def test_with_seconds(self):
        assert parse_duration("1h30m45s") == 3600 + 30 * 60 + 45

    def test_empty(self):
        assert parse_duration("") == 0

    def test_junk(self):
        assert parse_duration("xyz") == 0


class TestIsInProgress:
    @pytest.mark.parametrize("status", [
        "In Progress", "in progress", "In Development",
        "Doing", "Working", "Active", "Code Review",
    ])
    def test_recognized_statuses(self, status):
        assert is_in_progress(status) is True

    @pytest.mark.parametrize("status", [
        "To Do", "Done", "Blocked", "Backlog", "Closed", "", "Unknown",
    ])
    def test_unrecognized_statuses(self, status):
        assert is_in_progress(status) is False


class TestTicketIdRegex:
    """Regex check — imported from jira_timer.cli so the test tracks the source."""

    @pytest.mark.parametrize("ticket", ["ENG-123", "DATA-7", "proj2-45", "AB-1"])
    def test_valid(self, ticket):
        assert TICKET_ID_REGEX.match(ticket)

    @pytest.mark.parametrize("ticket", ["123", "ENG", "ENG-", "-123", "1ENG-5", "ENG_123"])
    def test_invalid(self, ticket):
        assert not TICKET_ID_REGEX.match(ticket)
